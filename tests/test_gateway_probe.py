from scripts.gateway_probe import ProbeResult, backend_from_headers, summarize


def test_backend_from_headers():
    assert backend_from_headers({"x-litellm-model-id": "self-hosted"}) == "self-hosted"
    assert backend_from_headers({}) is None


def test_summarize_counts_errors_and_unknown():
    rs = [ProbeResult(200, "self-hosted", 1.0), ProbeResult(200, "bailian", 2.0),
          ProbeResult(200, None, 1.5), ProbeResult(429, None, 0.1)]
    assert summarize(rs) == {"self-hosted": 1, "bailian": 1, "unknown": 1, "error": 1}


def test_payload_has_bench_like_shape():
    from scripts.gateway_probe import build_payload

    p = build_payload("qwen3-8b", prompt_tokens=1000, max_tokens=256, nonce="n-1")
    content = p["messages"][0]["content"]
    assert p["model"] == "qwen3-8b"
    assert p["max_tokens"] == 256
    assert content.startswith("n-1")
    assert content.count("hello") == 1000


def test_payloads_differ_by_nonce_to_defeat_prefix_cache():
    from scripts.gateway_probe import build_payload

    a = build_payload("qwen3-8b", 1000, 256, nonce="a")["messages"][0]["content"]
    b = build_payload("qwen3-8b", 1000, 256, nonce="b")["messages"][0]["content"]
    assert a[:8] != b[:8]


def test_client_limits_follow_concurrency():
    from scripts.gateway_probe import client_limits

    assert client_limits(200).max_connections == 200


def test_split_at_ready_time():
    from scripts.gateway_probe import ProbeResult, split_at
    rs = [ProbeResult(200, "bailian", 12.0, started_at=100.0), ProbeResult(200, "self-hosted", 3.0, started_at=101.0),
          ProbeResult(200, "self-hosted", 3.0, started_at=250.0)]
    before, after = split_at(rs, 200.0)
    assert before == {"bailian": 1, "self-hosted": 1} and after == {"self-hosted": 1}


def test_started_at_defaults_for_old_callers():
    from scripts.gateway_probe import ProbeResult
    assert ProbeResult(200, "bailian", 1.0).started_at == 0.0
