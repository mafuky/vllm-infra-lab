# vllm-infra-lab

在一张 RTX 4090 上自建大模型推理平台：vLLM + K8s GPU 调度 + Prometheus/Grafana 监控 + LiteLLM 网关降级。作者黄志鹏的个人项目，用来给简历补 AI Infra 经验。

## 当前状态（2026-10-12）

- 设计文档已完成，等待作者评审：`docs/superpowers/specs/2026-10-12-vllm-infra-lab-design.md`。动手前先通读这份文档，所有技术决定以它为准。
- 还没有任何代码。
- 下一步：作者确认设计文档后，用 writing-plans 写实施计划（按天拆成步骤和验收命令），再开始第 1 天的工作。

## 背景

- 作者 3 年 Java/Vue 全栈，2026 年起做 AI Agent 研发（事实追溯 Agent、研报生成 Agent 平台、沙箱、Token 成本统计）。
- 目标岗位：Agent 开发负责人或 AI Infra。简历的短板是推理服务、GPU 调度、监控告警，本项目专门补这三块。
- 项目完成后，要把实测结果写进简历。简历是一份 Claude 在线文档：https://claude.ai/code/artifact/02010e12-4e49-4c4e-bf56-bf5c1d9b4b1a 。简历含电话、邮箱，不要复制进本仓库。

## 已定决策（不要推翻，有异议先问作者）

- 一台优云智算 GPU 云服务器（RTX 4090 24GB）上跑单节点 k3s，四个命名空间：gpu、infer、gateway、monitoring。
- 主模型 Qwen3-8B BF16，对照 Qwen3-8B-AWQ；模型从 ModelScope 下载到宿主机 `/data/models`。
- 监控直接写 Prometheus、Grafana 配置，不用 Prometheus Operator；告警不接通知渠道。
- 网关只用 LiteLLM 自带的并发上限和 fallback，不写自定义路由；fallback 到百炼的同款模型。
- 进度落后时的砍需求顺序：time-slicing 实验 → 成本计算 → AWQ 对照。

## 约束

- 周期一周，GPU 预算 100 元，累计超过 80 元时停止追加实验。
- 开发机是 Mac（M4 Pro），只写配置、脚本、画图，不跑推理。
- 本机 kubectl 当前 context `k3s-sandbox` 是 sandbox-operator 用的远程 CPU 集群，不是本项目的 GPU 集群，不要往上部署本项目。

## 工作约定

- 学习方式沿用 `../sandbox-operator`：讲解写在每个配置文件和脚本的头部注释里，说明这一课回答什么问题、怎么执行、应该看到什么现象、背后的原理。动手前先看一眼那个仓库的写法。
- 作者亲手在 GPU 机器上执行每一课。你负责写代码和讲解，不要替作者编造或预测实测数据；README 和简历里的每个数字都必须来自 `results/` 下的真实结果。
- 所有镜像和组件锁定具体版本号，不用 `latest`、`main-stable` 这类浮动标签。
- 密钥（百炼 API Key 等）只放 K8s Secret 和本地 `.env`，仓库只提交 `.env.example`。
- LiteLLM 配置以 `../agent-cost-loop/k8s/10-litellm-config.yaml` 和 `30-litellm.yaml` 为起点，那里是 mock 配置，需要改成真实后端。
- 和作者沟通用中文，先说结论，简洁。
