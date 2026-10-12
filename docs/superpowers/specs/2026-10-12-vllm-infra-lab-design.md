# vllm-infra-lab 设计文档

- 日期：2026-10-12
- 状态：待评审
- 周期：一周（7 天，其中 5 天开 GPU）
- 预算：GPU 费用不超过 100 元

## 1. 目的

### 1.1 模拟的场景

公司目前调用百炼 API，账单持续上涨，需要评估「自己部署一个开源 Qwen 模型，替代一部分 API 调用」是否可行。本项目在一张 RTX 4090 上把这件事完整做一遍，回答五个问题：

| 问题 | 对应部分 | 产出 |
| --- | --- | --- |
| 模型怎么跑起来？ | vLLM 部署 Qwen3-8B | OpenAI 兼容的推理接口 |
| 一张卡能扛多少并发？ | 压测与调参 | 吞吐、延迟随并发变化的曲线，以及调参前后对比 |
| 多个模型怎么分 GPU？ | K8s GPU 调度、time-slicing | 调度配置、Pending 现象、共享一张卡的干扰数据 |
| 出问题怎么第一时间知道？ | Prometheus、Grafana、告警 | 看板，以及压测中真实触发的告警 |
| 扛不住怎么办？省不省钱？ | LiteLLM 网关切换、成本计算 | 超载切到百炼的演示，自建和 API 的成本对比结论 |

### 1.2 求职目标

补齐 AI Infra 岗位 JD 中最常见、而作者目前缺少的三项经验：推理服务、GPU 调度、监控告警。简历上的每一个结论都要有本项目的实测数据支撑，面试被追问时能讲出数据的来历。

### 1.3 成功标准

全部满足才算完成：

1. k3s 节点显示 `nvidia.com/gpu` 资源；超额申请 GPU 的 Pod 保持 Pending，事件中出现 `Insufficient nvidia.com/gpu`。有截图和命令输出。
2. vLLM 提供 OpenAI 兼容接口，`/v1/chat/completions` 调用成功。
3. 5 档并发（1、4、16、32、64）的压测结果表和曲线图：输出吞吐（token/s）、首字延迟 TTFT 的 P50/P95、每 token 延迟 ITL 的 P95。另有至少两个参数的调优对比。
4. Grafana 看板截图；三条告警在压测中真实进入 firing 状态，有截图。
5. 请求超过 vLLM 并发上限时，LiteLLM 把多出的请求转到百炼，有统计数据证明。
6. README 汇总所有结果，可按步骤复现；另附简历条目和面试问答。

以下三项属于第 6 节中可砍的内容，做完则写进 README，砍掉不影响完成判定：time-slicing 干扰数据、BF16 对 AWQ 对比、成本对比表和结论。

### 1.4 约束

- GPU：优云智算 GPU 云服务器，RTX 4090 24GB 单卡，按量或抢占式计费。前提是有 root 权限、能安装 Docker 和 k3s（第 1 天确认）。
- 开发机：Mac（M4 Pro，24GB 内存），只写配置、脚本和画图，不跑推理。
- 学习方式：沿用 sandbox-operator 的风格，讲解写在配置文件和脚本的头部注释里，每课一个目录。作者亲手在 GPU 机器上执行每一课，并记录现象和数据。

## 2. 不做的事

- 多节点、多卡、张量并行或流水线并行等分布式推理。
- 生产级高可用、Alertmanager 通知渠道、Prometheus Operator。
- 模型微调或训练。
- 网关的预算闸门、和 agent-cost-loop 的实时打通。成本只用压测数据算一次。
- 自定义路由逻辑。切换只用 LiteLLM 自带的并发上限和 fallback 功能。
- 节点污点、亲和性的实验。单节点上意义不大，只在讲解中提及。

## 3. 架构

```
Mac（开发机）
  │  SSH 端口转发：Grafana 3000、Prometheus 9090
  ▼
优云智算 GPU 云服务器（RTX 4090 24GB）
  └─ k3s 单节点
      ├─ gpu         NVIDIA 设备插件（含 time-slicing 配置）、DCGM Exporter
      ├─ infer       vLLM（Qwen3-8B）Deployment + Service
      ├─ gateway     LiteLLM Proxy：主路由 vLLM，fallback 百炼
      └─ monitoring  Prometheus、Grafana

  压测客户端：在 GPU 机器上运行，直连 vLLM 或经过 LiteLLM
  模型权重：宿主机数据盘 /data/models（从 ModelScope 下载一次）
```

