import json
import re
from pathlib import Path

MON = Path("lessons/03-monitoring")
dash = json.loads((MON / "dashboards/vllm-overview.json").read_text(encoding="utf-8"))
panels = [p for p in dash["panels"] if p["type"] != "row"]
exprs = [t["expr"] for p in panels for t in p.get("targets", [])]
expected = set((MON / "expected-metrics.txt").read_text(encoding="utf-8").split())


def referenced(texts):
    names = set()
    for t in texts:
        for m in re.findall(r"(vllm:[a-z_]+|DCGM_FI_[A-Z_]+)", t):
            names.add(re.sub(r"_(bucket|sum|count)$", "", m))
    return names


def test_dashboard_identity():
    assert dash["uid"] == "vllm-overview"
    assert len(panels) == 8
    assert all(p["datasource"]["uid"] == "prometheus" for p in panels)


def test_dashboard_covers_key_metrics():
    joined = "\n".join(exprs)
    for name in ["vllm:kv_cache_usage_perc", "vllm:num_requests_waiting", "vllm:time_to_first_token_seconds_bucket",
                 "vllm:inter_token_latency_seconds_bucket", "vllm:generation_tokens_total",
                 "vllm:prefix_cache_hits_total", "DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_FB_USED"]:
        assert name in joined


def test_only_expected_metrics_are_referenced():
    rules = (MON / "rules/vllm-alerts.yaml").read_text(encoding="utf-8")
    assert referenced(exprs + [rules]) <= expected
