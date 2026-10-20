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
2. 估算 CUDA Graph 要占的显存（`gpu_worker.py:580-585`），但**默认不从 KV 里扣**：只有设了环境变量 `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS` 才扣（`gpu_worker.py:588-591`）。
3. 可用 KV 显存 = 申请的显存（总显存 × `gpu_memory_utilization`）− 非 KV 的占用（权重、非 torch 内存、前向的瞬时峰值）− 上一步扣的 CUDA Graph（`gpu_worker.py:622-625`）。

用第一周的启动日志（`results/02-vllm/kv-capacity.txt`）逐项对上：

```
申请的显存   47.37 GiB × 0.45                 = 21.32 GiB
权重 + 非 torch                               − 15.54 GiB
前向瞬时峰值                                  −  1.61 GiB
CUDA Graph 0.23 GiB（默认不扣）               −  0
可用 KV 显存                                  =  4.17 GiB   ← 日志 Available KV cache memory: 4.17 GiB

每 token 的 KV = 36 层 × 2（K、V）× 8 个 KV 头 × 128 维 × 2 字节 = 147,456 字节
4.17 GiB ÷ 147,456 字节 = 30,365 token，按每块 16 token 向下取整
= 1,897 块 = 30,352 token                                    ← 日志 GPU KV cache size: 30,352 tokens
```

如果 CUDA Graph 也扣掉，可用 KV 只有 3.94 GiB，与日志对不上，这也印证了第 2 步「默认不扣」。

## 调用路径二：块怎么分、KV 满了怎么办

每一步调度器给请求要块（`allocate_slots`）。正在 decode 的请求每生成 16 个 token 要一个新块；新请求一进来就要一次分够 prompt 的块。
分不到时，调度器抢占 `running` 里排在最后的请求：**释放它的全部块、已算 token 数归零**，以后从头重算（笔记 1 的调用路径第 3 步）。
vLLM v1 不再把 KV 换出到 CPU 内存，只有重算这一种抢占方式。

## 对应本项目的哪个现象

**启动日志的 KV 容量**：已经在上面逐项对上，误差为 0。第一周按「每 token 约 144KB」手算的 2~3 万 token 也是同一套公式。

**KVCacheNearFull 告警来回跳**（`results/03-monitoring/c32-knee.md`）：KV 不是慢慢涨满后一直满着。每次抢占或请求完成都会一下子归还一整个请求的块
（一个 1,280 token 的请求是 80 块，约占 4%），占用率在 0.85~0.99 之间锯齿形波动，「连续 1 分钟高于 0.9」很难满足。

**待查问题 1（只解释了一半）**：代码里 `max_num_seqs` 至少从两处影响启动时的显存账：

| 影响 | 位置 | `max_num_seqs` 变小时 |
| --- | --- | --- |
| CUDA Graph 最大捕获批次 = `min(2 × max_num_seqs, 512)` | `vllm/config/vllm.py:2206-2224` | 默认 256 时捕获到 512；16 时只到 32，要捕获的图少很多 |
| 假批次拆成的请求数 = `min(token 数, max_num_seqs)` | `gpu_model_runner.py:5944` | 采样阶段的 logits 张量变小（每个请求一行，词表 151,936） |

两处都预测「`max_num_seqs` 越小，非 KV 占用越少，KV 越多」。这能解释 16 时 KV 变多（4.65 vs 4.17 GiB），
**解释不了 32、64 时反而变少**（3.46、3.38 GiB）。第一周只保存了「Available KV cache memory」这一行，没保存分项。

验证办法（下次上机，约 6 分钟）：分别用 `--max-num-seqs 32` 和默认值各启动一次，读启动日志里
「Actual usage is … for consumed memory, … for peak activation, and … for CUDAGraph memory」那一行，看多出来的显存落在哪一项。
