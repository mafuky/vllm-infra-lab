from pathlib import Path

from scripts.benchlib import load_rows
from scripts.plot import plot_rows

FIX = Path("tests/fixtures/bench")


def test_plot_writes_two_non_empty_pngs(tmp_path):
    paths = plot_rows(load_rows(FIX), tmp_path)
    assert sorted(p.name for p in paths) == ["throughput.png", "ttft_p95.png"]
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)
