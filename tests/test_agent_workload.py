import asyncio
import json
import random

import httpx
import pytest

from scripts.agent_workload import (HIT_METRICS, Session, TurnRecord, build_sessions, hit_rate, is_content_chunk,
                                    make_text, messages_for_turn, percentile, report, run_seed, run_sessions,
                                    summarize)


class SpaceTokenizer:
    """按空格切词的假分词器：一个词一个 token。"""

    def __init__(self):
        self.vocab: dict[str, int] = {}
        self.words: list[str] = []

    def encode(self, text, add_special_tokens=False):
        ids = []
        for w in text.split():
            if w not in self.vocab:
                self.vocab[w] = len(self.words)
                self.words.append(w)
            ids.append(self.vocab[w])
        return ids

    def decode(self, ids):
        return " ".join(self.words[i] for i in ids)


TOK = SpaceTokenizer()
SMALL = dict(prefix_tokens=20, user_tokens=5, assistant_tokens=3)


def test_text_has_exact_token_count():
    assert len(TOK.encode(make_text(TOK, 37, random.Random(0)), add_special_tokens=False)) == 37


def test_shared_sessions_share_prefix_unique_do_not():
    shared = build_sessions(TOK, 3, shared=True, seed=1, **SMALL)
    unique = build_sessions(TOK, 3, shared=False, seed=1, **SMALL)
    assert len({s.prefix for s in shared}) == 1 and len({s.prefix for s in unique}) == 3
    assert shared[0].users != shared[1].users


def test_same_seed_same_sessions():
    assert build_sessions(TOK, 2, shared=True, seed=7, **SMALL) == build_sessions(TOK, 2, shared=True, seed=7, **SMALL)


def test_turn_messages_grow_with_history():
    s = build_sessions(TOK, 1, shared=True, seed=0, **SMALL)[0]
    roles = [m["role"] for m in messages_for_turn(s, 3)]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]


def test_context_over_limit_raises():
    with pytest.raises(ValueError):
        build_sessions(TOK, 1, shared=True, seed=0, prefix_tokens=60, user_tokens=10, assistant_tokens=5, max_context=100)


def test_role_only_chunk_is_not_first_token():
    assert not is_content_chunk({"choices": [{"delta": {"role": "assistant"}}]})
    assert not is_content_chunk({"choices": [{"delta": {"content": ""}}]})
    assert not is_content_chunk({"choices": [], "usage": {"prompt_tokens": 10}})
    assert is_content_chunk({"choices": [{"delta": {"content": "好"}}]})


def test_percentile_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5


def test_failed_turn_stops_session_and_is_counted():
    recs = [TurnRecord(0, 1, True, 100, 900, 4100, 150), TurnRecord(0, 2, False, None, None, None, None),
            TurnRecord(1, 1, True, 120, 950, 4100, 150), TurnRecord(1, 2, True, 80, 800, 4750, 150)]
    rows = {r["turn"]: r for r in summarize(recs)}
    assert rows[2]["failed"] == 1 and rows[2]["ok"] == 1 and rows[2]["ttft_p50_ms"] == 80


def _sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def test_run_stops_failed_session_others_continue():
    sessions = build_sessions(TOK, 2, shared=True, seed=3, turns=3, **SMALL)
    bad_user = sessions[0].users[1]  # 会话 0 的第 2 轮返回 500

    def handler(request):
        body = json.loads(request.content)
        if body["messages"][-1]["content"] == bad_user:
            return httpx.Response(500, text="boom")
        assert body["stream"] is True and body["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(200, text=_sse({"choices": [{"delta": {"role": "assistant"}}]},
                                              {"choices": [{"delta": {"content": "好"}}]},
                                              {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 3}}))

    recs = asyncio.run(run_sessions("http://x", "qwen3-8b", sessions, concurrency=2,
                                    transport=httpx.MockTransport(handler)))
    s0 = sorted((r.turn, r.ok) for r in recs if r.session == 0)
    s1 = [r for r in recs if r.session == 1]
    assert s0 == [(1, True), (2, False)]
    assert len(s1) == 3 and all(r.ok and r.ttft_ms is not None and r.completion_tokens == 3 for r in s1)


def test_hit_rate_from_counter_deltas():
    before = 'vllm:prefix_cache_hits_total{model_name="q"} 100\nvllm:prefix_cache_queries_total{model_name="q"} 1000\n'
    after = 'vllm:prefix_cache_hits_total{model_name="q"} 600\nvllm:prefix_cache_queries_total{model_name="q"} 2000\n'
    assert hit_rate(before, after, *HIT_METRICS["vllm"]) == 0.5
    assert hit_rate(before, before, *HIT_METRICS["vllm"]) is None


def test_report_lists_each_file(tmp_path):
    d = tmp_path / "vllm-shared"
    d.mkdir()
    (d / "c8.json").write_text(json.dumps({
        "args": {"engine": "vllm", "condition": "shared", "concurrency": 8},
        "turns": [{"turn": 1, "ok": 32, "failed": 0, "ttft_p50_ms": 120.0, "ttft_p95_ms": 300.0}],
        "overall": {"ok": 32, "failed": 0, "output_tok_s": 500.0}, "hit_rate": 0.8}))
    md = report([d / "c8.json"])
    assert "vllm" in md and "shared" in md and "| 8 |" in md and "80.0%" in md


def test_run_seed_differs_per_run_but_not_per_engine():
    # 同一条件、同一并发在两个引擎上种子相同；不同并发或条件之间不同，避免上一轮的前缀缓存被下一轮命中
    seeds = {run_seed(0, c, n) for c in ("shared", "unique") for n in (8, 16, 32)}
    assert len(seeds) == 6
    assert run_seed(0, "shared", 8) == run_seed(0, "shared", 8)


class MergingTokenizer(SpaceTokenizer):
    """解码时把末尾两个词粘在一起（BPE 截断再解码常见的情况）：重新编码会少一个 token。"""

    def decode(self, ids):
        words = [self.words[i] for i in ids]
        if len(words) >= 2:
            words[-2:] = [words[-2] + words[-1]]
        return " ".join(words)


def test_text_exact_count_when_decode_merges_tokens():
    tok = MergingTokenizer()
    text = make_text(tok, 30, random.Random(1))
    assert len(tok.encode(text)) == 30


def test_stream_without_content_or_with_error_is_failure():
    sessions = build_sessions(TOK, 2, shared=True, seed=5, turns=1, **SMALL)
    first_user = sessions[0].users[0]

    def handler(request):
        body = json.loads(request.content)
        if body["messages"][-1]["content"] == first_user:
            return httpx.Response(200, text=_sse({"error": {"message": "context too long"}}))
        return httpx.Response(200, text=_sse({"choices": [{"delta": {"role": "assistant"}}]}))

    recs = asyncio.run(run_sessions("http://x", "qwen3-8b", sessions, concurrency=2, transport=httpx.MockTransport(handler)))
    assert [r.ok for r in sorted(recs, key=lambda r: r.session)] == [False, False]
