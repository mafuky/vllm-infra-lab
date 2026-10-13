"""把压测结果画成曲线：横轴并发，每个实验配置一条线。

    .venv/bin/python scripts/plot.py results/bench results/charts > results/bench/summary.md

生成 throughput.png（输出吞吐）和 ttft_p95.png（首字延迟 P95），同时把汇总表打印到标准输出。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

try:
    from scripts.benchlib import BenchRow, load_rows, to_markdown
except ModuleNotFoundError:  # 直接以 python scripts/plot.py 运行时
    from benchlib import BenchRow, load_rows, to_markdown


def _plot(rows: list[BenchRow], field: str, ylabel: str, out: Path) -> Path:
    by_label: dict[str, list[BenchRow]] = defaultdict(list)
    for r in rows:
        by_label[r.label].append(r)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for label, rs in sorted(by_label.items()):
        rs = sorted(rs, key=lambda r: r.concurrency)
        ax.plot([r.concurrency for r in rs], [getattr(r, field) for r in rs], marker="o", label=label)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("concurrency")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_rows(rows: list[BenchRow], out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        _plot(rows, "output_throughput", "output tokens/s", out_dir / "throughput.png"),
        _plot(rows, "p95_ttft_ms", "TTFT P95 (ms)", out_dir / "ttft_p95.png"),
    ]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("用法：plot.py <bench_dir> <out_dir>")
    rows = load_rows(Path(sys.argv[1]))
    for p in plot_rows(rows, Path(sys.argv[2])):
        print(f"<!-- 已生成 {p} -->", file=sys.stderr)
    print(to_markdown(rows))