所有组件跑在同一台机器上。压测客户端也在本机运行，避免公网延迟污染数据。

## 4. 组件设计

### 4.1 GPU 运行时与调度（命名空间 gpu）

**准备**

1. 确认镜像自带 NVIDIA 驱动：`nvidia-smi` 能看到 4090，记录驱动和 CUDA 版本。
2. 安装 NVIDIA Container Toolkit。
3. 安装 k3s。k3s 检测到 toolkit 后会自动配置 containerd 的 nvidia 运行时。确认集群中存在 `nvidia` RuntimeClass，没有就手动创建。所有 GPU Pod 都设置 `runtimeClassName: nvidia`。
4. 部署 NVIDIA k8s-device-plugin（DaemonSet），锁定版本号。

**实验 1：按整卡分配**

- 验证节点 `allocatable` 中出现 `nvidia.com/gpu: 1`。
- vLLM 申请 `nvidia.com/gpu: 1`，正常调度。
- 再创建一个同样申请 1 张卡的测试 Pod，观察它保持 Pending，`kubectl describe` 中出现 `Insufficient nvidia.com/gpu`。
- 讲解点：扩展资源只能按整数申请，request 必须等于 limit，不能超卖；调度器在 Filter 阶段排除了这个节点。

**实验 2：time-slicing 共享一张卡**

- 设备插件配置 `timeSlicing.resources[].replicas: 2`，节点资源变为 `nvidia.com/gpu: 2`。
- 部署两个 Qwen3-1.7B 实例，各设 `--gpu-memory-utilization 0.4`，同时运行在这张卡上。
- 测量：单实例单独压测，对比两个实例同时压测，记录 TTFT 和吞吐的变化。
- 故意失败一次：两个实例都设 0.9，第二个启动时显存不足退出。保存日志。
- 讲解点：time-slicing 只分时间片，不隔离显存和故障域；对比 MPS、MIG 的隔离程度和适用场景（4090 不支持 MIG）。另外，DCGM 在 time-slicing 下无法把 GPU 利用率拆分到每个 Pod。

实验 2 是进度落后时第一个砍掉的内容。

### 4.2 推理服务（命名空间 infer）

**模型**

- 主模型：`Qwen/Qwen3-8B`，BF16，权重约 16GB。
- 对照模型：`Qwen/Qwen3-8B-AWQ`，4bit 量化，权重约 6GB。
- 都从 ModelScope 下载到 `/data/models`，Pod 通过 hostPath 只读挂载。

**KV Cache 容量估算**（压测前写进讲解，压测后用 vLLM 启动日志中的实际 KV Cache 容量核对）

- 每 token 的 KV Cache：2（K 和 V）× 36 层 × 8 个 KV 头 × 128 维 × 2 字节 = 147,456 字节，约 144KB。
- 显存占比 0.9 时，约 21.6GB 可用，扣掉 16GB 权重和约 2GB 的激活、CUDA Graph 开销，剩 3-4GB 给 KV Cache，约 2-3 万个 token。
- 推论：每个请求约 1,300 个 token（输入 1024 加输出 256）时，同时能服务约 20 个请求，超过就会排队。压测要验证这个拐点。

**启动参数与调优实验**

| 参数 | 基线值 | 对比实验 |
| --- | --- | --- |
| `--max-model-len` | 8192 | 不做对比，只解释为什么不用默认的长上下文 |
| `--gpu-memory-utilization` | 0.90 | 对比 0.85，看 KV Cache 容量和吞吐的变化 |
| `--max-num-seqs` | 默认值 | 对比 16、32、64 |
| 前缀缓存 | 开启 | 共享系统提示词的场景下，对比开和关的 TTFT |
| 量化 | BF16 | 对比 AWQ，看高并发下的吞吐和延迟 |

**Deployment 要点**

- `resources.limits: nvidia.com/gpu: 1`，`runtimeClassName: nvidia`。
- `/dev/shm` 挂载内存型 emptyDir。
- 启动探针给足时间（模型加载需要一两分钟），就绪探针请求 `/health`。
- 镜像使用 `vllm/vllm-openai`，锁定具体版本号。第 1 天选一个发布满两周的稳定版本。

### 4.3 压测

