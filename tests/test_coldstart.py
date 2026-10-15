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


def test_eager_init_line_without_compilation():
    # --enforce-eager 时 init engine 那一行可能没有 (compilation: ...)，不能因此丢掉引擎初始化和 API 就绪两段
    log = ("2026-10-15T07:54:10.500000000Z x Model loading took 15.27 GiB memory and 7.213097 seconds\n"
           "2026-10-15T07:54:20.000000000Z x init engine (profile, create kv cache, warmup model) took 9.50 s\n")
    p = phases(json.loads(POD.read_text()), log_marks(log))
    assert p["引擎初始化"] == pytest.approx(9.5) and p["其中编译"] is None and p["API 就绪"] is not None


def test_clear_compile_cache_happens_after_old_pod_gone(monkeypatch):
    import scripts.coldstart as cs
    calls = []
    monkeypatch.setattr(cs, "_kubectl", lambda *a: calls.append(("kubectl",) + a) or (
        json.dumps({"items": [json.loads(POD.read_text())]}) if a[:1] == ("get",) and "json" in a else ""))
    monkeypatch.setattr(cs, "_wait_no_pods", lambda: calls.append(("wait",)))
    monkeypatch.setattr(cs.subprocess, "run", lambda cmd, **k: calls.append(("run", " ".join(cmd))))
    cs.one_run(drop_caches=False, clear_compile_cache=True)
    order = [c[0] if c[0] != "kubectl" else c[1] + ":" + (c[2] if len(c) > 2 else "") for c in calls]
    rm = next(i for i, c in enumerate(calls) if c[0] == "run" and "/data/cache/vllm" in c[1])
    assert order.index("scale:deploy/vllm") < order.index("wait") < rm
    assert rm < [i for i, c in enumerate(calls) if c[:2] == ("kubectl", "scale")][1]


def test_header_says_restore_with_replace():
    # kubectl patch 不更新 last-applied 注解，事后 kubectl apply 删不掉补丁加的 env、卷和挂载
    doc = Path("scripts/coldstart.py").read_text(encoding="utf-8")
    assert "kubectl replace -f lessons/02-vllm/vllm-qwen3-8b.yaml" in doc
