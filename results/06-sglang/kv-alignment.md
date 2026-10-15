# SGLang 与 vLLM 的 KV Cache 容量对齐（2026-10-16）

| 引擎 | 显存参数 | KV Cache 容量（token） | 与 vLLM 的偏差 | 来源 |
| --- | --- | --- | --- | --- |
| vLLM v0.30.0 | `--gpu-memory-utilization 0.45` | 30,352 | — | `../02-vllm/kv-capacity.txt` |
| SGLang v0.5.20 | `--mem-fraction-static 0.42` | 32,116 | +5.8% | 启动日志 `max_total_num_tokens=32116`，`KV Cache is allocated ... K size: 2.21 GB, V size: 2.21 GB` |

一次尝试即在 10% 以内，未再调整。两个参数语义不同：vLLM 的是整卡显存占比（权重 + KV + 激活 + CUDA Graph），
SGLang 的是权重 + KV 池占比；按「权重 15.3 GiB + 30,352 token × 144KB ≈ 19.5 GiB，÷ 47.4 GiB ≈ 0.41」估算后取 0.42。

单发核对：`/v1/completions` 带 `ignore_eos: true`、`max_tokens: 256` 时输出恰好 256 token（`finish_reason: length`）；
`/v1/chat/completions` 带 `chat_template_kwargs.enable_thinking: false` 时回答不含思考内容。两项与 vLLM 一致，标准负载可直接复用 `bench.sh`。

指标核对：`expected-metrics.txt` 的 4 个指标全部存在；Agent 负载命中率用的 `sglang:cached_tokens_total`、`sglang:prompt_tokens_total` 也存在（`metrics.txt`）。
