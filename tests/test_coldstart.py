import json
from pathlib import Path

import pytest

from scripts.coldstart import log_marks, median_phases, parse_ts, phases, pod_times

FIX = Path("tests/fixtures/coldstart")
POD = FIX / "pod.json"
LOG = FIX / "vllm.log"
PARTS = ["调度", "容器启动", "进程初始化", "加载权重", "引擎初始化", "API 就绪"]


def test_parses_nanosecond_timestamp():
    t = parse_ts("2026-10-15T07:54:10.123456789Z")
    assert t.microsecond == 123456 and t.utcoffset().total_seconds() == 0
    assert parse_ts("2026-10-15T07:53:58Z").second == 58


def test_pod_times_from_conditions():
    t = pod_times(json.loads(POD.read_text()))
    assert (t["ready"] - t["created"]).total_seconds() == 74


def test_phases_add_up_to_total():
    p = phases(json.loads(POD.read_text()), log_marks(LOG.read_text()))
    assert p["加载权重"] == pytest.approx(7.213, abs=0.01)
    assert p["其中编译"] == pytest.approx(35.13)
    assert sum(p[k] for k in PARTS) == pytest.approx(p["总计"], abs=0.01)
    assert p["总计"] == pytest.approx(74.0, abs=0.01)


def test_missing_mark_gives_none_phase():
    p = phases(json.loads(POD.read_text()), log_marks("no useful lines\n"))
    assert p["加载权重"] is None and p["引擎初始化"] is None
    assert p["总计"] == pytest.approx(74.0, abs=0.01)


def test_median_of_three_runs():
    runs = [{"总计": 70.0, "加载权重": None}, {"总计": 90.0, "加载权重": 7.0}, {"总计": 74.0, "加载权重": 9.0}]
    m = median_phases(runs)
    assert m["总计"] == 74.0 and m["加载权重"] == 8.0
