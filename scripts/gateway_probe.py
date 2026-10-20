"""第 5 课 · 流量到底去了哪里

经 LiteLLM 网关并发发一批请求，按响应头 x-litellm-model-id 统计每个请求最终由谁处理：
self-hosted（自建 vLLM）还是 bailian（百炼）。在 bench-client Pod 里运行：

    kubectl -n infer exec deploy/bench-client -- env LITELLM_MASTER_KEY=<key> \\
      python3 /lab/scripts/gateway_probe.py --base-url http://litellm.gateway:4000 \\
      --model qwen3-8b --total 300 --concurrency 64 --out /results/gateway/overload.json

输出一行汇总，例如 {"self-hosted": 231, "bailian": 69}，并把每个请求的明细写进 --out。
非 200 的响应计入 error；200 但没有后端标识的计入 unknown，两种都不会让脚本中断。

── 请求为什么要这么长 ────────────────────────────────────────

切换靠的是超时：vLLM 排队太久，请求超过网关的 timeout 才会被转给百炼。timeout 是按第 4 课的压测
（输入 1024、输出 256）标定的，所以探测请求默认也做成同样的形状：约 1000 个 token 的输入、
最多 256 个 token 的输出，每个请求开头带一个随机串，避免命中前缀缓存。
如果只发「一句话」的短请求，vLLM 几乎不排队，每个两秒就完成，超时永远触发不了。
另外 httpx 默认最多 100 个连接，并发开得再大也会被悄悄压到 100，所以连接上限跟着 --concurrency 走。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Optional

BACKEND_HEADER = "x-litellm-model-id"


@dataclass
class ProbeResult:
    status: int
    backend: Optional[str]
    latency_s: float
    # 请求发出时的 Unix 时间。第 8 课用它按「新副本就绪」的时间点把请求分成前后两半
    started_at: float = 0.0


def backend_from_headers(headers: Mapping[str, str]) -> Optional[str]:
    return headers.get(BACKEND_HEADER) or None


def build_payload(model: str, prompt_tokens: int, max_tokens: int, nonce: str) -> dict:
    """构造一个和压测同形状的请求：nonce 打头，后面约 prompt_tokens 个 token 的填充。"""
    filler = "hello " * prompt_tokens
    return {
        "model": model,
        "messages": [{"role": "user", "content": f"{nonce} 请复述下面的内容：{filler}"}],
        "max_tokens": max_tokens,
        "stream": False,
    }


def client_limits(concurrency: int):
    import httpx

    return httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)


def summarize(results: list[ProbeResult]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for r in results:
        if r.status != 200:
            counts["error"] += 1
        elif r.backend is None:
            counts["unknown"] += 1
        else:
            counts[r.backend] += 1
    return dict(counts)


def split_at(results: list[ProbeResult], t: float) -> tuple[dict[str, int], dict[str, int]]:
    """按发出时间切成 t 之前、之后两组，各自汇总。"""
    return (summarize([r for r in results if r.started_at < t]),
            summarize([r for r in results if r.started_at >= t]))


async def probe(base_url: str, api_key: str, model: str, total: int, concurrency: int,
                prompt_tokens: int = 1000, max_tokens: int = 256) -> list[ProbeResult]:
    import httpx

    sem = asyncio.Semaphore(concurrency)
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=180,
                                 limits=client_limits(concurrency)) as client:
        async def one() -> ProbeResult:
            payload = build_payload(model, prompt_tokens, max_tokens, nonce=uuid.uuid4().hex)
            async with sem:
                sent, start = time.time(), time.perf_counter()
                try:
                    resp = await client.post("/v1/chat/completions", json=payload)
                    return ProbeResult(resp.status_code, backend_from_headers(resp.headers), time.perf_counter() - start, sent)
                except httpx.HTTPError:
                    return ProbeResult(0, None, time.perf_counter() - start, sent)

        return await asyncio.gather(*(one() for _ in range(total)))


def main() -> None:
    ap = argparse.ArgumentParser(description="经 LiteLLM 网关探测请求最终落在哪个后端")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--total", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--prompt-tokens", type=int, default=1000, help="输入长度（约等于 token 数），默认与压测一致")
    ap.add_argument("--max-tokens", type=int, default=256, help="输出上限，默认与压测一致")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    api_key = os.environ.get("LITELLM_MASTER_KEY")
    if not api_key:
        raise SystemExit("请设置环境变量 LITELLM_MASTER_KEY")

    results = asyncio.run(probe(args.base_url, api_key, args.model, args.total, args.concurrency,
                                args.prompt_tokens, args.max_tokens))
    summary = summarize(results)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "args": vars(args) | {"out": str(out)},
                               "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
