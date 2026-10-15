from scripts.autoscale_timeline import events

ROWS = [{"t": 0, "replicas": 1, "ready": 1, "waiting_avg": 0.0},
        {"t": 30, "replicas": 1, "ready": 1, "waiting_avg": 6.2},
        {"t": 45, "replicas": 2, "ready": 1, "waiting_avg": 9.0},
        {"t": 140, "replicas": 2, "ready": 2, "waiting_avg": 3.0},
        {"t": 900, "replicas": 1, "ready": 1, "waiting_avg": 0.0}]


def test_events_and_delays():
    e = events(ROWS)
    assert (e["t_cross"], e["t_scale"], e["t_ready"], e["t_scale_down"]) == (30, 45, 140, 900)
    assert (e["scale_delay_s"], e["ready_delay_s"], e["total_s"]) == (15, 95, 110)


def test_no_scale_gives_none():
    e = events([{"t": 0, "replicas": 1, "ready": 1, "waiting_avg": 7.0}])
    assert e["t_cross"] == 0 and e["t_scale"] is None and e["total_s"] is None


def test_missing_waiting_value_is_skipped():
    # Prometheus 查询失败或刚启动没有数据时记为 None，不能当成 0 或报错
    e = events([{"t": 0, "replicas": 1, "ready": 1, "waiting_avg": None},
                {"t": 5, "replicas": 1, "ready": 1, "waiting_avg": 5.5}])
    assert e["t_cross"] == 5


def test_already_two_replicas_before_crossing_never_negative():
    # 记录开始时副本已经是 2（上一步遗留）：没有发生「从 1 扩到 2」，耗时为空而不是负数
    rows = [{"t": 0, "replicas": 2, "ready": 2, "waiting_avg": 0.0},
            {"t": 30, "replicas": 2, "ready": 2, "waiting_avg": 7.0}]
    e = events(rows)
    assert e["t_scale"] is None and e["scale_delay_s"] is None and e["total_s"] is None


def test_unknown_replicas_rows_are_skipped():
    rows = [{"t": 0, "replicas": None, "ready": None, "waiting_avg": 6.0},
            {"t": 5, "replicas": 1, "ready": 1, "waiting_avg": 6.0},
            {"t": 20, "replicas": 2, "ready": 1, "waiting_avg": 7.0},
            {"t": 90, "replicas": 2, "ready": 2, "waiting_avg": 2.0}]
    e = events(rows)
    assert (e["t_cross"], e["t_scale"], e["t_ready"]) == (0, 20, 90)


def test_sample_survives_kubectl_failure(monkeypatch):
    import subprocess

    import scripts.autoscale_timeline as at

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("kubectl", 20)
    monkeypatch.setattr(at.subprocess, "run", boom)
    row = at._sample()
    assert row["replicas"] is None and row["waiting_avg"] is None


def test_record_refuses_existing_file(tmp_path):
    import pytest

    import scripts.autoscale_timeline as at
    out = tmp_path / "timeline.jsonl"
    out.write_text("{}\n")
    with pytest.raises(SystemExit):
        at.record(out, duration=0, interval=1)


def test_header_explains_hpa_sync_and_tolerance():
    from pathlib import Path
    doc = Path("lessons/08-autoscaling/scaledobject.yaml").read_text(encoding="utf-8")
    assert "容差" in doc and "5.5" in doc
