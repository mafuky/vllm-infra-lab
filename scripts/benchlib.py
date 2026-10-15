"""读取 vllm bench serve 的结果 JSON，汇总成一张表。

目录约定：results/bench/<label>/c<并发>.json，label 是一组实验的名字（baseline、awq、seqs32……）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BenchRow:
    label: str
    concurrency: int
    output_throughput: float
    p50_ttft_ms: float
    p95_ttft_ms: float
    p95_itl_ms: float
    p95_e2el_ms: float
    completed: int
    failed: int


def load_rows(bench_dir: Path) -> list[BenchRow]:
    """读取 bench_dir 下所有 <label>/c<N>.json，按 label、并发排序。"""
    rows = []
    for path in Path(bench_dir).glob("*/c*.json"):
        d = json.loads(path.read_text(encoding="utf-8"))
        rows.append(BenchRow(
            label=path.parent.name,
            concurrency=int(d["max_concurrency"]),
            output_throughput=float(d["output_throughput"]),
            p50_ttft_ms=float(d["p50_ttft_ms"]),
            p95_ttft_ms=float(d["p95_ttft_ms"]),
            p95_itl_ms=float(d["p95_itl_ms"]),
            p95_e2el_ms=float(d["p95_e2el_ms"]),
            completed=int(d["completed"]),
            # 失败数必须单独列出来：丢了请求时吞吐看起来照样正常
            failed=int(d["num_prompts"]) - int(d["completed"]),
        ))
    return sorted(rows, key=lambda r: (r.label, r.concurrency))


_HEADER = "| 配置 | 并发 | 输出吞吐 tok/s | TTFT P50 ms | TTFT P95 ms | ITL P95 ms | E2E P95 ms | 完成 | 失败 |"


def to_markdown(rows: list[BenchRow]) -> str:
    lines = [_HEADER, "|" + " --- |" * 9]
    for r in rows:
        lines.append(f"| {r.label} | {r.concurrency} | {r.output_throughput:.1f} | {r.p50_ttft_ms:.0f} | "
                     f"{r.p95_ttft_ms:.0f} | {r.p95_itl_ms:.1f} | {r.p95_e2el_ms:.0f} | {r.completed} | {r.failed} |")
    return "\n".join(lines)
