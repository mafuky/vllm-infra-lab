# vllm-infra-lab

在一张 RTX 4090（48G）上，用 k3s 搭一个自建大模型推理平台：GPU 调度、vLLM 推理服务、Prometheus/Grafana 监控告警、LiteLLM 网关超载降级到云端 API。
场景是一个已经在调百炼 API 的业务团队，想评估「自建 Qwen3-8B 能扛多少流量、什么时候该把流量切回云端、哪种用量下自建更便宜」。
每一课的讲解写在对应配置文件和脚本的头部注释里；本文的每个数字都来自 `results/` 下的实测文件。

## 架构

```
                          ┌───────────────────── k3s 单节点（RTX 4090 48G）─────────────────────┐
  客户端 / 压测 ──► LiteLLM 网关 ──► vLLM（Qwen3-8B BF16，显存 0.45 模拟 24G 卡）        namespace: infer
                   (gateway)   │      ▲ nvidia.com/gpu: 1（NVIDIA device plugin，namespace: gpu）
                               │      │
                   超时 11 秒   │      └── /metrics ──┐
                               ▼                     ▼
                     百炼 qwen3.8-flash          Prometheus ◄── DCGM Exporter（GPU 指标）
                     （fallback，云端）              │  三条告警规则                namespace: monitoring
                                                    └──► Grafana 看板（8 个面板）
                          └──────────────────────────────────────────────────────────────────────┘
```

## 版本

所有镜像锁定具体版本（`scripts/check_pins.py` 禁止 `latest` 和不带标签），2026-10-13 逐个确认存在且发布满两周。

| 组件 | 版本 |
| --- | --- |
| NVIDIA 驱动 | 580.178.04（官方 `.run`） |
| k3s | v1.35.8+k3s1 |
| NVIDIA Container Toolkit | 1.20.1-1 |
| NVIDIA device plugin | v0.20.1 |
| DCGM Exporter | 4.4.2-4.7.1-ubuntu22.04 |
| vLLM | v0.30.0（CUDA 13） |
| Prometheus / Grafana | v3.15.0 / 13.2.3 |
| LiteLLM | v1.102.1 |
| 模型 | Qwen3-8B（BF16）、Qwen3-8B-AWQ、Qwen3-1.7B，来自 ModelScope |

## 复现步骤

Mac 上 `make check` 跑完全部离线验证（pytest、镜像锁定检查、kubeconform、shellcheck、promtool 告警规则单测）。GPU 机器上按课执行：

| 课 | 内容 | 入口 |
| --- | --- | --- |
| 1 | 裸虚机装驱动、toolkit、k3s；整卡分配和 time-slicing | `scripts/setup-node.sh`、`scripts/doctor.sh`、`lessons/01-gpu-scheduling/` |
| 2 | 部署 vLLM，核对 KV Cache 容量和指标名 | `scripts/download-model.sh`、`lessons/02-vllm/` |
| 3 | Prometheus、DCGM、Grafana 和告警 | `scripts/create-secrets.sh`、`lessons/03-monitoring/` |
| 4 | 1~64 并发压测找拐点，调参对比 | `lessons/04-loadtest/`、`scripts/bench.sh`、`scripts/plot.py` |
| 5 | LiteLLM 超时降级、故障切换、成本 | `lessons/05-gateway/`、`scripts/gateway_probe.py`、`scripts/cost.py` |

## 结果

### 1. GPU 调度（`results/01-gpu/`）

- 整卡分配：节点上报 `nvidia.com/gpu: 1`，第二个申请整卡的 Pod 停在 Pending，调度器报 `Insufficient nvidia.com/gpu`（`pending.txt`）。
- time-slicing 把同一张卡报成 2 个。两个 Qwen3-1.7B 实例并发 16 同时压测：单实例吞吐从 984 降到 876/888 tok/s，P95 TTFT 从 443ms 升到 576/614ms；但两个合计约 1,764 tok/s，是单独跑的 1.8 倍（`ts-*.log`）。
- time-slicing 不隔离显存：两个实例都设 0.9 时没有崩溃，后完成显存测量的实例 KV Cache 只剩 8,512 token（先完成的拿到 309,696 token），两个 Pod 都显示 Ready（`overcommit-*.log`）。同时启动时还会互相干扰显存测量，导致一次启动失败（`startup-race-a.log`）。

### 2. KV Cache：估算与实测（`results/02-vllm/kv-capacity.txt`）

每 token 的 KV Cache = 36 层 × 2（K、V）× 8 个 KV 头 × 128 维 × 2 字节 = 147,456 字节 ≈ 144KB。
实测：显存 0.45 时，权重占 15.27 GiB，可用 KV Cache 4.17 GiB，vLLM 报告 30,352 token。4.17 GiB ÷ 144KB ≈ 30,365，与实测只差按 16 token 分块的取整。

### 3. 压测拐点和调参（`results/bench/`）

random 数据集，输入 1024、输出 256 token。基线（显存 0.45）：

| 并发 | 输出吞吐 tok/s | TTFT P95 | E2E P95 |
| --- | --- | --- | --- |
| 1 | 57 | 115ms | 4.5s |
| 16 | 592 | 947ms | 7.1s |
| 32 | 720 | 7,684ms | 16.0s |
| 64 | 734 | 17,441ms | 25.7s |

