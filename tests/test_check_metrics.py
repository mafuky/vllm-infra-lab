from scripts.check_metrics import missing_metrics

SAMPLE = """# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{le="0.1",model_name="qwen3-8b"} 3
vllm:time_to_first_token_seconds_count{model_name="qwen3-8b"} 3
vllm:generation_tokens_total{model_name="qwen3-8b"} 768
vllm:kv_cache_usage_perc{model_name="qwen3-8b"} 0.12
"""


def test_histogram_base_name_matches_bucket_lines():
    assert missing_metrics(SAMPLE, ["vllm:time_to_first_token_seconds"]) == []


def test_counter_matched_by_total_name():
    assert missing_metrics(SAMPLE, ["vllm:generation_tokens_total"]) == []


def test_reports_renamed_metric():
    assert missing_metrics(SAMPLE, ["vllm:gpu_cache_usage_perc"]) == ["vllm:gpu_cache_usage_perc"]


def test_comment_lines_do_not_count():
    assert missing_metrics("# HELP vllm:num_requests_waiting x\n", ["vllm:num_requests_waiting"]) == ["vllm:num_requests_waiting"]
