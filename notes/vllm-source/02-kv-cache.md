# 源码笔记 2 · KV Cache：容量怎么定、块怎么分

对应 vLLM v0.30.0。代码位置写成 `vllm/<路径>:<行号>`。

## 要解决的问题

1. 启动日志里那个「GPU KV cache size: 30,352 tokens」是怎么算出来的？
2. KV 满了以后发生什么？KVCacheNearFull 告警为什么老在 Pending 和 Firing 之间来回跳？
3. `knee.md` 待查问题 1：`--max-num-seqs` 明明是调度参数，为什么会改变可用的 KV 显存（默认 4.17 GiB，设 16 时 4.65，设 32、64 时 3.46、3.38）？

## 关键数据结构

| 名字 | 位置 | 作用 |
| --- | --- | --- |
| 块（block） | `vllm/config/cache.py:73` | KV 的分配单位，本项目每块 16 个 token |
| `BlockPool` | `vllm/v1/core/block_pool.py` | 所有块的池子，`get_new_blocks` 分配、`free_blocks` 归还 |
| `FreeKVCacheBlockQueue` | `vllm/v1/core/kv_cache_utils.py:246` | 空闲块的双向链表，按 LRU 排序：最久没用的在队头，先被复用 |
| `KVCacheManager.allocate_slots` | `vllm/v1/core/kv_cache_manager.py` | 调度器每一步给请求分块的入口（见笔记 1） |

## 调用路径一：容量是怎么算出来的

引擎启动时，`Worker.determine_available_memory`（`vllm/v1/worker/gpu_worker.py:528`）做三件事：

1. 用一批假输入跑一次前向（`profile_run`，`gpu_worker.py:566-572`），记下显存峰值。假批次是 `max_num_batched_tokens` 个 token，
   拆成 `min(token 数, max_num_seqs)` 个请求（`vllm/v1/worker/gpu_model_runner.py:5923-5945`）。
2. 估算 CUDA Graph 要占的显存（`gpu_worker.py:580-585`），**默认会从 KV 里扣掉**：开关 `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS` 默认是 1（`vllm/envs.py:2109-2111`，判断在 `gpu_worker.py:588-591`）。
   这个估算值被并进日志里的「peak activation」一起打印（`gpu_worker.py:616-618`）；日志里单独的「CUDAGraph memory」是捕获完成后的**实际**占用，通常比估算小。
3. 可用 KV 显存 = 申请的显存（总显存 × `gpu_memory_utilization`）− 非 KV 的占用（权重、非 torch 内存、前向的瞬时峰值）− CUDA Graph 估算（`gpu_worker.py:622-625`）。

用第一周的启动日志（`results/02-vllm/kv-capacity.txt`）逐项对上：

```
申请的显存   47.37 GiB × 0.45                 = 21.32 GiB
权重 + 非 torch                               − 15.54 GiB
前向瞬时峰值 1.18 + CUDA Graph 估算 0.43        −  1.61 GiB   ← 日志 peak activation 1.61 GiB
可用 KV 显存                                  =  4.17 GiB   ← 日志 Available KV cache memory: 4.17 GiB

每 token 的 KV = 36 层 × 2（K、V）× 8 个 KV 头 × 128 维 × 2 字节 = 147,456 字节
4.17 GiB ÷ 147,456 字节 = 30,365 token，按每块 16 token 向下取整
= 1,897 块 = 30,352 token                                    ← 日志 GPU KV cache size: 30,352 tokens
```

CUDA Graph 估算 0.43 GiB 来自日志里「--gpu-memory-utilization=0.4500 is equivalent to 0.4409 without CUDA graph memory profiling」：(0.45 − 0.4409) × 47.37 ≈ 0.43 GiB。
实际捕获只用了 0.23 GiB，估算偏大约 0.2 GiB，所以日志会建议改用 `--kv-cache-memory` 精确指定。
（本笔记初稿把日志里的 0.23 当成「没扣的 CUDA Graph」，算成「默认不扣」，2026-10-21 上机核对分项后更正。）

## 调用路径二：块怎么分、KV 满了怎么办

每一步调度器给请求要块（`allocate_slots`）。正在 decode 的请求每生成 16 个 token 要一个新块；新请求一进来就要一次分够 prompt 的块。
分不到时，调度器抢占 `running` 里排在最后的请求：**释放它的全部块、已算 token 数归零**，以后从头重算（笔记 1 的调用路径第 3 步）。
vLLM v1 不再把 KV 换出到 CPU 内存，只有重算这一种抢占方式。

## 对应本项目的哪个现象

**启动日志的 KV 容量**：已经在上面逐项对上，误差为 0。第一周按「每 token 约 144KB」手算的 2~3 万 token 也是同一套公式。

**KVCacheNearFull 告警来回跳**（`results/03-monitoring/c32-knee.md`）：KV 不是慢慢涨满后一直满着。每次抢占或请求完成都会一下子归还一整个请求的块
（一个 1,280 token 的请求是 80 块，约占 4%），占用率在 0.85~0.99 之间锯齿形波动，「连续 1 分钟高于 0.9」很难满足。

**待查问题 1（定位到了哪一项，原因仍未解释）**：代码里 `max_num_seqs` 至少从两处影响启动时的显存账：

| 影响 | 位置 | `max_num_seqs` 变小时 |
| --- | --- | --- |
| CUDA Graph 最大捕获批次 = `min(2 × max_num_seqs, 512)` | `vllm/config/vllm.py:2206-2224` | 默认 256 时捕获到 512；16 时只到 32，要捕获的图少很多 |
| 假批次拆成的请求数 = `min(token 数, max_num_seqs)` | `gpu_model_runner.py:5944` | 采样阶段的 logits 张量变小（每个请求一行，词表 151,936） |

两处都预测「`max_num_seqs` 越小，非 KV 占用越少，KV 越多」。2026-10-21 上机，三种设置各启动一次，读启动日志的分项（`results/verify/startup-*.txt`）：

| 分项（GiB） | 默认 256 | `--max-num-seqs 32` | `--max-num-seqs 16` |
| --- | --- | --- | --- |
| 权重 + 非 torch | 15.54 | **16.55** | 15.38 |
| 前向瞬时峰值 | 1.18 | 1.19 | 1.19 |
| CUDA Graph 估算 | 0.43 | 0.12 | 0.10 |
| 可用 KV（21.32 减去以上三项） | 4.17 | 3.46 | 4.65 |
| KV 容量（token） | 30,352 | 25,216 | 33,824 |

- **CUDA Graph 估算**按代码预测的方向变小（捕获上限从 512 降到 64、32），每种设置省出约 0.3 GiB。
- **前向瞬时峰值**几乎不变：假批次的 token 总数都是 2,048，只是拆成的请求数不同，采样的 logits 差别只有百 MB 级。
- **真正让 32 变少的是「权重 + 非 torch」多了 1.01 GiB**：权重相同（都是 15.27 GiB），多出来的是 PyTorch 分配器以外的显存（CUDA 上下文、各类库的工作区等）。
  它在 16 时并不变大（15.38），所以不是随 `max_num_seqs` 单调变化的；第一周设 32、64 时的 KV（3.46、3.38 GiB）和这次一致，说明可以稳定复现，不是测量抖动。
  它为什么只在 32、64 时出现，需要追到 CUDA 库的分配行为，超出本笔记范围。