- 工具：`vllm bench serve`，和服务端用同一个镜像版本，通过 Job 或 `kubectl exec` 在集群内运行。
- 数据集：random，输入 1024 token，输出 256 token。走 completions 接口，不经过聊天模板，避免 Qwen3 思考模式干扰结果。
- 并发：1、4、16、32、64，每档请求数不少于并发数的 10 倍，且不少于 50 个。
- 记录：输出吞吐，TTFT 的 P50 和 P95，ITL 的 P95，以及失败数。原始 JSON 存入 `results/`。
- 画图：Mac 上用 Python（matplotlib）读取 JSON，生成吞吐和延迟随并发变化的曲线 PNG。
- 每天结束前用 rsync 把 `results/` 拉回 Mac，防止实例回收导致数据丢失。

### 4.4 监控与告警（命名空间 monitoring）

**部署**：直接写 Prometheus 和 Grafana 的 Deployment、ConfigMap，不用 Operator。Prometheus 通过 `kubernetes_sd` 加 Pod 注解（`prometheus.io/scrape`、`prometheus.io/port`）发现采集目标。数据保留 7 天，存储在宿主机目录。

**采集目标**

- vLLM `/metrics`：运行中和排队中的请求数、KV Cache 占用率、TTFT 直方图、ITL 直方图、prompt 和生成 token 计数、前缀缓存命中。
- DCGM Exporter：GPU 利用率、显存占用、温度、功耗。
- LiteLLM：按后端统计的请求数。如果所用版本的 Prometheus 指标属于企业版功能，就改为从压测客户端的结果中统计。

**Grafana 看板**（JSON 预置，按排查顺序从上到下排列）

1. 用户体验：TTFT P50/P95、ITL P95
2. 吞吐：每秒生成 token 数
3. 容量：运行中和排队中的请求数、KV Cache 占用率
4. 硬件：GPU 利用率、显存占用
5. 优化效果：前缀缓存命中率
6. 网关：自建和百炼各自承接的请求数

**告警规则**

| 名称 | 条件 | 类型 |
| --- | --- | --- |
| KVCacheNearFull | KV Cache 占用率 > 90%，持续 1 分钟 | 先兆 |
| RequestQueueBacklog | 排队请求数 > 5，持续 1 分钟 | 先兆，也是网关切换的信号 |
| TTFTP95High | TTFT P95 > 2 秒，持续 2 分钟 | 症状 |

原则：告警只盯用户能感受到的症状和能提前预判的先兆，不对 GPU 利用率高告警。

注意：vLLM 不同版本的指标名有变化。第 3 天部署后先 `curl /metrics` 核对实际名称，再确定查询语句。

### 4.5 网关与成本（命名空间 gateway）

**LiteLLM 配置**

- 以 agent-cost-loop 中 `k8s/10-litellm-config.yaml` 和 `k8s/30-litellm.yaml` 为起点。原配置使用 mock 模型和未锁定的 `main-stable` 镜像，本项目改为真实后端，并锁定镜像版本。
- 对外暴露一个模型名 `qwen3-8b`：
  - 主部署：自建 vLLM，`api_base` 指向 `http://vllm.infer:8000/v1`，设置 `max_parallel_requests`，取值为第 5 天压测得出的拐点并发数；设置请求超时。
  - fallback：百炼的同款 Qwen3-8B（OpenAI 兼容端点）。第 1 天确认百炼提供该模型及其价格，没有就用最接近的型号，并在结果中注明。
- 百炼 API Key 存在 K8s Secret 中，仓库只放 `.env.example`，`.gitignore` 排除真实配置。

**演示两种切换**

1. 超载：经网关以高于并发上限的并发压测，多出的请求转到百炼。
2. 故障：把 vLLM 缩容到 0，所有请求转到百炼；恢复后流量回到自建。

每个响应要能区分实际后端（LiteLLM 响应头或 `response.model` 字段，第 6 天确认具体方式），统计两边各承接了多少请求。

**成本计算**（脚本 `scripts/cost.py`，输入压测 JSON 和价格参数）

- 自建每百万 token 成本 = GPU 小时价 ÷（实测饱和吞吐 token/s × 3600）× 1,000,000。
- 分别按 100% 和 30% 的 GPU 利用率计算，后者更接近真实业务。
- API 成本 = 百炼该模型的每百万 token 输入、输出单价（第 6 天从官方价格页获取，注明日期）。
- 盈亏平衡点 = GPU 每日成本 ÷ API 每 token 单价，即每天调用多少 token 以上自建更便宜。
- 产出一张对比表和一句结论。

