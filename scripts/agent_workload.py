"""第 6 课 · Agent 式多轮负载：长系统提示词 + 越聊越长的上下文

半天（两个引擎 × 两组条件 × 三档并发，每轮约 3~8 分钟）。

── 你在做什么 ────────────────────────────────────────────────

聊天负载是「短输入、长输出」，Agent 正好反过来：每次请求都带一大段系统提示词和工具定义（所有会话共用），
每一轮把上一轮的工具结果追加进上下文，输入越来越长，输出很短。这种负载以 prefill 为主，
首字延迟取决于「重复的那部分有没有被缓存住」。vLLM 用按块哈希的前缀缓存，SGLang 用基数树（RadixAttention），
这一课用同一组请求看两者在 Agent 负载下的实际差别。

── 负载模型 ──────────────────────────────────────────────────

每个会话 6 轮：系统提示词 4,000 token（模拟工具定义），每轮用户消息 500 token（模拟工具返回），
每轮输出 150 token。第 6 轮的上下文约 7,700 token，不超过 8,192。同时进行的会话数分 8、16、32 三档，
每档会话总数是并发的 4 倍。两组条件：
  shared   所有会话共用同一段系统提示词：跨会话 + 会话内都能复用
  unique   每个会话的系统提示词都不同：只剩会话内逐轮复用
两组之差就是「跨会话共享前缀」的收益。

── 为什么历史里的助手回答用固定文本 ──────────────────────────

真实的 Agent 会把模型上一轮的输出放进下一轮的上下文。但两个引擎的输出不会逐字相同，
那样它们收到的请求序列就不一样了，对比不公平。所以历史里的助手回答用同一个种子生成的固定文本，
两个引擎收到逐字节相同的请求；模型本轮真实生成的 150 token 只用来计时，不进入下一轮。

种子按「条件 + 并发」派生（run_seed）：两个引擎在同一条件、同一并发下拿到相同序列，
而不同轮次之间的系统提示词不同，上一轮留在缓存里的前缀不会被下一轮白白命中（第一周 bench.sh 踩过同类的坑）。

── 怎么执行（在 bench-client Pod 里） ─────────────────────────

  kubectl -n infer exec deploy/bench-client -- python3 /lab/scripts/agent_workload.py run \\
    --base-url http://sglang.infer:8000 --engine sglang --condition shared --concurrency 8 \\
    --out /results/agent/sglang-shared/c8.json

  Mac 上汇总：.venv/bin/python scripts/agent_workload.py report results/agent

── 应该看到什么 ──────────────────────────────────────────────

每轮一行 TTFT P50/P95。第 1 轮要算完整个系统提示词，最慢；之后各轮只需算新增的 500 token，
命中缓存时 TTFT 明显下降。shared 组从第 1 轮起就能命中别的会话留下的系统提示词，unique 组不能。
命中率取引擎自己的计数器，运行前后各抓一次 /metrics 求差值。
TTFT 从第一个带内容的流式块算起：OpenAI 兼容接口的第一个块通常只有 role，从那里算会把 TTFT 算小。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import httpx

# 历史上下文里每条消息的聊天模板开销（角色标记等）按 10 个 token 估算
MESSAGE_OVERHEAD_TOKENS = 10

# 命中率用的「命中 token 数、查询 token 数」计数器；sglang 一项第 9 天按实际 /metrics 核对
HIT_METRICS = {
    "vllm": ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_queries_total"),
    "sglang": ("sglang:cached_tokens_total", "sglang:prompt_tokens_total"),
}

_WORDS = ("调用 工具 返回 结果 检索 文档 字段 订单 用户 状态 时间 金额 数据 报告 分析 结论 风险 指标 查询 记录 "
          "agent tool call result search query table column value status error retry plan step observe "
          "context memory schema json field latency budget invoice report summary").split()


@dataclass
class Session:
    prefix: str
    users: list[str]
    assistants: list[str]


@dataclass
class TurnRecord:
    session: int
    turn: int
    ok: bool
    ttft_ms: Optional[float]
    e2e_ms: Optional[float]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]


def _count(tokenizer, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False))


def make_text(tokenizer, n_tokens: int, rng: random.Random) -> str:
    """生成恰好 n_tokens 个 token 的文本。

    随机取词、编码后截断再解码。BPE 截断处的 token 解码后可能和相邻字符粘在一起，重新编码会多或少几个，
    所以在 n 附近试几个截断点；都对不上就整段重新抽词（只在末尾追加词不会改变前 n 个 token，会原地打转）。
    """
    for _ in range(50):
        words: list[str] = []
        while _count(tokenizer, " ".join(words)) < n_tokens + 20:
            words.extend(rng.choice(_WORDS) for _ in range(max(1, n_tokens // 4)))
        ids = tokenizer.encode(" ".join(words), add_special_tokens=False)
        for k in sorted(range(max(1, n_tokens - 20), min(len(ids), n_tokens + 20) + 1), key=lambda k: abs(k - n_tokens)):
            text = tokenizer.decode(ids[:k])
            if _count(tokenizer, text) == n_tokens:
                return text
    raise RuntimeError(f"50 次都没能生成恰好 {n_tokens} 个 token 的文本")


def run_seed(seed: int, condition: str, concurrency: int) -> int:
    return zlib.crc32(f"{seed}:{condition}:{concurrency}".encode())


def messages_for_turn(session: Session, turn: int) -> list[dict]:
    msgs = [{"role": "system", "content": session.prefix}]
    for i in range(turn - 1):
        msgs.append({"role": "user", "content": session.users[i]})
        msgs.append({"role": "assistant", "content": session.assistants[i]})
    msgs.append({"role": "user", "content": session.users[turn - 1]})
    return msgs


def build_sessions(tokenizer, n_sessions: int, shared: bool, seed: int, turns: int = 6, prefix_tokens: int = 4000,
                   user_tokens: int = 500, assistant_tokens: int = 150, max_context: int = 8192) -> list[Session]:
    rng = random.Random(seed)
    common = make_text(tokenizer, prefix_tokens, rng) if shared else None
    sessions = []
    for _ in range(n_sessions):
        prefix = common if shared else make_text(tokenizer, prefix_tokens, rng)
        users = [make_text(tokenizer, user_tokens, rng) for _ in range(turns)]
        assistants = [make_text(tokenizer, assistant_tokens, rng) for _ in range(turns - 1)]
        session = Session(prefix, users, assistants)
        last = messages_for_turn(session, turns)
        context = sum(_count(tokenizer, m["content"]) for m in last) + MESSAGE_OVERHEAD_TOKENS * len(last)
        if context > max_context:
            raise ValueError(f"第 {turns} 轮上下文约 {context} token，超过上限 {max_context}")
        sessions.append(session)
    return sessions


def is_content_chunk(chunk: dict) -> bool:
    choices = chunk.get("choices") or []
    return bool(choices) and bool((choices[0].get("delta") or {}).get("content"))


def percentile(values: list[float], q: float) -> float:
    """最近秩法：排序后取第 ceil(q/100·n) 个。"""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def summarize(records: list[TurnRecord]) -> list[dict]:
    rows = []
    for turn in sorted({r.turn for r in records}):
        recs = [r for r in records if r.turn == turn]
        ttfts = [r.ttft_ms for r in recs if r.ok and r.ttft_ms is not None]
        rows.append({
            "turn": turn,
            "ok": sum(r.ok for r in recs),
            "failed": sum(not r.ok for r in recs),
            "ttft_p50_ms": percentile(ttfts, 50) if ttfts else None,
            "ttft_p95_ms": percentile(ttfts, 95) if ttfts else None,
        })
    return rows


def metric_sum(metrics_text: str, name: str) -> float:
    total = 0.0
    for line in metrics_text.splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            total += float(line.rsplit(" ", 1)[1])
    return total


def hit_rate(before: str, after: str, hits: str, queries: str) -> Optional[float]:
    dq = metric_sum(after, queries) - metric_sum(before, queries)
    if dq <= 0:
        return None
    return (metric_sum(after, hits) - metric_sum(before, hits)) / dq


async def _one_turn(client: httpx.AsyncClient, model: str, session_idx: int, turn: int, messages: list[dict],
                    max_tokens: int) -> TurnRecord:
    payload = {
        "model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True},
        "max_tokens": max_tokens, "temperature": 0, "ignore_eos": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    failed = TurnRecord(session_idx, turn, False, None, None, None, None)
    start = time.perf_counter()
    ttft = None
    usage: dict = {}
    try:
        async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                await resp.aread()
                return failed
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if ttft is None and is_content_chunk(chunk):
                    ttft = (time.perf_counter() - start) * 1000
                if chunk.get("usage"):
                    usage = chunk["usage"]
    except (httpx.HTTPError, json.JSONDecodeError):
        return failed
    return TurnRecord(session_idx, turn, True, ttft, (time.perf_counter() - start) * 1000,
                      usage.get("prompt_tokens"), usage.get("completion_tokens"))


async def run_sessions(base_url: str, model: str, sessions: list[Session], concurrency: int, max_tokens: int = 150,
                       timeout: float = 300, transport: Optional[httpx.AsyncBaseTransport] = None) -> list[TurnRecord]:
    """同时最多 concurrency 个会话；会话内各轮串行，某一轮失败就结束该会话。"""
    records: list[TurnRecord] = []
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits, transport=transport) as client:
        async def one_session(idx: int, session: Session) -> None:
            async with sem:
                for turn in range(1, len(session.users) + 1):
                    rec = await _one_turn(client, model, idx, turn, messages_for_turn(session, turn), max_tokens)
                    records.append(rec)
                    if not rec.ok:
                        return

        await asyncio.gather(*(one_session(i, s) for i, s in enumerate(sessions)))
    return records


def _ms(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.0f}"


def report(paths: list[Path]) -> str:
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    runs.sort(key=lambda r: (r["args"]["engine"], r["args"]["condition"], r["args"]["concurrency"]))
    lines = ["| 引擎 | 条件 | 并发 | 轮次 | TTFT P50 ms | TTFT P95 ms | 成功 | 失败 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in runs:
        a = r["args"]
        for t in r["turns"]:
            lines.append(f"| {a['engine']} | {a['condition']} | {a['concurrency']} | {t['turn']} | {_ms(t['ttft_p50_ms'])} "
                         f"| {_ms(t['ttft_p95_ms'])} | {t['ok']} | {t['failed']} |")
    lines += ["", "| 引擎 | 条件 | 并发 | 前缀缓存命中率 | 输出 tok/s | 失败请求 |", "| --- | --- | --- | --- | --- | --- |"]
    for r in runs:
        a, o = r["args"], r["overall"]
        hr = "-" if r.get("hit_rate") is None else f"{r['hit_rate'] * 100:.1f}%"
        lines.append(f"| {a['engine']} | {a['condition']} | {a['concurrency']} | {hr} | {o['output_tok_s']:.0f} | {o['failed']} |")
    return "\n".join(lines) + "\n"


def _cmd_run(args: argparse.Namespace) -> None:
    from transformers import AutoTokenizer  # 只在 GPU 机器的 bench-client 里用到

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    n_sessions = args.sessions or 4 * args.concurrency
    sessions = build_sessions(tokenizer, n_sessions, shared=args.condition == "shared",
                              seed=run_seed(args.seed, args.condition, args.concurrency))
    metrics_url = args.base_url.rstrip("/") + "/metrics"
    before = httpx.get(metrics_url, timeout=30).text
    start = time.time()
    records = asyncio.run(run_sessions(args.base_url, args.model, sessions, args.concurrency))
    wall = time.time() - start
    after = httpx.get(metrics_url, timeout=30).text
    ok = [r for r in records if r.ok]
    out = {
        "args": {"engine": args.engine, "condition": args.condition, "concurrency": args.concurrency,
                 "sessions": n_sessions, "seed": args.seed, "base_url": args.base_url},
        "turns": summarize(records),
        "overall": {"ok": len(ok), "failed": len(records) - len(ok), "wall_s": wall,
                    "output_tok_s": sum(r.completion_tokens or 0 for r in ok) / wall},
        "hit_rate": hit_rate(before, after, *HIT_METRICS[args.engine]),
        "records": [asdict(r) for r in records],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("turns", "overall", "hit_rate")}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent 式多轮负载")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base-url", required=True)
    r.add_argument("--model", default="qwen3-8b")
    r.add_argument("--engine", choices=sorted(HIT_METRICS), required=True)
    r.add_argument("--condition", choices=["shared", "unique"], required=True)
    r.add_argument("--concurrency", type=int, required=True)
    r.add_argument("--sessions", type=int, default=0, help="默认 4 × 并发")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--tokenizer", default="/models/Qwen3-8B")
    r.add_argument("--out", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("dir")
    args = ap.parse_args()
    if args.cmd == "run":
        _cmd_run(args)
    else:
        print(report(sorted(Path(args.dir).glob("*/c*.json"))), end="")


if __name__ == "__main__":
    main()
