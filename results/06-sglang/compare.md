# vLLM vs SGLang 标准负载对比（2026-10-16）

同一张 RTX 4090 48G、同一个 Qwen3-8B（BF16）、同一个压测客户端（`scripts/bench.sh`，random 输入 1024、输出 256，`--ignore-eos`，每档不同 seed）。
KV Cache 容量：**vLLM 30,352 token / SGLang 32,116 token（+5.8%）**，见 `kv-alignment.md`。vLLM 数据为 `../bench/baseline/`（2026-10-15 新 seed 重跑），SGLang 为 `../bench/sglang-baseline/`。两边全部 0 失败。

| 并发 | 输出吞吐 tok/s（vLLM / SGLang） | TTFT P50 ms | TTFT P95 ms | ITL P95 ms | E2E P95 ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 57 / 57 | 113 / 122 | 115 / 123 | 17.6 / 17.2 | 4,526 / 4,462 |
| 4 | 191 / 193 | 281 / 327 | 424 / 423 | 18.9 / 18.6 | 5,185 / 5,118 |
| 16 | 592 / 586 | 585 / 1,143 | 947 / 1,649 | 21.4 / 21.3 | 7,122 / 6,998 |
| 32 | 720 / 738 | 1,503 / 2,120 | 7,683 / 9,489 | 75.9 / 23.9 | 16,028 / 17,398 |
| 64 | 734 / 752 | 11,220 / 11,436 | 17,439 / 18,627 | 78.4 / 24.0 | 25,694 / 26,536 |

## 结论

- **吞吐基本相同**：各档差距在 2.5% 以内，SGLang 在并发 32、64 略高（KV 多 5.8% 也有贡献）。
- **首字延迟 vLLM 更好**：并发 16 时 TTFT P50 585 vs 1,143ms、P95 947 vs 1,649ms；并发 32 时 P95 7.7 vs 9.5 秒。
- **逐字延迟 SGLang 更稳**：并发 32、64 时 ITL P95 约 24ms，vLLM 为 76~78ms。
- 推测（待源码笔记核实）：SGLang 的日志里 prefill 和 decode 是分开的批次（`Prefill batch` / `Decode batch`），新请求的 prefill 不插进正在生成的批次，逐字稳定但新请求要等；vLLM 把新请求的 prefill 分块混进 decode 步，首字更快，但正在生成的请求被拖慢。这也对应 `../bench/knee.md` 待查问题 2（vLLM 设 `--max-num-seqs 32/64` 后 ITL P95 也降到约 24ms）。
- 选型：在意首字的聊天类产品偏向 vLLM；长输出、在意流式输出流畅度的场景 SGLang 更稳。Agent 场景以 prefill 为主，看第 10 天的 Agent 负载结果。
