"""第 5 课 · 流量到底去了哪里

经 LiteLLM 网关并发发一批请求，按响应头 x-litellm-model-id 统计每个请求最终由谁处理：
self-hosted（自建 vLLM）还是 bailian（百炼）。在 bench-client Pod 里运行：

    kubectl -n infer exec deploy/bench-client -- env LITELLM_MASTER_KEY=<key> \\
      python3 /lab/scripts/gateway_probe.py --base-url http://litellm.gateway:4000 \\
      --model qwen3-8b --total 300 --concurrency 64 --out /results/gateway/overload.json

输出一行汇总，例如 {"self-hosted": 231, "bailian": 69}，并把每个请求的明细写进 --out。
非 200 的响应计入 error；200 但没有后端标识的计入 unknown，两种都不会让脚本中断。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
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


def backend_from_headers(headers: Mapping[str, str]) -> Optional[str]:
    return headers.get(BACKEND_HEADER) or None


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


async def probe(base_url: str, api_key: str, model: str, total: int, concurrency: int) -> list[ProbeResult]:
    import httpx

    sem = asyncio.Semaphore(concurrency)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "请用一句话介绍 Kubernetes。"}],
        "max_tokens": 64,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=180) as client:
        async def one() -> ProbeResult:
            async with sem:
                start = time.perf_counter()
                try:
                    resp = await client.post("/v1/chat/completions", json=payload)
                    return ProbeResult(resp.status_code, backend_from_headers(resp.headers), time.perf_counter() - start)
                except httpx.HTTPError:
                    return ProbeResult(0, None, time.perf_counter() - start)

        return await asyncio.gather(*(one() for _ in range(total)))


def main() -> None:
    ap = argparse.ArgumentParser(description="经 LiteLLM 网关探测请求最终落在哪个后端")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--total", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    api_key = os.environ.get("LITELLM_MASTER_KEY")
    if not api_key:
        raise SystemExit("请设置环境变量 LITELLM_MASTER_KEY")

    results = asyncio.run(probe(args.base_url, api_key, args.model, args.total, args.concurrency))
    summary = summarize(results)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "args": vars(args) | {"out": str(out)},
                               "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
