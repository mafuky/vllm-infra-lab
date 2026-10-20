# 第 4 天 · 并发 32 时看板和告警的状态（2026-10-15 16:40 前后）

用于截图的一轮负载，不进入第 5 天的正式压测数据：random 数据集，输入 1024、输出 256，`--ignore-eos`，800 个请求，最大并发 32，显存 0.45（KV Cache 30,352 token）。

## 负载中途，看板各面板的 PromQL 即时值

| 面板 | 并发 16（同日早些时候） | 并发 32 |
| --- | --- | --- |
| TTFT P50 / P95 | 0.48s / 0.82s | 1.96s / 8.87s |
| ITL P95 | 25ms | 68ms |
| 生成吞吐 | 592 tok/s | 746 tok/s |
| 运行中 / 排队中 | 16 / 0 | 25 / 7 |
| KV Cache 占用率 | 63.7% | 99% |
| GPU 利用率 | 100% | 100% |

## 客户端汇总（vllm bench serve）

成功 800、失败 0；输出吞吐 726 tok/s；TTFT 中位数 1505ms、P99 7781ms；TPOT 中位数 32ms。

## 告警

- `TTFTP95High`（P95 > 2s 持续 2m）进入 Firing。
- `KVCacheNearFull`（> 0.9 持续 1m）停在 Pending：占用率在 0.85~0.99 之间来回摆动（请求完成就释放块），没有连续 1 分钟都高于 0.9。
- `RequestQueueBacklog`（排队 > 5 持续 1m）同样在 5 上下摆动，没有触发。

结论：并发从 16 翻到 32，吞吐只多 26%，TTFT P95 涨了约 11 倍。拐点在 16 到 32 之间，和按每 token 144KB 算出的约 20 个并发一致。
摆动的指标配 `for: 1m` 会一直卡在 Pending，第 5 天压到 64 并发时再看这两条能否触发；如果仍不触发，考虑改用 `avg_over_time(...[1m]) > 阈值`。

截图：`results/screenshots/04-grafana-c32-knee.webp`、`04-prometheus-targets.webp`、`04-prometheus-alert-rules.webp`。
