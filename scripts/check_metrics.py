"""指标名核对：部署后的 vLLM、DCGM 实际暴露了哪些指标，和看板、告警依赖的是否一致。

── 为什么要有它 ──────────────────────────────────────────────

vLLM 改过不止一次指标名（比如 KV Cache 占用率从 gpu_cache_usage_perc 改成了 kv_cache_usage_perc）。
名字一变，告警规则的查询结果就是空的：不报错，只是永远不触发，看起来一切正常。
第 2 课部署完 vLLM 后先跑一次这个脚本，有缺失就先改 expected-metrics.txt、看板和告警。

── 怎么执行 ──────────────────────────────────────────────────

    curl -s http://vllm.infer:8000/metrics > results/02-vllm/metrics.txt
    python3 scripts/check_metrics.py results/02-vllm/metrics.txt lessons/03-monitoring/expected-metrics.txt

全部存在退出码 0；有缺失就逐个打印，退出码 1。
"""

from __future__ import annotations

import sys
from pathlib import Path

_HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")


def _sample_names(metrics_text: str) -> set[str]:
    names = set()
    for line in metrics_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cut = len(line)
        for sep in ("{", " "):
            pos = line.find(sep)
            if pos != -1:
                cut = min(cut, pos)
        names.add(line[:cut])
    return names


def missing_metrics(metrics_text: str, expected: list[str]) -> list[str]:
    """返回 expected 中在 metrics_text 里找不到的指标名，保持原顺序。

    直方图只需写基础名：出现 N_bucket、N_sum 或 N_count 任意一个就算 N 存在。
    """
    names = _sample_names(metrics_text)
    missing = []
    for name in expected:
        if name in names or any(name + suffix in names for suffix in _HISTOGRAM_SUFFIXES):
            continue
        missing.append(name)
    return missing


def main(metrics_path: str, expected_path: str) -> int:
    expected = Path(expected_path).read_text(encoding="utf-8").split()
    missing = missing_metrics(Path(metrics_path).read_text(encoding="utf-8"), expected)
    for name in missing:
        print(f"缺少指标：{name}")
    if not missing:
        print(f"✅ {len(expected)} 个指标全部存在")
    return 1 if missing else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("用法：check_metrics.py <metrics.txt> <expected-metrics.txt>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
