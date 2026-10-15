"""第 7 课 · 新副本要多久才能接流量

半天（每种条件 3 次，每次约 1~2 分钟，加上切换补丁的时间）。在 GPU 机器上执行：

    python3 scripts/coldstart.py --label baseline --runs 3 --out /data/results/coldstart/baseline.json
    python3 scripts/coldstart.py --label drop-caches --runs 3 --drop-caches --out /data/results/coldstart/drop-caches.json

── 你在做什么 ────────────────────────────────────────────────

流量突增时，扩出来的新副本要等它「能接请求」才有用。第一周看到 vLLM 从启动到就绪要 1 分多钟，
这一课把这段时间拆开，看时间花在哪，再针对最长的那段做优化。

── 拆成哪 6 段 ──────────────────────────────────────────────

  调度        Pod 创建 → 被调度到节点（K8s 条件 PodScheduled）
  容器启动    调度 → 容器进程启动（镜像已在本地，不含拉取）
  进程初始化  容器启动 → 开始加载权重（Python 导入、CUDA 初始化）
  加载权重    日志 Model loading took ... and <秒> seconds 里的秒数
  引擎初始化  权重加载完 → 日志 init engine ... took（显存测量、分配 KV Cache、编译和 CUDA Graph 捕获）
  API 就绪    引擎初始化完 → Pod 的 Ready 条件（API 服务启动 + 就绪探针的探测间隔）

规格书写的是 5 段，这里把「进程初始化」单列出来，6 段加起来才等于总时长。
「其中编译」取 init engine 那一行括号里的 compilation 秒数，是「引擎初始化」的一部分。

── 为什么先缩到 0 再扩到 1 ────────────────────────────────

直接删 Pod 时，ReplicaSet 会立刻建新 Pod，可旧 Pod 还占着唯一的 GPU，新 Pod 只能 Pending 到旧的退出，
这段等待会被算进「调度」。所以每次都先缩到 0、等旧 Pod 完全消失，再扩到 1，从新 Pod 创建开始计时。

── 三项优化各作用在哪一段 ──────────────────────────────────

  --drop-caches                 清掉页缓存，权重从磁盘冷读：看「加载权重」能慢多少（也就是平时页缓存帮了多少）
  compile-cache.patch.json      把编译缓存挂到宿主机：第二次起「其中编译」应明显变短
  enforce-eager.patch.json      跳过编译和 CUDA Graph：启动更快，但推理变慢，要用压测量出 ITL 的代价

  kubectl -n infer patch deploy vllm --type=json --patch-file lessons/07-coldstart/compile-cache.patch.json
  python3 scripts/coldstart.py --label compile-cache-first --runs 1 --clear-compile-cache --out ...
  python3 scripts/coldstart.py --label compile-cache-warm --runs 3 --out ...

打补丁本身会触发一次重建，那个 Pod 会先把编译缓存写满；所以「缓存为空」的那次要加 --clear-compile-cache，
在旧 Pod 退出后、新 Pod 启动前清空 /data/cache/vllm。

恢复基线必须用 kubectl replace -f lessons/02-vllm/vllm-qwen3-8b.yaml，不能用 apply：
kubectl patch 不会更新 apply 记录的 last-applied 注解，apply 只会还原它管过的字段，
补丁加的环境变量、卷和挂载会一直留着，后面的实验就都在热缓存上跑了（args 是整体替换的，--enforce-eager 能被 apply 还原）。

── 应该看到什么 ──────────────────────────────────────────────

输出 JSON 里 runs 是每次的 6 段和总计，median 是中位数。第一周的参考值：加载权重约 7 秒，引擎初始化约 55 秒，
其中编译约 35 秒。缺了某一行日志（格式变了、容器重启只剩半截日志）时，依赖它的段为空，并打印缺哪一行。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

NS = "infer"
SELECTOR = "app=vllm"
COMPILE_CACHE_DIR = "/data/cache/vllm"
PARTS = ["调度", "容器启动", "进程初始化", "加载权重", "引擎初始化", "API 就绪"]

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$")
_LOAD = re.compile(r"Model loading took .* and ([\d.]+) seconds")
# --enforce-eager 时可能不打印括号里的 compilation，所以那一组可选
_INIT = re.compile(r"init engine .* took ([\d.]+) s(?: \(compilation: ([\d.]+) s\))?")


def parse_ts(s: str) -> datetime:
    """解析 RFC3339，小数部分最多 9 位（kubectl logs --timestamps 是纳秒），截到微秒。"""
    m = _TS.match(s.strip())
    if not m:
        raise ValueError(f"不是 RFC3339 时间：{s!r}")
    base, frac, tz = m.groups()
    frac = (frac or "0")[:6].ljust(6, "0")
    return datetime.fromisoformat(f"{base}.{frac}{'+00:00' if tz == 'Z' else tz}")


def _condition(pod: dict, kind: str) -> Optional[datetime]:
    for c in pod.get("status", {}).get("conditions", []):
        if c.get("type") == kind and c.get("status") == "True":
            return parse_ts(c["lastTransitionTime"])
    return None


def pod_times(pod: dict) -> dict[str, Optional[datetime]]:
    statuses = pod.get("status", {}).get("containerStatuses") or [{}]
    started = statuses[0].get("state", {}).get("running", {}).get("startedAt")
    return {
        "created": parse_ts(pod["metadata"]["creationTimestamp"]),
        "scheduled": _condition(pod, "PodScheduled"),
        "started": parse_ts(started) if started else None,
        "ready": _condition(pod, "Ready"),
    }


def log_marks(log_text: str) -> dict:
    marks: dict = {"load_done": None, "load_seconds": None, "engine_ready": None, "compile_seconds": None}
    for line in log_text.splitlines():
        ts, _, rest = line.partition(" ")
        if m := _LOAD.search(rest):
            marks["load_done"], marks["load_seconds"] = parse_ts(ts), float(m.group(1))
        elif m := _INIT.search(rest):
            marks["engine_ready"] = parse_ts(ts)
            marks["compile_seconds"] = float(m.group(2)) if m.group(2) else None
    return marks


def _secs(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    return None if a is None or b is None else (b - a).total_seconds()


def phases(pod: dict, marks: dict) -> dict[str, Optional[float]]:
    t = pod_times(pod)
    load_done, load_s = marks.get("load_done"), marks.get("load_seconds")
    load_start = None if load_done is None or load_s is None else load_done.timestamp() - load_s
    started = t["started"].timestamp() if t["started"] else None
    return {
        "调度": _secs(t["created"], t["scheduled"]),
        "容器启动": _secs(t["scheduled"], t["started"]),
        "进程初始化": None if load_start is None or started is None else load_start - started,
        "加载权重": load_s,
        "引擎初始化": _secs(load_done, marks.get("engine_ready")),
        "API 就绪": _secs(marks.get("engine_ready"), t["ready"]),
        "总计": _secs(t["created"], t["ready"]),
        "其中编译": marks.get("compile_seconds"),
    }


def median_phases(runs: list[dict]) -> dict[str, Optional[float]]:
    """每一段对有值的那几次取中位数；全都缺就是 None。"""
    keys = list(dict.fromkeys(k for r in runs for k in r))
    out = {}
    for k in keys:
        vals = [r[k] for r in runs if r.get(k) is not None]
        out[k] = statistics.median(vals) if vals else None
    return out


def _kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", "-n", NS, *args], check=True, capture_output=True, text=True).stdout


def _wait_no_pods(timeout: int = 300) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if not _kubectl("get", "pods", "-l", SELECTOR, "-o", "name").strip():
            return
        time.sleep(2)
    raise TimeoutError("旧的 vllm Pod 5 分钟内没有退出")


def one_run(drop_caches: bool, clear_compile_cache: bool = False) -> dict:
    _kubectl("scale", "deploy/vllm", "--replicas=0")
    _wait_no_pods()
    if clear_compile_cache:
        # 必须在旧 Pod 退出之后清：打补丁触发的那次重建已经把缓存写满了
        subprocess.run(["sudo", "sh", "-c", f"rm -rf {COMPILE_CACHE_DIR}/*"], check=True)
    if drop_caches:
        subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)
    _kubectl("scale", "deploy/vllm", "--replicas=1")
    _kubectl("rollout", "status", "deploy/vllm", "--timeout=900s")
    pod = json.loads(_kubectl("get", "pods", "-l", SELECTOR, "-o", "json"))["items"][0]
    log = _kubectl("logs", pod["metadata"]["name"], "--timestamps")
    marks = log_marks(log)
    missing = [k for k in ("load_done", "engine_ready") if marks[k] is None]
    if missing:
        print(f"⚠️  日志里缺少标记：{missing}（Model loading took / init engine ... took 两行）")
    return {"pod": pod["metadata"]["name"], "phases": phases(pod, marks), "log": log}


def main() -> None:
    ap = argparse.ArgumentParser(description="vLLM 冷启动分段计时")
    ap.add_argument("--label", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--drop-caches", action="store_true", help="每次启动前清页缓存（需要 sudo）")
    ap.add_argument("--clear-compile-cache", action="store_true", help=f"每次启动前清空 {COMPILE_CACHE_DIR}（需要 sudo）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    runs = []
    for i in range(args.runs):
        r = one_run(args.drop_caches, args.clear_compile_cache)
        runs.append(r)
        print(f"第 {i + 1} 次：" + "，".join(f"{k} {v:.1f}s" if v is not None else f"{k} -" for k, v in r["phases"].items()))
    out = {"label": args.label, "drop_caches": args.drop_caches, "clear_compile_cache": args.clear_compile_cache, "runs": runs,
           "median": median_phases([r["phases"] for r in runs])}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("中位数：" + json.dumps(out["median"], ensure_ascii=False))


if __name__ == "__main__":
    main()
