# 源码笔记 1 · 调度器：每一步算哪些 token

对应 vLLM v0.30.0（与 `vllm/vllm-openai:v0.30.0` 镜像同一个 tag）。代码位置写成 `vllm/<路径>:<行号>`。

## 要解决的问题

压测里看到三个现象，都要回到「每一步到底算了哪些 token」来解释：

1. 基线在并发 16 → 32 时，吞吐只多 22%，TTFT P95 从 947ms 涨到 7,683ms（`results/bench/knee.md`）。
2. 同样在并发 32、64，基线的 ITL P95 是 76~78ms，SGLang 只有约 24ms（`results/06-sglang/compare.md`）。
3. `knee.md` 待查问题 2：设了 `--max-num-seqs 32/64` 之后，vLLM 自己的 ITL P95 也降到约 24ms。

## 关键数据结构

| 名字 | 位置 | 含义 | 本项目的取值 |
| --- | --- | --- | --- |
| `self.running` / `self.waiting` | `vllm/v1/core/sched/scheduler.py` | 正在算的请求、排队的请求 | — |
| `token_budget` | `scheduler.py:581` | 这一步最多算多少个 token，来自 `max_num_batched_tokens` | **2,048** |
| `max_num_running_reqs` | `scheduler.py:124` | 同时在跑的请求数上限，来自 `max_num_seqs` | **256** |

两个默认值按显卡显存选（`vllm/engine/arg_utils.py:2727-2760`）：显存 ≥ 70GiB 的卡（H100 等）以 OpenAI 服务方式启动时是 8,192 / 1,024；
4090 48G 落在 `else` 分支，是 **2,048 / 256**。

## 调用路径

`Scheduler.schedule()`（`scheduler.py:561`）开头的注释说得很直白：调度器里**没有 prefill 阶段和 decode 阶段之分**，
每个请求只有「已经算了多少个 token」和「总共要算多少个 token」，每一步尽量让前者追上后者（`scheduler.py:563-572`）。

1. **先排正在跑的请求**（`scheduler.py:610-612`）：在 decode 的请求每个只要 1 个 token；还没 prefill 完的请求要剩下的 prompt token。
   每排一个，从 `token_budget` 里扣掉（`scheduler.py:807`）。
2. **给它分 KV 块**（`allocate_slots`，`scheduler.py:723-728`）。分不到就**抢占**：把 `running` 里排在最后的请求踢出去（`scheduler.py:753`、`785`），
   直到分得到或者没人可踢。
3. **抢占是「重算」，不是「换出」**：`_preempt_request`（`scheduler.py:1477`）释放这个请求的所有 KV 块，把 `num_computed_tokens` 归零（`scheduler.py:1497`），
   放回等待队列。它下次被调度时，要把 prompt 加上已经生成的 token 全部重新 prefill 一遍。
4. **再排等待中的请求**（`scheduler.py:854-864`）：只有这一步**没有发生抢占**、且在跑的请求数没到 `max_num_seqs` 时才进。
   新请求能拿的 token 数是 `min(剩余 prompt, 剩余 token_budget)`（`scheduler.py:1035-1080`），开着 chunked prefill 时超出预算的部分留到下一步。

所以一步里常常是「二十几个 decode 请求各 1 个 token + 一个新请求的上千个 prompt token」挤在一起算。

## 对应本项目的哪个现象

**现象 1：拐点之后 TTFT 暴涨。** 等待中的请求要「分得到 KV 块」才能进（第 2、4 步）。KV Cache 只有 30,352 token（见笔记 2），
并发 32 时装不下，新请求只能在 `waiting` 里等，等待时间全部算进 TTFT；吞吐受 KV 容量封顶，几乎不再涨。
抢占会让情况更糟：被踢出的请求要从头重算，重算的 prefill 又占掉别人的预算。

**现象 2：vLLM 和 SGLang 的 ITL 差别。** 一步里只要混进一个上千 token 的 prefill，这一步就从「二十几个 token」变成「一千多个 token」。
另外 CUDA Graph 最多只捕获到 512 个 token 的批次（`vllm/config/vllm.py:2206-2224`，默认上限 `min(2 × max_num_seqs, 512)`），
混了 prefill 的大批次不走 CUDA Graph，比纯 decode 的步慢得多。正在生成的请求在这样的步里多等一下，ITL 的长尾就上去了。
SGLang 的日志里 `Prefill batch` 和 `Decode batch` 是分开的两种批次，新请求的 prefill 不插进正在 decode 的批次：
逐字稳定（ITL P95 约 24ms），代价是新请求要等（并发 16 时 TTFT P95 1,649 vs 947ms）。两个引擎在「首字」和「逐字」之间做了相反的取舍。

**现象 3（未解决）：为什么 vLLM 设了 `--max-num-seqs 32/64` 也只有约 24ms。** 并发 32 时，`max_num_seqs=32` 并没有比默认的 256 更多地限制住同时在跑的请求数
（KV 只装得下二十来个），按上面的机制，混批应该照样发生。代码里看不出差别在哪，有两种可能：
- 两组的抢占次数不同（KV 容量也不同：30,352 vs 25,216 token），重算带来的混批次数不同；
- 混批的频率本身不同。

验证办法（下次上机，约 10 分钟）：分别用基线和 `--max-num-seqs 32` 跑一轮并发 32，对比两个指标的增量：
`vllm:num_preemptions`（抢占次数，`vllm/v1/metrics/loggers.py:667`）和 `vllm:iteration_tokens_total`（每一步算了多少 token 的直方图，`loggers.py:735`）。
如果混批或抢占在基线里明显更多，就能确认原因。
