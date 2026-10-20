"""第 8 课 · 把一次扩容拆成时间线

在 GPU 机器上执行（需要 kubectl）：

    python3 scripts/autoscale_timeline.py record --duration 1800 --interval 5 \\
      --out /data/results/autoscaling/timeline.jsonl
    python3 scripts/autoscale_timeline.py summarize /data/results/autoscaling/timeline.jsonl

record 每隔 interval 秒记一行：Unix 时间、Deployment 的期望副本数和就绪副本数、
KEDA 用来触发的那条查询的当前值（1 分钟平均排队数）。排队数经 kubectl get --raw 走 Service 代理查 Prometheus，
不需要在本机开端口转发。查询失败或还没有数据时记为 null，不中断记录。

summarize 从时间线里找出四个时间点，并算出三段耗时：

  t_cross       1 分钟平均排队数第一次超过阈值 5
  t_scale       此后期望副本数从 1 变成 2（KEDA 改了副本数）
  t_ready       此后就绪副本数从 1 变成 2（新副本能接流量）
  t_scale_down  之后期望副本数回到 1（缩容稳定窗口 300 秒过后）

  scale_delay_s = t_scale - t_cross    扩容的反应时间：HPA 每 15 秒同步一次，且有 10% 容差（要超过 5.5 才扩），
                                       所以这段还包含平均排队数从 5 爬到 5.5 的时间
  ready_delay_s = t_ready - t_scale    新副本的冷启动（对照第 7 课）
  total_s       = t_ready - t_cross    从「扛不住」到「多了一个副本」

只认「从 1 变成 2」的那一刻：记录开始时副本已经是 2，就算没有发生扩容。没越过阈值、没扩容时对应的时间点
和耗时为 null，不会出现负数，汇总照常输出。记录文件已存在时拒绝运行，避免混进上一次实验的数据。
时间都是 Unix 时间戳，t_ready 可以直接交给 gateway_probe.split_at，统计新副本就绪前后各有多少请求转去百炼。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Optional

QUERY = "avg_over_time(sum(vllm:num_requests_waiting)[1m:])"
PROM_PROXY = "/api/v1/namespaces/monitoring/services/prometheus:9090/proxy/api/v1/query"


def _rise(rows: list[dict], key: str, at_least: float, after: Optional[float]) -> Optional[float]:
    """第一次「从小于 at_least 变成不小于 at_least」的时刻，只看 after 之后（含）的行；值未知（None）的行跳过。

    只认「跨过去」的那一刻：记录开始时副本已经是 2，就没有发生扩容，返回 None，而不是把第一行当成扩容时间。
    """
    prev = None
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        if prev is not None and prev < at_least <= v and (after is None or r["t"] >= after):
            return r["t"]
        prev = v
    return None


def _diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None else b - a


def events(rows: list[dict], threshold: float = 5) -> dict:
    rows = sorted(rows, key=lambda r: r["t"])
    t_cross = next((r["t"] for r in rows if r.get("waiting_avg") is not None and r["waiting_avg"] > threshold), None)
    t_scale = _rise(rows, "replicas", 2, after=t_cross)
    t_ready = None if t_scale is None else _rise(rows, "ready", 2, after=t_scale)
    t_down = None if t_ready is None else next((r["t"] for r in rows if r["t"] > t_ready and r.get("replicas") == 1), None)
    return {
        "t_cross": t_cross, "t_scale": t_scale, "t_ready": t_ready, "t_scale_down": t_down,
        "scale_delay_s": _diff(t_cross, t_scale), "ready_delay_s": _diff(t_scale, t_ready), "total_s": _diff(t_cross, t_ready),
    }


def _sample() -> dict:
    """采一行。kubectl 或 Prometheus 任一次失败都记为 None，不中断 30 分钟的记录。"""
    replicas = ready = waiting = None
    try:
        dep = json.loads(subprocess.run(["kubectl", "-n", "infer", "get", "deploy", "vllm", "-o", "json"],
                                        check=True, capture_output=True, text=True, timeout=20).stdout)
        replicas, ready = dep["spec"].get("replicas", 0), dep.get("status", {}).get("readyReplicas") or 0
    except (subprocess.SubprocessError, ValueError, KeyError):
        pass
    try:
        raw = subprocess.run(["kubectl", "get", "--raw", f"{PROM_PROXY}?query={urllib.parse.quote(QUERY)}"],
                             check=True, capture_output=True, text=True, timeout=20).stdout
        result = json.loads(raw)["data"]["result"]
        waiting = float(result[0]["value"][1]) if result else None
    except (subprocess.SubprocessError, ValueError, KeyError, IndexError):
        pass
    return {"t": time.time(), "replicas": replicas, "ready": ready, "waiting_avg": waiting}


def record(out: Path, duration: float, interval: float) -> None:
    if out.exists():
        # 追加到旧文件会把上一次实验的行混进来，汇总出的时间点就不对了
        raise SystemExit(f"{out} 已存在，换一个文件名，或先把旧文件挪走")
    out.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + duration
    with out.open("w", encoding="utf-8") as f:
        while time.time() < end:
            row = _sample()
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"{time.strftime('%H:%M:%S')} 副本 {row['replicas']}（就绪 {row['ready']}） 1 分钟平均排队 {row['waiting_avg']}")
            time.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="KEDA 扩缩容时间线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--duration", type=float, default=1800)
    r.add_argument("--interval", type=float, default=5)
    r.add_argument("--out", required=True)
    s = sub.add_parser("summarize")
    s.add_argument("timeline")
    s.add_argument("--threshold", type=float, default=5)
    args = ap.parse_args()
    if args.cmd == "record":
        record(Path(args.out), args.duration, args.interval)
        return
    rows = [json.loads(line) for line in Path(args.timeline).read_text(encoding="utf-8").splitlines() if line.strip()]
    e = events(rows, args.threshold)
    t0 = rows[0]["t"] if rows else 0
    print(json.dumps(e))
    for k in ("t_cross", "t_scale", "t_ready", "t_scale_down"):
        v = e[k]
        print(f"{k:13} " + ("未发生" if v is None else f"+{v - t0:.0f}s（{time.strftime('%H:%M:%S', time.localtime(v))}）"))
    for k in ("scale_delay_s", "ready_delay_s", "total_s"):
        print(f"{k:13} " + ("-" if e[k] is None else f"{e[k]:.0f}s"))


if __name__ == "__main__":
    main()
