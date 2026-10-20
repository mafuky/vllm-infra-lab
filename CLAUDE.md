# vllm-infra-lab

在一张 RTX 4090（48G）上自建大模型推理平台：vLLM + K8s GPU 调度 + Prometheus/Grafana 监控 + LiteLLM 网关降级。作者mafuyu的个人项目。

## 当前状态（2026-10-15）

- 第二周设计已评审（双卡无货，扩缩容改为单张 48G 卡上两个副本），实施计划：`docs/superpowers/plans/2026-10-16-vllm-infra-lab-week2.md`，任务 1-5 在 Mac 上做，6-9 上 GPU。服务器上 k3s、三个模型和全部镜像（含 SGLang v0.5.20-cu130、KEDA 2.21.0，digest 已核对）都已就绪；系统盘已扩到 200GB；`.env` 三个密钥都已填好并同步到服务器。
- vLLM 镜像在国内公共镜像站拉取极不稳定（断连、层数据校验失败），已用断点续传逐层下载、导入并核对官方 digest；以后重装节点时注意预留时间。
- 实测 KV Cache 30,352 token（显存 0.45），与按每 token 144KB 推算的结果一致；并发 32 时运行 25、排队 7，拐点在 16 到 32 之间。

以下为 2026-10-13 的状态记录：

- 设计文档已获作者认可：`docs/superpowers/specs/2026-10-12-vllm-infra-lab-design.md`。
- 第一周实施计划已获认可，执行方式为在当前会话直接实现（executing-plans），最后派一个审查 Agent 整体检查：`docs/superpowers/plans/2026-10-12-vllm-infra-lab.md`。动手前先通读这两份文档，技术决定以它们为准；计划中对规格书的两处调整（成本按单请求比较、网关切换靠超时）以计划为准。
- GPU 实例已开通并完成检查（任务 7 Step 1-3）。任务 1-6 在 Mac 上完成，任务 8-13 由作者在 GPU 机器上执行。第 2 天按「先无卡模式装 k3s、拉镜像、下模型，再有卡模式装驱动」的顺序做（任务 8 Step 0），`setup-node.sh` 检测不到 GPU 时会自动跳过驱动。
- 项目已延长为两周。第二周设计文档：`docs/superpowers/specs/2026-10-13-vllm-infra-lab-week2-design.md`（SGLang 对比与 Agent 式负载、冷启动、KEDA 扩缩容、vLLM 源码笔记），2026-10-15 已评审。

## 已定决策（不要推翻，有异议先问作者）

- 一台优云智算华北二A RTX 4090 48G 单卡（14 核、96GB 内存，Ubuntu-nvidia 22.04 虚机，按量 3.17 元/小时；24G 的 4090、3090 和 5090 在 2026-10-13 均无货）上跑单节点 k3s，四个命名空间：gpu、infer、gateway、monitoring。实例用系统镜像 Ubuntu-nvidia 22.04 创建（虚机，`ubuntu` 用户、可 sudo，端口以详情页「登录指令」为准；出厂驱动 570.153.02 是用 `.run` 装的，`setup-node.sh` 负责升级到 580.178.04），按量付费；基础镜像和社区镜像创建的是容器（root、23 端口），装不了 k3s。
- 主模型 Qwen3-8B BF16，对照 Qwen3-8B-AWQ；模型从 ModelScope 下载到宿主机 `/data/models`。
- 监控直接写 Prometheus、Grafana 配置，不用 Prometheus Operator；告警不接通知渠道。
- 网关只用 LiteLLM 自带的超时和 fallback，不写自定义路由；fallback 到百炼的 qwen3.8-flash（百炼已不提供 qwen3-8b，2026-10-13 确认；北京地域每百万 token 输入 0.8 元、输出 2.7 元）。LiteLLM 的 `max_parallel_requests` 是信号量排队，不会触发 fallback（已查 v1.102.1 源码）。
- 进度落后时的砍需求顺序：time-slicing 实验 → 成本计算 → AWQ 对照。

## 约束

- 周期两周。第一周 GPU 约 80 元（超过 80 元停止可砍项）；两周合计不超过 150 元（超过 120 元停止所有可砍项）。不需要 GPU 的活放在无卡模式下做。
- 48G 卡用 `--gpu-memory-utilization 0.45` 模拟 24G 卡（KV Cache 约 2-3 万 token），另做 0.90 的不限显存对照。
- 开发机是 Mac（M4 Pro），只写配置、脚本、画图，不跑推理。
- 本机 kubectl 当前 context `k3s-sandbox` 是 sandbox-operator 用的远程 CPU 集群，不是本项目的 GPU 集群，不要往上部署本项目。

## 工作约定

- 学习方式沿用 `../sandbox-operator`：讲解写在每个配置文件和脚本的头部注释里，说明这一课回答什么问题、怎么执行、应该看到什么现象、背后的原理。动手前先看一眼那个仓库的写法。
- 作者亲手在 GPU 机器上执行每一课。你负责写代码和讲解，不要替作者编造或预测实测数据；README 里的每个数字都必须来自 `results/` 下的真实结果。
- 所有镜像和组件锁定具体版本号，不用 `latest`、`main-stable` 这类浮动标签。
- 密钥（百炼 API Key 等）只放 K8s Secret 和本地 `.env`，仓库只提交 `.env.example`。
- LiteLLM 配置以 `../agent-cost-loop/k8s/10-litellm-config.yaml` 和 `30-litellm.yaml` 为起点，那里是 mock 配置，需要改成真实后端。
- 和作者沟通用中文，先说结论，简洁。