## 5. 仓库结构

```
vllm-infra-lab/
├── README.md                 结果汇总、复现步骤、简历条目、面试问答
├── lessons/
│   ├── 01-gpu-scheduling/    设备插件、Pending 演示、time-slicing
│   ├── 02-vllm/              vLLM Deployment、AWQ 对照
│   ├── 03-monitoring/        Prometheus、Grafana、DCGM、告警规则
│   ├── 04-loadtest/          压测 Job、调参记录
│   └── 05-gateway/           LiteLLM 配置、切换演示
├── scripts/
│   ├── doctor.sh             环境自检：驱动、toolkit、k3s、GPU 资源、各组件就绪
│   ├── setup-node.sh         安装 toolkit 和 k3s
│   ├── download-model.sh     从 ModelScope 下载模型
│   ├── bench.sh              按档位跑压测，结果写入 results/
│   ├── plot.py               画曲线
│   └── cost.py               成本计算
├── results/                  压测 JSON、曲线图、截图
└── docs/superpowers/specs/   本设计文档
```

每课目录下的配置文件，头部注释写清楚：这一课要回答什么问题、怎么执行、应该看到什么现象、背后的原理。

## 6. 一周计划

| 天 | 内容 | GPU |
| --- | --- | --- |
| 1 | Mac 上写完全部配置和脚本；下单 GPU，确认 root 权限和 Docker；锁定各组件版本号；查百炼价格 | 否（确认实例时短暂开机） |
| 2 | 第 1 课：toolkit、k3s、设备插件，实验 1 和实验 2 | 约 4-5 小时 |
| 3 | 第 2 课：下载模型，部署 vLLM，打通接口，核对指标名 | 约 3-4 小时 |
| 4 | 第 3 课：Prometheus、Grafana、DCGM、告警规则 | 约 3-4 小时 |
| 5 | 第 4 课：基线压测，调参对比，AWQ 对照，告警触发截图 | 约 6 小时 |
| 6 | 第 5 课：LiteLLM 网关，两种切换演示，成本计算 | 约 4 小时 |
| 7 | 整理 README、曲线、截图，写简历条目和面试问答 | 否 |

预计约 25 个 GPU 小时，按 1.3-1.9 元/小时计算，约 35-50 元，预算有余量。

**进度落后时的砍需求顺序**

1. time-slicing 实验（第 1 课实验 2）
2. 成本计算（保留切换演示）
3. AWQ 对照（保留参数调优）

基线部署、压测、监控告警、超载切换四项不砍。

## 7. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 优云智算实例是容器，没有 root 或不能装 Docker | 已确认（2026-10-13，平台答复）：系统镜像（Ubuntu、Ubuntu-nvidia）创建的是虚机，以 `ubuntu` 用户、22 端口登录，可 sudo，支持装 Docker 和 k3s；基础镜像、社区镜像创建的是容器（root、23 端口），不适用。开实例时选 Ubuntu-nvidia 系统镜像 |
| 4090 缺货，或抢占式实例被回收 | 改用按量计费；每天结束前同步 `results/` 回 Mac |
| 模型下载慢 | 使用 ModelScope；模型放数据盘，关机不删盘 |
| vLLM 指标名与预期不符 | 第 3 天核对后再写查询语句 |
| LiteLLM 的 Prometheus 指标是企业版功能 | 改为从压测客户端结果统计各后端请求数 |
| 预算超支 | 每天记录 GPU 时长；累计超过 80 元时停止追加实验 |
| 密钥泄露 | 百炼 Key 只放 K8s Secret 和本地 `.env`，仓库只提交 `.env.example` |

## 8. 第 1 天需要确认的事项

1. 优云智算实例类型：是否有 root，能否安装 Docker 和 k3s；关机后是否还收费。
2. 驱动版本和 CUDA 版本，是否满足所选 vLLM 版本的要求。
3. 锁定版本号：vLLM 镜像、NVIDIA device plugin、DCGM Exporter、Prometheus、Grafana、LiteLLM。
4. 百炼是否提供 Qwen3-8B，以及每百万 token 的输入和输出价格。
