"""第 5 课 · 自建到底省不省钱

用压测测出的饱和吞吐，算自建 vLLM 和调用百炼的单请求成本，以及盈亏平衡点。

    .venv/bin/python scripts/cost.py --bench results/bench/baseline/c32.json \\
      --gpu-price 3.17 --input-price <百炼输入 元/百万token> --output-price <百炼输出 元/百万token>

按「单个请求」比较（默认输入 1024、输出 256 个 token），而不是只按输出 token：
API 对输入 token 也收钱，只看输出会低估 API 的成本，两边口径就不一致了。

利用率给两行：100% 是 GPU 一刻不停地满载，现实里做不到；30% 更接近一般业务的平均水平。
自建的成本随利用率反比上升，这是「自建是否划算」最关键的变量。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _positive(**values: float) -> None:
    for name, v in values.items():
        if v <= 0:
            raise ValueError(f"{name} 必须大于 0，当前为 {v}")


def self_hosted_cost_per_request(gpu_price_per_hour: float, requests_per_sec: float, utilization: float) -> float:
    """GPU 每秒的价格 ÷ 每秒实际完成的请求数。utilization 是 GPU 处于满载的时间比例。"""
    _positive(gpu_price_per_hour=gpu_price_per_hour, requests_per_sec=requests_per_sec, utilization=utilization)
    return gpu_price_per_hour / 3600 / requests_per_sec / utilization


def api_cost_per_request(input_tokens: int, output_tokens: int,
                         input_price_per_mtok: float, output_price_per_mtok: float) -> float:
    _positive(input_price_per_mtok=input_price_per_mtok, output_price_per_mtok=output_price_per_mtok)
    return (input_tokens * input_price_per_mtok + output_tokens * output_price_per_mtok) / 1e6


def breakeven_requests_per_day(gpu_price_per_hour: float, api_cost_per_request: float) -> float:
    """一张卡租一整天的钱，够调用多少次 API。日调用量超过这个数，自建才更便宜。"""
    _positive(gpu_price_per_hour=gpu_price_per_hour, api_cost_per_request=api_cost_per_request)
    return gpu_price_per_hour * 24 / api_cost_per_request


def main() -> None:
    ap = argparse.ArgumentParser(description="自建与 API 的单请求成本对比")
    ap.add_argument("--bench", required=True, help="饱和并发那一档的 vllm bench serve 结果 JSON")
    ap.add_argument("--gpu-price", type=float, required=True, help="GPU 元/小时（实付）")
    ap.add_argument("--input-price", type=float, required=True, help="API 输入 元/百万 token")
    ap.add_argument("--output-price", type=float, required=True, help="API 输出 元/百万 token")
    ap.add_argument("--input-tokens", type=int, default=1024)
    ap.add_argument("--output-tokens", type=int, default=256)
    args = ap.parse_args()

    d = json.loads(Path(args.bench).read_text(encoding="utf-8"))
    rps = float(d["request_throughput"])
    api = api_cost_per_request(args.input_tokens, args.output_tokens, args.input_price, args.output_price)
    tokens = args.input_tokens + args.output_tokens

    print(f"数据来源：{args.bench}（并发 {d.get('max_concurrency')}，请求吞吐 {rps:.2f} 个/秒）\n")
    print("| GPU 利用率 | 自建 元/请求 | API 元/请求 | 自建 元/百万 token | 单卡日上限 请求/天 |")
    print("| --- | --- | --- | --- | --- |")
    for util in (1.0, 0.3):
        own = self_hosted_cost_per_request(args.gpu_price, rps, util)
        print(f"| {util:.0%} | {own:.5f} | {api:.5f} | {own / tokens * 1e6:.2f} | {rps * 86400 * util:,.0f} |")
    be = breakeven_requests_per_day(args.gpu_price, api)
    print(f"\n盈亏平衡：每天超过 {be:,.0f} 个请求，租一张卡全天运行比调用 API 便宜。")
    if be > rps * 86400:
        print("注意：这个量已经超过单卡满载一整天的处理能力，单卡自建在这个价格下不可能比 API 便宜。")


if __name__ == "__main__":
    main()
