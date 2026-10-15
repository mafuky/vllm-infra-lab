| 引擎 | 条件 | 并发 | 轮次 | TTFT P50 ms | TTFT P95 ms | 成功 | 失败 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sglang | shared | 8 | 1 | 863 | 915 | 32 | 0 |
| sglang | shared | 8 | 2 | 634 | 695 | 32 | 0 |
| sglang | shared | 8 | 3 | 648 | 714 | 32 | 0 |
| sglang | shared | 8 | 4 | 666 | 733 | 32 | 0 |
| sglang | shared | 8 | 5 | 663 | 756 | 32 | 0 |
| sglang | shared | 8 | 6 | 656 | 4554 | 32 | 0 |
| sglang | shared | 16 | 1 | 1541 | 9324 | 64 | 0 |
| sglang | shared | 16 | 2 | 845 | 2758 | 64 | 0 |
| sglang | shared | 16 | 3 | 846 | 5248 | 64 | 0 |
| sglang | shared | 16 | 4 | 2018 | 7858 | 64 | 0 |
| sglang | shared | 16 | 5 | 6787 | 9174 | 64 | 0 |
| sglang | shared | 16 | 6 | 7091 | 9607 | 64 | 0 |
| sglang | shared | 32 | 1 | 8710 | 22105 | 128 | 0 |
| sglang | shared | 32 | 2 | 3370 | 10055 | 128 | 0 |
| sglang | shared | 32 | 3 | 8149 | 14553 | 128 | 0 |
| sglang | shared | 32 | 4 | 14048 | 18262 | 128 | 0 |
| sglang | shared | 32 | 5 | 16675 | 21949 | 128 | 0 |
| sglang | shared | 32 | 6 | 20692 | 24526 | 128 | 0 |
| sglang | unique | 8 | 1 | 7453 | 9262 | 32 | 0 |
| sglang | unique | 8 | 2 | 6633 | 8790 | 32 | 0 |
| sglang | unique | 8 | 3 | 6980 | 8808 | 32 | 0 |
| sglang | unique | 8 | 4 | 3336 | 9620 | 32 | 0 |
| sglang | unique | 8 | 5 | 8115 | 9459 | 32 | 0 |
| sglang | unique | 8 | 6 | 7576 | 10079 | 32 | 0 |
| sglang | unique | 16 | 1 | 15780 | 22925 | 64 | 0 |
| sglang | unique | 16 | 2 | 14111 | 16246 | 64 | 0 |
| sglang | unique | 16 | 3 | 15488 | 20352 | 64 | 0 |
| sglang | unique | 16 | 4 | 16938 | 21776 | 64 | 0 |
| sglang | unique | 16 | 5 | 21477 | 22897 | 64 | 0 |
| sglang | unique | 16 | 6 | 22497 | 23929 | 64 | 0 |
| sglang | unique | 32 | 1 | 35464 | 49529 | 128 | 0 |
| sglang | unique | 32 | 2 | 30025 | 35537 | 128 | 0 |
| sglang | unique | 32 | 3 | 35371 | 41214 | 128 | 0 |
| sglang | unique | 32 | 4 | 41018 | 46942 | 128 | 0 |
| sglang | unique | 32 | 5 | 46672 | 49219 | 128 | 0 |
| sglang | unique | 32 | 6 | 49295 | 51237 | 128 | 0 |
| vllm | shared | 8 | 1 | 715 | 1198 | 32 | 0 |
| vllm | shared | 8 | 2 | 297 | 668 | 32 | 0 |
| vllm | shared | 8 | 3 | 395 | 591 | 32 | 0 |
| vllm | shared | 8 | 4 | 395 | 609 | 32 | 0 |
| vllm | shared | 8 | 5 | 408 | 628 | 32 | 0 |
| vllm | shared | 8 | 6 | 726 | 4037 | 32 | 0 |
| vllm | shared | 16 | 1 | 1469 | 8305 | 64 | 0 |
| vllm | shared | 16 | 2 | 615 | 1112 | 64 | 0 |
| vllm | shared | 16 | 3 | 735 | 3277 | 64 | 0 |
| vllm | shared | 16 | 4 | 2431 | 7199 | 64 | 0 |
| vllm | shared | 16 | 5 | 6926 | 7479 | 64 | 0 |
| vllm | shared | 16 | 6 | 8355 | 12242 | 64 | 0 |
| vllm | shared | 32 | 1 | 7949 | 25576 | 128 | 0 |
| vllm | shared | 32 | 2 | 1561 | 6619 | 128 | 0 |
| vllm | shared | 32 | 3 | 7809 | 10636 | 128 | 0 |
| vllm | shared | 32 | 4 | 13633 | 16455 | 128 | 0 |
| vllm | shared | 32 | 5 | 19046 | 20329 | 128 | 0 |
| vllm | shared | 32 | 6 | 24359 | 27197 | 128 | 0 |
| vllm | unique | 8 | 1 | 6775 | 10296 | 32 | 0 |
| vllm | unique | 8 | 2 | 3063 | 5404 | 32 | 0 |
| vllm | unique | 8 | 3 | 4844 | 6347 | 32 | 0 |
| vllm | unique | 8 | 4 | 6392 | 7176 | 32 | 0 |
| vllm | unique | 8 | 5 | 7366 | 7738 | 32 | 0 |
| vllm | unique | 8 | 6 | 8325 | 11592 | 32 | 0 |
| vllm | unique | 16 | 1 | 15096 | 25075 | 64 | 0 |
| vllm | unique | 16 | 2 | 12804 | 13783 | 64 | 0 |
| vllm | unique | 16 | 3 | 14537 | 17706 | 64 | 0 |
| vllm | unique | 16 | 4 | 18258 | 19489 | 64 | 0 |
| vllm | unique | 16 | 5 | 20075 | 20771 | 64 | 0 |
| vllm | unique | 16 | 6 | 24189 | 25887 | 64 | 0 |
| vllm | unique | 32 | 1 | 37278 | 54964 | 128 | 0 |
| vllm | unique | 32 | 2 | 30853 | 33312 | 128 | 0 |
| vllm | unique | 32 | 3 | 35077 | 37225 | 128 | 0 |
| vllm | unique | 32 | 4 | 39302 | 43674 | 128 | 0 |
| vllm | unique | 32 | 5 | 45260 | 46549 | 128 | 0 |
| vllm | unique | 32 | 6 | 51630 | 57228 | 128 | 0 |