拐点是并发 16：再翻倍到 32，吞吐只多 22%，TTFT P95 涨 8.1 倍。30,352 token ÷ 每请求 1,280 token ≈ 23 个并发，与拐点一致。
9 轮压测共 12,260 个请求，0 失败。曲线：`results/charts/throughput.png`、`results/charts/ttft_p95.png`。

| 调参 | KV Cache | 关键现象 |
| --- | --- | --- |
| 显存 0.40 | 13,104 token | 并发 16 时 TTFT P95 已达 6.4 秒，吞吐封顶约 434 tok/s |
| 显存 0.90 | 185,568 token | 并发 128 吞吐仍在涨（1,187 tok/s），ITL P95 从 21ms 涨到 282ms：瓶颈从排队变成算力 |
| `--max-num-seqs 16` | 33,824 token | 吞吐封顶 591 tok/s，ITL P95 始终低于 25ms，多余请求线性排队 |
| 前缀缓存（512 token 共享前缀） | 30,352 token | 命中率 49.8%；并发 32 时 TTFT P95 1.0 秒 vs 关闭时 7.9 秒，吞吐 1,092 vs 707 tok/s，拐点推到 32 |
| AWQ 4 bit | 99,712 token | 单请求吞吐 138 vs 57 tok/s；并发 64 时 1,346 tok/s、TTFT P95 3.4 秒（未评估回答质量） |

完整表格 `results/bench/summary.md`，分析和测量问题说明 `results/bench/knee.md`。

### 4. 监控告警（`results/03-monitoring/`、`results/screenshots/`）

- Prometheus 按 Pod 注解自动发现 vLLM 和 DCGM，13 个预期指标全部存在。
- 三条告警：KV Cache > 90% 持续 1 分钟、排队 > 5 持续 1 分钟、TTFT P95 > 2 秒持续 2 分钟。压测期间三条都进入过 Firing，时间线见 `alerts-day5.txt`。
- 看板截图：`04-grafana-c32-knee.webp`（并发 32 时运行 25、排队 7、KV Cache 99%、TTFT P95 8.9 秒）。

### 5. 网关降级（`results/gateway/summary.md`）

| 场景 | 自建 | 百炼 | 错误 |
| --- | --- | --- | --- |
| 超载：300 个请求、并发 32 | 241 | 59 | 0 |
| vLLM 下线：40 个请求 | 0 | 40 | 0 |
| vLLM 恢复后：40 个请求 | 40 | 0 | 0 |

超时设为拐点并发 E2E P95 的 1.5 倍（11 秒）。转到百炼的请求先在自建上等满超时，用户等待 P50 14.2 秒、P95 20.3 秒。

### 6. 成本（`results/cost.md`）

按拐点并发的实测吞吐和 3.17 元/小时：自建每请求 0.00038 元（GPU 满载）、0.00127 元（利用率 30%），百炼 qwen3.8-flash 0.00151 元。每天超过约 5 万个请求，整卡全天租用比调用 API 便宜。两边不是同一个模型，且只算了 GPU 租金。

## 踩过的坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| vLLM Pod 反复重启，报 `VLLM_PORT ... appears to be a URI` | Service 叫 `vllm`，K8s 注入 `VLLM_PORT=tcp://IP:8000`，和 vLLM 自己读的环境变量撞名 | `enableServiceLinks: false`，加测试固定 |
| Pod 卡在 ContainerCreating | 公共镜像站能连上但不出数据；断点续传时某一段数据被替换，层的 sha256 校验失败 | 逐层断点续传、校验 sha256 后组装 OCI 包导入，并核对官方 manifest digest |
| 看板网关面板没有数据 | LiteLLM v1.102.1 默认 `/metrics` 也要 master key，Prometheus 拿到 401 | 只对指标端点关鉴权，不把管理员密钥交给 Prometheus |
| 随机负载的前缀缓存命中率 4%~10% | `vllm bench serve` 默认 `--seed 0`，各档并发提示词相同，跨档命中缓存 | 每档并发用不同 seed；影响范围写进 `knee.md` |
| 比例面板纵轴显示 0~10000% | percentunit 面板没固定上下限，无数据时被自动撑到 0~100 | 固定为 0~1，加测试 |
| 网关重启后 46/150 个请求 0.04 秒内失败 | 旧 Pod 退出窗口内 Service 仍有流量指向它 | 记为后续改进：preStop、优雅退出或多副本 |

## 已知边界

- vLLM 整个挂掉时指标消失，三条告警都不会触发（promtool 用例 `vllm_absent` 固定了这一行为）；靠 `scripts/doctor.sh` 和网关 fallback 兜底。没有流量时 TTFT 分位数是 NaN，告警同样不触发（用例 `no_traffic`）。
- time-slicing 不隔离显存和故障，只适合开发测试或利用率低的小模型。
- 告警用 `for: 1m`，KV Cache 占用率在 0.85~0.99 间来回摆动时会一直停在 Pending。
- 单节点、单卡，没有多副本和扩缩容（第二周计划用 KEDA 补）。
