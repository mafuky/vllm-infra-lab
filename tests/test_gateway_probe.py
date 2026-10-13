from scripts.gateway_probe import ProbeResult, backend_from_headers, summarize


def test_backend_from_headers():
    assert backend_from_headers({"x-litellm-model-id": "self-hosted"}) == "self-hosted"
    assert backend_from_headers({}) is None


def test_summarize_counts_errors_and_unknown():
    rs = [ProbeResult(200, "self-hosted", 1.0), ProbeResult(200, "bailian", 2.0),
          ProbeResult(200, None, 1.5), ProbeResult(429, None, 0.1)]
    assert summarize(rs) == {"self-hosted": 1, "bailian": 1, "unknown": 1, "error": 1}
