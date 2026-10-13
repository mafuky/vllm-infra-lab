from pathlib import Path

from scripts.benchlib import load_rows, to_markdown

FIX = Path("tests/fixtures/bench")


def test_rows_grouped_by_label_and_sorted():
    rows = load_rows(FIX)
    assert [(r.label, r.concurrency) for r in rows] == [("awq", 16), ("baseline", 1), ("baseline", 16)]


def test_failed_requests_are_reported():
    row = next(r for r in load_rows(FIX) if r.label == "baseline" and r.concurrency == 16)
    assert row.failed == 2
    assert "| 2 |" in to_markdown([row])


def test_markdown_has_header():
    assert to_markdown(load_rows(FIX)).splitlines()[0].startswith("| 配置 | 并发 |")