| 引擎 | 条件 | 并发 | 前缀缓存命中率 | 输出 tok/s | 失败请求 |
| --- | --- | --- | --- | --- | --- |
| sglang | shared | 8 | 88.5% | 289 | 0 |
| sglang | shared | 16 | 72.3% | 280 | 0 |
| sglang | shared | 32 | 65.5% | 264 | 0 |
| sglang | unique | 8 | 12.8% | 111 | 0 |
| sglang | unique | 16 | 0.1% | 105 | 0 |
| sglang | unique | 32 | 0.1% | 106 | 0 |
| vllm | shared | 8 | 86.1% | 278 | 0 |
| vllm | shared | 16 | 68.4% | 254 | 0 |
| vllm | shared | 32 | 65.3% | 248 | 0 |
| vllm | unique | 8 | 0.0% | 104 | 0 |
| vllm | unique | 16 | 0.0% | 104 | 0 |
| vllm | unique | 32 | 0.0% | 104 | 0 |

## 结论（2026-10-16）

负载：每会话 6 轮，系统提示词 4,000 token、每轮新增 500 token、每轮输出 150 token；第 1 轮上下文 4,517 token，第 6 轮 7,817 token。
两个引擎的 KV Cache 已对齐（vLLM 30,352 / SGLang 32,116 token）。12 组共 3,264 个请求，全部 0 失败。

| 引擎 | 条件 | 并发 | 第 1 轮 TTFT P50 | 第 2~6 轮 TTFT P50 的中位数 | 第 6 轮 TTFT P95 | 总耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| sglang | shared | 8 | 863ms | 656ms | 4,554ms | 100s |
| vllm | shared | 8 | 715ms | 395ms | 4,037ms | 104s |
| sglang | unique | 8 | 7,453ms | 6,980ms | 10,079ms | 260s |
| vllm | unique | 8 | 6,775ms | 6,392ms | 11,592ms | 278s |
| sglang | shared | 16 | 1,541ms | 2,018ms | 9,607ms | 206s |
| vllm | shared | 16 | 1,469ms | 2,431ms | 12,242ms | 227s |
| sglang | unique | 32 | 35,464ms | 41,018ms | 51,237ms | 1,092s |
| vllm | unique | 32 | 37,278ms | 39,302ms | 57,228ms | 1,111s |

1. **共享系统提示词的收益远大于换引擎**：并发 8 时，第 2~6 轮首字 vLLM 从 6,392ms 降到 395ms（16 倍）、SGLang 从 6,980ms 降到 656ms（11 倍）；输出吞吐约 2.7 倍（vLLM 278 vs 104、SGLang 289 vs 111 tok/s）。两个引擎之间只差 10%~30%。
2. **系统提示词各不相同时，连会话内部的逐轮复用也失效**：unique 组命中率 vLLM 全为 0%，SGLang 仅并发 8 时 12.8%。8 个会话同时进行需要约 3.6~6.2 万 token 的 KV，超过约 3 万 token 的容量，一个会话的上一轮缓存在它下一轮到来前就被别的会话挤掉了。vLLM 日志在 unique c32 时显示「Running: 4 reqs, Waiting: 28 reqs, GPU KV cache usage 84%」。KV 容量决定了多轮对话的缓存能不能生效。
3. **引擎差别**：vLLM 低并发首字略快（c8 shared 第 2~6 轮 395 vs 656ms）；SGLang 命中率（88.5% vs 86.1%、72.3% vs 68.4%）和吞吐（各档高 4%~10%）略高，基数树在 KV 紧张时多保住一点缓存（unique c8 12.8% vs 0%）。这种以 prefill 为主的负载下，两者都受同一个 KV 容量限制。
4. 对 Agent 平台的含义：把系统提示词和工具定义做成所有用户共用的固定前缀（变化的内容放到后面），比换推理引擎更有效；多副本时还需要按前缀路由，否则同一前缀分散到各副本，命中率会下降（第 8 课讲解）。
