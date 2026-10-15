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
