# vllm-infra-lab 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一张 RTX 4090（48G）上搭出单节点 k3s 推理平台（GPU 调度、vLLM、监控告警、LiteLLM 降级），每课带讲解，产出可写进简历的实测数据。

**Architecture:** 任务 1-6 在 Mac 上写全部配置、脚本和 Python 工具，用 pytest、promtool、kubeconform、shellcheck 离线验证。任务 7-13 由作者在 GPU 机器上逐课执行，Claude 协助排障，结果写入 `results/`。所有 K8s 资源是手写 YAML，按课放在 `lessons/` 下。

**Tech Stack:** k3s、NVIDIA device plugin、DCGM Exporter、vLLM、Prometheus、Grafana、LiteLLM、Python 3（pytest、httpx、matplotlib）、Bash。

**Spec:** `docs/superpowers/specs/2026-10-12-vllm-infra-lab-design.md`

## Global Constraints

- 第一周完成；第一周 GPU 费用约 80 元，超过 80 元停止可砍项；两周合计不超过 150 元。装软件、下模型、拉镜像等不需要 GPU 的活放在无卡模式（0.15 元/小时）下做。
- 硬件：优云智算上海 RTX 4090 48G 单卡（14 核、96GB 内存，Ubuntu-nvidia 22.04 虚机，按量 3.17 元/小时，出厂驱动 570.153.02，系统盘可用 83GB）；开发机 Mac 只写配置、脚本、画图。
- 命名空间固定为 `gpu`、`infer`、`gateway`、`monitoring`；模型目录固定为宿主机 `/data/models`，从 ModelScope 下载。
- 主模型 `Qwen/Qwen3-8B`（BF16），对照 `Qwen/Qwen3-8B-AWQ`；time-slicing 实验用 `Qwen/Qwen3-1.7B`。
- vLLM 基线参数：`--max-model-len 8192`、`--gpu-memory-utilization 0.45`（48G × 0.45 ≈ 21.6GB，等同 24G 卡跑 0.9）、开启前缀缓存。另做一组 0.90 的不限显存对照。
- 压测：random 数据集，输入 1024、输出 256 token；并发 1、4、16、32、64；每档请求数 = max(10 × 并发, 50)。
- 告警阈值：KV Cache 占用 > 0.9 持续 1m；排队请求 > 5 持续 1m；TTFT P95 > 2s 持续 2m。
- 所有镜像锁定具体版本号，禁止 `latest`、`main-stable`、不带标签。
- 密钥只放 K8s Secret 和本地 `.env`，仓库只提交 `.env.example`。
- README 和简历中的每个数字都必须来自 `results/` 下的真实结果，不得编造或预测。
- 每个 YAML 和脚本的头部注释按 `../sandbox-operator/internal/controller/sandbox_controller.go` 的写法：标题「第 N 课 · <标题>」、预计时长，再分「── 你在做什么 ──」「── 为什么 ──」「── 怎么执行 ──」「── 应该看到什么 ──」几节，用真实工作场景讲原理。中文。
- 版本号（2026-10-13 已逐个探测存在，且发布满两周；任务 1 仍用 skopeo 复核；不存在时换成同系列最近一个发布满两周的稳定版，并同步改 README 版本表）：

| 组件 | 版本 |
| --- | --- |
| NVIDIA 驱动 | `580.178.04`（官方 `.run`，2026-07-13 发布，580 长期支持分支） |
| k3s | `v1.35.8+k3s1` |
| NVIDIA Container Toolkit | `1.20.1-1` |
| NVIDIA device plugin | `nvcr.io/nvidia/k8s-device-plugin:v0.20.1` |
| DCGM Exporter | `nvcr.io/nvidia/k8s/dcgm-exporter:4.4.2-4.7.1-ubuntu22.04` |
| vLLM | `vllm/vllm-openai:v0.30.0`（默认 CUDA 13，需驱动 580 以上） |
| Prometheus | `prom/prometheus:v3.15.0` |
| Grafana | `grafana/grafana:13.2.3` |
| LiteLLM | `ghcr.io/berriai/litellm:v1.102.1`（新版镜像标签不再带 -stable） |
| CUDA 测试镜像 | `nvidia/cuda:12.4.1-base-ubuntu22.04` |

## Review Focus

1. **压测有失败请求**：`vllm bench serve` 结果里 `completed` < `num_prompts` 时，汇总表必须显示失败数，否则吞吐看起来正常但其实丢了请求。由任务 5 的 `test_failed_requests_are_reported` 覆盖。
2. **网关响应缺少后端标识**：所有后端都失败返回错误，或响应头没有 `x-litellm-model-id` 时，探测脚本要计入 `error` 或 `unknown`，不能崩溃。由任务 6 的 `test_summarize_counts_errors_and_unknown` 覆盖。
3. **成本计算的除零**：吞吐为 0 或利用率为 0 时要报清楚的错误，不能返回 `inf`。由任务 6 的 `test_rejects_zero_throughput` 覆盖。
4. **隐式 latest 镜像**：`image: nginx` 这种不写标签的写法等于 latest，必须被检出；带 `@sha256:` 摘要的写法算锁定。由任务 1 的 `test_untagged_image_is_floating` 覆盖。
5. **零流量和服务挂掉时的告警**：没有请求时 TTFT 分位数是 NaN，vLLM 挂掉时指标消失，三条告警都不应触发。这是已知边界（服务挂掉靠 doctor 和网关 fallback 兜底），用测试固定下来并写进 README。由任务 4 的 promtool 用例 `no_traffic` 和 `vllm_absent` 覆盖。

---

## 文件结构

```
vllm-infra-lab/
├── Makefile                        make check：一条命令跑完全部离线验证
├── .gitignore  .env.example  requirements-dev.txt
├── lessons/
│   ├── 00-namespaces.yaml
│   ├── 01-gpu-scheduling/          registries.yaml、runtimeclass.yaml、device-plugin*.yaml、gpu-hog-*.yaml、timeslice-vllm.yaml
│   ├── 02-vllm/                    vllm-qwen3-8b.yaml、vllm-qwen3-8b-awq.yaml
│   ├── 03-monitoring/              prometheus.yaml、dcgm-exporter.yaml、grafana.yaml、rules/、dashboards/、expected-metrics.txt
│   ├── 04-loadtest/                bench-client.yaml
│   └── 05-gateway/                 litellm-config.yaml、litellm.yaml
├── scripts/
│   ├── check_pins.py  check-images.sh                  任务 1
│   ├── setup-node.sh  doctor.sh                         任务 2
│   ├── download-model.sh  check_metrics.py              任务 3
│   ├── create-secrets.sh                                任务 4
│   ├── bench.sh  benchlib.py  plot.py                   任务 5
│   └── gateway_probe.py  cost.py                        任务 6
├── tests/                          pytest 用例和 fixtures/
└── results/                        bench/<label>/c<N>.json、gateway/、charts/、screenshots/、01-gpu/ 等
```

GPU 机器上的约定路径：仓库同步到 `/data/lab`，模型在 `/data/models`，压测结果写 `/data/results`（即仓库 `results/` 的来源），Prometheus 数据在 `/data/prometheus`。

---

### Task 1: 开发工具、仓库骨架和镜像锁定检查

**Files:**
- Create: `Makefile`、`.gitignore`、`.env.example`、`requirements-dev.txt`、`lessons/00-namespaces.yaml`
- Create: `scripts/check_pins.py`、`scripts/check-images.sh`
- Test: `tests/test_check_pins.py`

**Interfaces:**
- Produces: `find_floating_images(yaml_text: str) -> list[str]`，返回所有未锁定的镜像引用；CLI `python3 scripts/check_pins.py <file>...`，有未锁定镜像时打印并退出码 1。
- Produces: `make check`，后续任务往里追加检查项。

- [ ] **Step 1: 安装 Mac 开发工具并建虚拟环境**

Run: `brew install kubeconform shellcheck prometheus skopeo && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
`requirements-dev.txt` 写 `pytest`、`httpx`、`matplotlib`，各锁一个当前已发布满两周的具体版本（`==`）。
Expected: `kubeconform -v`、`shellcheck -V`、`promtool --version`、`skopeo -v`、`.venv/bin/pytest --version` 都有输出。

- [ ] **Step 2: 写失败的测试**

```python
from scripts.check_pins import find_floating_images

def test_latest_is_floating():
    assert find_floating_images("image: vllm/vllm-openai:latest") == ["vllm/vllm-openai:latest"]

def test_main_stable_is_floating():
    assert find_floating_images("image: ghcr.io/berriai/litellm:main-stable") == ["ghcr.io/berriai/litellm:main-stable"]

def test_untagged_image_is_floating():
    assert find_floating_images("      image: nginx") == ["nginx"]

def test_registry_port_is_not_a_tag():
    assert find_floating_images("image: localhost:5000/foo") == ["localhost:5000/foo"]

def test_pinned_and_digest_pass():
    text = "image: prom/prometheus:v3.15.0\n  image: foo@sha256:" + "a" * 64
    assert find_floating_images(text) == []
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `.venv/bin/pytest tests/test_check_pins.py -v`
Expected: FAIL，`ModuleNotFoundError: scripts.check_pins`（`scripts/__init__.py` 和 `tests/conftest.py` 也在本步建好，使 `scripts` 可导入）。

- [ ] **Step 4: 实现 `find_floating_images` 和 CLI**

按行匹配 `image:` 后的引用（去掉引号）；有 `@sha256:` 视为锁定；否则取最后一个 `/` 之后的部分判断有无 `:tag`，tag 为 `latest` 或含 `stable` 但不含数字版本（如 `main-stable`）视为浮动。

- [ ] **Step 5: 运行测试，确认通过**

Run: `.venv/bin/pytest tests/test_check_pins.py -v`
Expected: 5 passed。

- [ ] **Step 6: 写骨架文件和 `make check`**

- `.gitignore`：`.env`、`.venv/`、`*.secret.yaml`、`__pycache__/`。
- `.env.example`：`DASHSCOPE_API_KEY=`、`LITELLM_MASTER_KEY=`、`GRAFANA_ADMIN_PASSWORD=`、`GPU_HOST=`（ssh 别名）。
- `lessons/00-namespaces.yaml`：四个 Namespace。
- `scripts/check-images.sh`：从 `lessons/**/*.yaml` 提取 `image:`，逐个执行 `skopeo inspect --raw docker://<ref> >/dev/null`，汇报不存在的镜像，任一失败退出码 1。
- `Makefile` 的 `check` 目标依次跑：`pytest`、`python3 scripts/check_pins.py $(shell find lessons -name '*.yaml')`、`kubeconform -strict -summary -kubernetes-version 1.35.0 -ignore-filename-pattern 'rules|dashboards|registries' lessons`、`shellcheck scripts/*.sh`。

Run: `make check`
Expected: 全部通过。

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "chore: 搭建仓库骨架和镜像锁定检查"
```

### Task 2: 第 1 课配置——节点安装与 GPU 调度

**Files:**
- Create: `scripts/setup-node.sh`、`scripts/doctor.sh`
- Create: `lessons/01-gpu-scheduling/registries.yaml`、`runtimeclass.yaml`、`device-plugin.yaml`、`device-plugin-config-exclusive.yaml`、`device-plugin-config-timeslice.yaml`、`gpu-hog-a.yaml`、`gpu-hog-b.yaml`、`timeslice-vllm.yaml`

**Interfaces:**
- Produces: `sudo scripts/setup-node.sh`（幂等）；`scripts/doctor.sh`（只读，按已部署的命名空间逐项检查，每项输出 ✅ 或 ❌ 加修复提示）。
- Produces: 两份同名 ConfigMap `nvidia-device-plugin-config`（键 `config.yaml`），二选一 apply 后重启 DaemonSet 生效。

- [ ] **Step 1: `setup-node.sh`**

优云智算虚机以 `ubuntu` 用户登录，脚本用 `sudo` 运行。依次：检查以 root 运行且 `SUDO_USER` 非空；`nvidia-smi` 不可用或驱动低于 580 时：apt 安装 `build-essential` 和 `linux-headers-$(uname -r)`，从 `https://download.nvidia.com/XFree86/Linux-x86_64/580.178.04/` 下载 `NVIDIA-Linux-x86_64-580.178.04.run` 静默安装（出厂的 570 驱动是 `.run` 装的，不能混用 apt 的驱动包；`.run` 会自动卸载旧版），提示重启后重新运行本脚本并退出；驱动 ≥ 580 时打印版本继续；按 NVIDIA 官方 apt 源安装 `nvidia-container-toolkit=1.20.1-1`；把 `registries.yaml` 复制到 `/etc/rancher/k3s/registries.yaml`；用 rancher 国内镜像安装脚本装 k3s（`INSTALL_K3S_MIRROR=cn INSTALL_K3S_VERSION=v1.35.8+k3s1`）；创建 `/data/models`、`/data/results`、`/data/lab`（属主 `SUDO_USER`）和 `/data/prometheus`（属主 65534）；把 `/etc/rancher/k3s/k3s.yaml` 复制到 `/home/$SUDO_USER/.kube/config` 并改属主，之后 `ubuntu` 用户不加 sudo 也能用 kubectl。

- [ ] **Step 2: `registries.yaml` 和 `runtimeclass.yaml`**

`registries.yaml` 给 `docker.io`、`ghcr.io`、`nvcr.io` 配 DaoCloud 镜像端点（`https://docker.m.daocloud.io`、`https://ghcr.m.daocloud.io`、`https://nvcr.m.daocloud.io`），保留原站作回退。`runtimeclass.yaml` 定义 `handler: nvidia` 的 RuntimeClass `nvidia`（k3s 已自动创建时 apply 无副作用）。

- [ ] **Step 3: 设备插件**

`device-plugin.yaml`：命名空间 `gpu` 的 DaemonSet，`runtimeClassName: nvidia`（k3s 上不设会找不到 NVML，讲解里写明这个坑），挂载 `/var/lib/kubelet/device-plugins` 和 ConfigMap，环境变量 `CONFIG_FILE=/config/config.yaml`。
`device-plugin-config-exclusive.yaml` 内容为 `version: v1`；`device-plugin-config-timeslice.yaml` 为 `sharing.timeSlicing.resources: [{name: nvidia.com/gpu, replicas: 2}]`。

- [ ] **Step 4: 实验用 Pod**

`gpu-hog-a.yaml`、`gpu-hog-b.yaml`：命名空间 `infer` 的 Pod，CUDA 测试镜像，`runtimeClassName: nvidia`，`limits: nvidia.com/gpu: 1`，命令 `nvidia-smi -L && sleep infinity`。讲解：b 会 Pending 及原因。
`timeslice-vllm.yaml`：两个 Deployment `vllm-small-a`、`vllm-small-b`（各带同名 Service，端口 8000），模型 `/models/Qwen3-1.7B`，`--served-model-name qwen3-1.7b --gpu-memory-utilization 0.4 --max-model-len 4096`，各申请 1 个 `nvidia.com/gpu`，`strategy: Recreate`。讲解里给出故意 OOM 的命令（`kubectl -n infer patch` 把两个实例改成 0.9），并对比 time-slicing、MPS、MIG 三种共享方式的隔离程度和适用场景（4090 不支持 MIG）。

- [ ] **Step 5: `doctor.sh`**

检查项：`nvidia-smi`、`nvidia-ctk --version`、`systemctl is-active k3s`、RuntimeClass `nvidia` 存在、节点 `allocatable` 中 `nvidia.com/gpu` 的数量、`/data/models` 下的模型目录；对存在的命名空间列出未就绪的 Pod，状态为 `ImagePullBackOff` 或 `ErrImagePull` 时提示检查 `registries.yaml`。

- [ ] **Step 6: 验证并提交**

Run: `make check`
Expected: kubeconform、shellcheck、check_pins 全部通过。

```bash
git add -A && git commit -m "feat: 第 1 课 节点安装与 GPU 调度配置"
```

### Task 3: 第 2 课配置——vLLM 部署和指标核对工具

**Files:**
- Create: `scripts/download-model.sh`、`lessons/02-vllm/vllm-qwen3-8b.yaml`、`lessons/02-vllm/vllm-qwen3-8b-awq.yaml`
- Create: `scripts/check_metrics.py`、`lessons/03-monitoring/expected-metrics.txt`
- Test: `tests/test_check_metrics.py`

**Interfaces:**
- Produces: Service `vllm.infer:8000`，模型名 `qwen3-8b`（两个 YAML 同名资源，二选一 apply）。
- Produces: `missing_metrics(metrics_text: str, expected: list[str]) -> list[str]`；CLI `python3 scripts/check_metrics.py <metrics.txt> <expected.txt>`，有缺失时退出码 1。
- Produces: `expected-metrics.txt`，任务 4 的看板和告警只能引用其中的指标。

- [ ] **Step 1: 写失败的测试**

```python
from scripts.check_metrics import missing_metrics

SAMPLE = """# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{le="0.1",model_name="qwen3-8b"} 3
vllm:time_to_first_token_seconds_count{model_name="qwen3-8b"} 3
vllm:generation_tokens_total{model_name="qwen3-8b"} 768
vllm:kv_cache_usage_perc{model_name="qwen3-8b"} 0.12
"""

def test_histogram_base_name_matches_bucket_lines():
    assert missing_metrics(SAMPLE, ["vllm:time_to_first_token_seconds"]) == []

def test_counter_matched_by_total_name():
    assert missing_metrics(SAMPLE, ["vllm:generation_tokens_total"]) == []

def test_reports_renamed_metric():
    assert missing_metrics(SAMPLE, ["vllm:gpu_cache_usage_perc"]) == ["vllm:gpu_cache_usage_perc"]

def test_comment_lines_do_not_count():
    assert missing_metrics("# HELP vllm:num_requests_waiting x\n", ["vllm:num_requests_waiting"]) == ["vllm:num_requests_waiting"]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `.venv/bin/pytest tests/test_check_metrics.py -v`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `missing_metrics`**

只看非注释行，取 `{` 或空格之前的样本名；期望名 N 视为存在的条件：某个样本名等于 N，或等于 N 加 `_bucket`、`_sum`、`_count`。保持期望列表的原顺序返回缺失项。

- [ ] **Step 4: 运行测试，确认通过**

Run: `.venv/bin/pytest tests/test_check_metrics.py -v`
Expected: 4 passed。

- [ ] **Step 5: `expected-metrics.txt`**

每行一个，按 vLLM v0.30.0 的命名（2026-10-13 已对照 v0.30.0 源码核实）：`vllm:num_requests_running`、`vllm:num_requests_waiting`、`vllm:kv_cache_usage_perc`、`vllm:time_to_first_token_seconds`、`vllm:inter_token_latency_seconds`、`vllm:generation_tokens_total`、`vllm:prompt_tokens_total`、`vllm:prefix_cache_queries_total`、`vllm:prefix_cache_hits_total`、`DCGM_FI_DEV_GPU_UTIL`、`DCGM_FI_DEV_FB_USED`、`DCGM_FI_DEV_GPU_TEMP`、`DCGM_FI_DEV_POWER_USAGE`。

- [ ] **Step 6: vLLM 部署和下载脚本**

`download-model.sh <ModelScope 模型 ID>...`：`pip install "modelscope>=1.25,<2"` 后 `modelscope download --model <id> --local_dir /data/models/<名称>`，已存在则跳过。
`vllm-qwen3-8b.yaml`：Deployment `vllm` 加 Service `vllm`（命名空间 `infer`，端口 8000）。`strategy: Recreate`（只有一张卡，滚动更新会让新 Pod 永远 Pending，讲解里写明）；`runtimeClassName: nvidia`；`limits: nvidia.com/gpu: 1`；hostPath `/data/models` 只读挂到 `/models`；`/dev/shm` 用 `medium: Memory` 的 emptyDir；参数 `--model /models/Qwen3-8B --served-model-name qwen3-8b --max-model-len 8192 --gpu-memory-utilization 0.45 --enable-prefix-caching --port 8000`；startupProbe `GET /health`，`periodSeconds: 10`、`failureThreshold: 60`；readinessProbe `GET /health`，`periodSeconds: 5`；Pod 注解 `prometheus.io/scrape: "true"`、`prometheus.io/port: "8000"`、`prometheus.io/path: /metrics`。讲解里写 KV Cache 容量估算（规格书 4.2 节的算式），并说明 48G 卡为什么用 0.45。
`vllm-qwen3-8b-awq.yaml`：同名资源，只把 `--model` 改为 `/models/Qwen3-8B-AWQ`。

- [ ] **Step 7: 验证并提交**

Run: `make check`
Expected: 全部通过。

```bash
git add -A && git commit -m "feat: 第 2 课 vLLM 部署和指标核对工具"
```

### Task 4: 第 3 课配置——监控与告警

**Files:**
- Create: `lessons/03-monitoring/prometheus.yaml`、`dcgm-exporter.yaml`、`grafana.yaml`
- Create: `lessons/03-monitoring/rules/vllm-alerts.yaml`、`rules/vllm-alerts.test.yaml`、`dashboards/vllm-overview.json`
- Create: `scripts/create-secrets.sh`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: 任务 3 的 `expected-metrics.txt`。
- Produces: Prometheus `prometheus.monitoring:9090`；Grafana `grafana.monitoring:3000`，数据源 uid `prometheus`，看板 uid `vllm-overview`。
- Produces: ConfigMap `prometheus-rules`、`grafana-dashboards` 由源文件生成：`kubectl -n monitoring create configmap <名> --from-file=<文件> --dry-run=client -o yaml | kubectl apply -f -`（写进讲解，源文件只有一份）。

- [ ] **Step 1: 先写告警规则的 promtool 测试**

`rules/vllm-alerts.test.yaml`，`rule_files: [vllm-alerts.yaml]`，`evaluation_interval: 15s`，用例：
- `kv_fires`：`vllm:kv_cache_usage_perc` 恒为 0.95，`eval_time: 2m` 期望 `KVCacheNearFull` firing；`eval_time: 30s` 期望无告警。
- `kv_below`：恒为 0.85，`eval_time: 3m` 无告警。
- `queue_fires`：`vllm:num_requests_waiting` 恒为 6，`2m` firing；`queue_at_threshold`：恒为 5，`3m` 无告警。
- `ttft_fires`：`vllm:time_to_first_token_seconds_bucket` 的 `le="2.5"` 恒为 0，`le="5"` 和 `le="+Inf"` 每 15s 加 10，`eval_time: 4m` 期望 `TTFTP95High` firing。
- `no_traffic`：三个 bucket 都恒定不变，`4m` 无告警。
- `vllm_absent`：不提供任何 vLLM 序列，`4m` 无告警。

- [ ] **Step 2: 运行测试，确认失败**

Run: `promtool test rules lessons/03-monitoring/rules/vllm-alerts.test.yaml`
Expected: FAIL，规则文件不存在。

- [ ] **Step 3: 写规则**

`rules/vllm-alerts.yaml`，组名 `vllm`：
- `KVCacheNearFull`：`max(vllm:kv_cache_usage_perc) > 0.9`，`for: 1m`
- `RequestQueueBacklog`：`sum(vllm:num_requests_waiting) > 5`，`for: 1m`
- `TTFTP95High`：`histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[1m]))) > 2`，`for: 2m`
每条带 `annotations.summary`，用中文说明含义和第一步排查动作。

- [ ] **Step 4: 运行测试，确认通过**

Run: `promtool check rules lessons/03-monitoring/rules/vllm-alerts.yaml && promtool test rules lessons/03-monitoring/rules/vllm-alerts.test.yaml`
Expected: `SUCCESS`。把这条命令加进 `make check`。

- [ ] **Step 5: 写看板一致性测试**

```python
import json, re
from pathlib import Path

MON = Path("lessons/03-monitoring")
dash = json.loads((MON / "dashboards/vllm-overview.json").read_text())
panels = [p for p in dash["panels"] if p["type"] != "row"]
exprs = [t["expr"] for p in panels for t in p.get("targets", [])]
expected = set((MON / "expected-metrics.txt").read_text().split())

def referenced(texts):
    names = set()
    for t in texts:
        for m in re.findall(r"(vllm:[a-z_]+|DCGM_FI_[A-Z_]+)", t):
            names.add(re.sub(r"_(bucket|sum|count)$", "", m))
    return names

def test_dashboard_identity():
    assert dash["uid"] == "vllm-overview"
    assert len(panels) == 8
    assert all(p["datasource"]["uid"] == "prometheus" for p in panels)

def test_dashboard_covers_key_metrics():
    joined = "\n".join(exprs)
    for name in ["vllm:kv_cache_usage_perc", "vllm:num_requests_waiting", "vllm:time_to_first_token_seconds_bucket",
                 "vllm:inter_token_latency_seconds_bucket", "vllm:generation_tokens_total",
                 "vllm:prefix_cache_hits_total", "DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_FB_USED"]:
        assert name in joined

def test_only_expected_metrics_are_referenced():
    rules = (MON / "rules/vllm-alerts.yaml").read_text()
    assert referenced(exprs + [rules]) <= expected
```

- [ ] **Step 6: 运行测试，确认失败**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: FAIL，看板文件不存在。

- [ ] **Step 7: 写看板 JSON**

8 个面板按排查顺序：①TTFT P50/P95 ②ITL P95 ③每秒生成 token（`rate(vllm:generation_tokens_total[1m])`）④运行中与排队请求 ⑤KV Cache 占用率 ⑥GPU 利用率与显存 ⑦前缀缓存命中率（hits 与 queries 的 rate 之比）⑧网关各后端请求数（LiteLLM 指标，`litellm_` 前缀，任务 12 确认可用性，不可用则删除该面板并把测试改为 7）。`schemaVersion` 用 Grafana 11 的值，`refresh: 5s`。

- [ ] **Step 8: 运行测试，确认通过**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: 3 passed。

- [ ] **Step 9: 部署清单和密钥脚本**

- `prometheus.yaml`：ServiceAccount、ClusterRole（pods、nodes、endpoints 的 get/list/watch）、ClusterRoleBinding、ConfigMap `prometheus-config`（`scrape_interval: 5s`；`kubernetes_sd_configs` role `pod`；按 `prometheus.io/scrape`、`port`、`path` 注解做 relabel；`rule_files: /etc/prometheus/rules/*.yaml`）、Deployment（`--storage.tsdb.retention.time=7d`，hostPath `/data/prometheus`，挂载 `prometheus-rules`）、Service 9090。
- `dcgm-exporter.yaml`：命名空间 `gpu` 的 DaemonSet，`runtimeClassName: nvidia`，`DCGM_EXPORTER_KUBERNETES=true`，挂载 `/var/lib/kubelet/pod-resources`，端口 9400 及 scrape 注解。讲解提到 time-slicing 下利用率无法拆到 Pod。
- `grafana.yaml`：ConfigMap 提供数据源（uid `prometheus`，`http://prometheus.monitoring:9090`）和看板 provider（目录 `/var/lib/grafana/dashboards`）；Deployment 挂载 `grafana-dashboards`，管理员密码取自 Secret `grafana-admin`；Service 3000。讲解写清访问方式：服务器上 `kubectl -n monitoring port-forward svc/grafana 3000`，Mac 上 `ssh -L 3000:localhost:3000 $GPU_HOST`。
- `create-secrets.sh`：读取 `.env`，创建 `monitoring/grafana-admin`（`admin-password`）和 `gateway/litellm-secrets`（`DASHSCOPE_API_KEY`、`LITELLM_MASTER_KEY`），每个 Secret 只含自己需要的键；变量为空时报错退出。

- [ ] **Step 10: 验证并提交**

Run: `make check`
Expected: 全部通过。

```bash
git add -A && git commit -m "feat: 第 3 课 监控、告警和看板"
```

### Task 5: 第 4 课配置——压测脚本与结果汇总

**Files:**
- Create: `lessons/04-loadtest/bench-client.yaml`、`scripts/bench.sh`、`scripts/benchlib.py`、`scripts/plot.py`
- Test: `tests/test_benchlib.py`、`tests/test_plot.py`、`tests/fixtures/bench/baseline/c1.json`、`c16.json`、`awq/c16.json`

**Interfaces:**
- Produces: `scripts/bench.sh <label> <base_url> <served_model> [额外 vllm bench 参数...]`，环境变量 `CONCURRENCIES`（默认 `1 4 16 32 64`）、`TOKENIZER`（默认 `/models/Qwen3-8B`）；结果写 `/results/bench/<label>/c<N>.json`。
- Produces: `@dataclass BenchRow(label: str, concurrency: int, output_throughput: float, p50_ttft_ms: float, p95_ttft_ms: float, p95_itl_ms: float, p95_e2el_ms: float, completed: int, failed: int)`；`load_rows(bench_dir: Path) -> list[BenchRow]`；`to_markdown(rows: list[BenchRow]) -> str`。
- Produces: `plot_rows(rows: list[BenchRow], out_dir: Path) -> list[Path]`，生成 `throughput.png`、`ttft_p95.png`，每个 label 一条线。CLI `python3 scripts/plot.py <bench_dir> <out_dir>`，同时打印 markdown 表。
- Consumes（任务 6）：`BenchRow.output_throughput` 和请求吞吐由 `cost.py` 直接读 JSON 的 `request_throughput`。

- [ ] **Step 1: 准备 fixtures 并写失败的测试**

fixture JSON 使用 `vllm bench serve --save-result` 的字段：`max_concurrency`、`num_prompts`、`completed`、`request_throughput`、`output_throughput`、`p50_ttft_ms`、`p95_ttft_ms`、`p95_itl_ms`、`p95_e2el_ms`。`baseline/c16.json` 设 `num_prompts: 160`、`completed: 158`。

```python
from pathlib import Path
from scripts.benchlib import load_rows, to_markdown

FIX = Path("tests/fixtures/bench")

def test_rows_grouped_by_label_and_sorted():
    rows = load_rows(FIX)
    assert [(r.label, r.concurrency) for r in rows] == [("awq", 16), ("baseline", 1), ("baseline", 16)]

def test_failed_requests_are_reported():
    row = next(r for r in load_rows(FIX) if r.label == "baseline" and r.concurrency == 16)
    assert row.failed == 2
    assert "| 2 |" in to_markdown([row])

def test_markdown_has_header():
    assert to_markdown(load_rows(FIX)).splitlines()[0].startswith("| 配置 | 并发 |")
```

`tests/test_plot.py`：`plot_rows(load_rows(FIX), tmp_path)` 返回两个存在且非空的 PNG。

- [ ] **Step 2: 运行测试，确认失败**

Run: `.venv/bin/pytest tests/test_benchlib.py tests/test_plot.py -v`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `benchlib.py` 和 `plot.py`**

label 取文件的上级目录名；并发取 JSON 的 `max_concurrency`；`failed = num_prompts - completed`。表头：`| 配置 | 并发 | 输出吞吐 tok/s | TTFT P50 ms | TTFT P95 ms | ITL P95 ms | E2E P95 ms | 完成 | 失败 |`。plot 使用 `matplotlib.use("Agg")`，横轴并发用对数刻度。

- [ ] **Step 4: 运行测试，确认通过**

Run: `.venv/bin/pytest tests/test_benchlib.py tests/test_plot.py -v`
Expected: 4 passed。

- [ ] **Step 5: 压测客户端和脚本**

`bench-client.yaml`：命名空间 `infer` 的 Deployment `bench-client`，vLLM 同版本镜像，不申请 GPU，命令 `sleep infinity`，挂载 `/data/models`（只读，到 `/models`）、`/data/results`（到 `/results`）、`/data/lab/scripts`（只读，到 `/lab/scripts`）。
`bench.sh`：对每个并发 c，`n = max(10*c, 50)`，执行 `kubectl -n infer exec deploy/bench-client -- vllm bench serve --backend openai --base-url <base_url> --model <served_model> --tokenizer $TOKENIZER --dataset-name random --random-input-len 1024 --random-output-len 256 --ignore-eos --num-prompts n --max-concurrency c --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,95 --save-result --result-dir /results/bench/<label> --result-filename c<c>.json <额外参数>`。讲解里列出第 5 天的实验矩阵（见任务 11）和前缀缓存实验用的 `--random-prefix-len 512 --random-input-len 512`。

- [ ] **Step 6: 验证并提交**

Run: `make check`
Expected: 全部通过。

```bash
git add -A && git commit -m "feat: 第 4 课 压测脚本与结果汇总"
```

### Task 6: 第 5 课配置——LiteLLM 网关、探测脚本和成本计算

**Files:**
- Create: `lessons/05-gateway/litellm-config.yaml`、`lessons/05-gateway/litellm.yaml`、`scripts/gateway_probe.py`、`scripts/cost.py`
- Test: `tests/test_gateway_probe.py`、`tests/test_cost.py`

**Interfaces:**
- Consumes: 任务 3 的 Service `vllm.infer:8000`；任务 4 的 Secret `gateway/litellm-secrets`；任务 5 的 bench JSON。
- Produces: `backend_from_headers(headers: Mapping[str, str]) -> str | None`（读 `x-litellm-model-id`）；`@dataclass ProbeResult(status: int, backend: str | None, latency_s: float)`；`async probe(base_url: str, api_key: str, model: str, total: int, concurrency: int) -> list[ProbeResult]`；`summarize(results: list[ProbeResult]) -> dict[str, int]`。CLI `python3 gateway_probe.py --base-url --model --total --concurrency --out <json>`，API key 读环境变量 `LITELLM_MASTER_KEY`。
- Produces: `self_hosted_cost_per_request(gpu_price_per_hour: float, requests_per_sec: float, utilization: float) -> float`；`api_cost_per_request(input_tokens: int, output_tokens: int, input_price_per_mtok: float, output_price_per_mtok: float) -> float`；`breakeven_requests_per_day(gpu_price_per_hour: float, api_cost_per_request: float) -> float`。CLI `python3 scripts/cost.py --bench <json> --gpu-price --input-price --output-price` 输出 markdown 表（利用率 100% 与 30% 两行）。

说明：规格书 4.5 节按「每百万输出 token」计算自建成本，会漏掉 API 的输入 token 费用。这里改为按单个请求比较（输入 1024、输出 256），两边口径一致；表里同时给出折算的每百万 token 成本供参考。

- [ ] **Step 1: 写失败的测试**

前两个用例放 `tests/test_gateway_probe.py`，其余放 `tests/test_cost.py`：

```python
import pytest
from scripts.gateway_probe import ProbeResult, backend_from_headers, summarize
from scripts.cost import self_hosted_cost_per_request, api_cost_per_request, breakeven_requests_per_day

def test_backend_from_headers():
    assert backend_from_headers({"x-litellm-model-id": "self-hosted"}) == "self-hosted"
    assert backend_from_headers({}) is None

def test_summarize_counts_errors_and_unknown():
    rs = [ProbeResult(200, "self-hosted", 1.0), ProbeResult(200, "bailian", 2.0),
          ProbeResult(200, None, 1.5), ProbeResult(429, None, 0.1)]
    assert summarize(rs) == {"self-hosted": 1, "bailian": 1, "unknown": 1, "error": 1}

def test_self_hosted_cost():
    assert self_hosted_cost_per_request(1.92, 4.0, 1.0) == pytest.approx(1.92 / 3600 / 4.0)
    assert self_hosted_cost_per_request(1.92, 4.0, 0.3) == pytest.approx(1.92 / 3600 / 4.0 / 0.3)

def test_api_cost():
    assert api_cost_per_request(1024, 256, 0.5, 2.0) == pytest.approx((1024 * 0.5 + 256 * 2.0) / 1e6)

def test_breakeven():
    assert breakeven_requests_per_day(1.92, 0.001) == pytest.approx(1.92 * 24 / 0.001)

def test_rejects_zero_throughput():
    with pytest.raises(ValueError):
        self_hosted_cost_per_request(1.92, 0.0, 1.0)
    with pytest.raises(ValueError):
        self_hosted_cost_per_request(1.92, 4.0, 0.0)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `.venv/bin/pytest tests/test_gateway_probe.py tests/test_cost.py -v`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `gateway_probe.py` 和 `cost.py`**

`probe` 用 `httpx.AsyncClient` 加 `asyncio.Semaphore(concurrency)` 发 `POST /v1/chat/completions`（默认约 1000 token 输入、`max_tokens: 256`、`stream: false`，与压测同形状，开头带随机串避开前缀缓存；连接上限跟随并发数。2026-10-13 整体审查发现原先的短请求不会让 vLLM 排队，超时切换触发不了），记录状态码、后端标识和耗时；网络异常记为 status 0。`summarize` 中非 200 计入 `error`，200 无标识计入 `unknown`。CLI 写结果前创建 `--out` 的父目录。`cost.py` 对非正的吞吐、利用率、单价抛 `ValueError`。

- [ ] **Step 4: 运行测试，确认通过**

Run: `.venv/bin/pytest tests/test_gateway_probe.py tests/test_cost.py -v`
Expected: 6 passed。

- [ ] **Step 5: LiteLLM 清单**

以 `../agent-cost-loop/k8s/10-litellm-config.yaml`、`30-litellm.yaml` 为起点。`litellm-config.yaml`（ConfigMap，命名空间 `gateway`）：
- 模型组 `qwen3-8b`：`model: hosted_vllm/qwen3-8b`，`api_base: http://vllm.infer:8000/v1`，`timeout: 30`（任务 12 按实测改），`extra_body: {chat_template_kwargs: {enable_thinking: false}}`，`model_info.id: self-hosted`。
- 模型组 `bailian-flash`：`model: openai/qwen3.8-flash`（百炼已不提供 qwen3-8b，2026-10-13 确认），`api_base: https://dashscope.aliyuncs.com/compatible-mode/v1`，`api_key: os.environ/DASHSCOPE_API_KEY`，`extra_body: {enable_thinking: false}`，`model_info.id: bailian`。
- `router_settings`：`fallbacks: [{"qwen3-8b": ["bailian-flash"]}]`，`num_retries: 0`。
- `general_settings.master_key: os.environ/LITELLM_MASTER_KEY`。

讲解写清切换机制：vLLM 过载时不拒绝请求而是排队，排队导致请求超过 `timeout`，LiteLLM 把超时请求转给百炼。这里没有采用规格书写的「并发上限」：LiteLLM v1.102.1 的 `router.py` 用 `asyncio.Semaphore` 实现 `max_parallel_requests`，超出的请求在网关排队等待，不会触发 fallback（2026-10-13 查源码确认，任务 12 的 Step 1b 可再实测佐证）。
`litellm.yaml`：Deployment（锁定版本镜像，`envFrom` Secret `litellm-secrets`，挂载配置，`--config /app/config.yaml --port 4000`）和 Service `litellm:4000`。

- [ ] **Step 6: 验证并提交**

Run: `make check`
Expected: 全部通过。

```bash
git add -A && git commit -m "feat: 第 5 课 网关、探测脚本和成本计算"
```

---

以下任务在 GPU 机器上执行：由作者亲手操作，Claude 协助排障并核对输出。每一步的输出存进 `results/` 对应目录，每天结束前在 Mac 上执行 `rsync -av $GPU_HOST:/data/results/ results/` 并提交。每天关机前把当天的 GPU 时长和费用追加到 `results/00-instance.md` 的费用表，累计超过 80 元时停止所有标为「可砍」的步骤。

### Task 7: 第 1 天收尾——开通 GPU 实例

- [x] **Step 1（2026-10-14 已完成：7 个镜像全部存在）:** Mac 上执行 `scripts/check-images.sh`。Expected: 所有镜像存在；不存在的按 Global Constraints 的规则替换，`make check` 后提交。
- [x] **Step 2（2026-10-13 已完成：上海 RTX 4090 48G，root 可用，驱动 570.153.02，系统盘可用 83G，内存 94Gi。写 `results/00-instance.md` 时不要写密码和 IP）:** 在优云智算开一台 RTX 4090 单卡实例，计费选**按量付费**（只有按量实例能在关机时切到 0.15 元/小时的无卡模式），镜像选**系统镜像 Ubuntu 22.04**，创建页如有预装 NVIDIA 驱动的选项则选 570 或更高版本（系统镜像是虚机；CUDA、PyTorch 等基础镜像和社区镜像是容器，不能装 Docker 和 k3s，不要选）。按实例详情页「登录指令」中的主机和端口登录（华北一C 等无公网 IP 的地域是映射端口，不一定是 22），用户 `ubuntu`；先用控制台密码登录，再 `ssh-copy-id` 加公钥；在 Mac 的 `~/.ssh/config` 里配好别名并写进 `.env` 的 `GPU_HOST`。确认：`sudo -i` 能拿到 root；`nvidia-smi` 有没有输出（没有说明需要装驱动，第 2 天由 `setup-node.sh` 处理），有则记下驱动版本；`df -h` 中可用空间不少于 80GB（三个模型约 26GB，加上 vLLM 等镜像）；关机后是否计费（查文档或客服）。写入 `results/00-instance.md`。如果登录用户是 root 且端口是 23，说明开成了容器实例，删除后按上面的要求重开。
- [x] **Step 3（2026-10-13 已完成：百炼已不提供 qwen3-8b，降级目标改为 qwen3.8-flash，北京地域输入 0.8 元、输出 2.7 元 / 百万 token，价格页 https://help.aliyun.com/zh/model-studio/model-pricing）:** 查百炼是否提供 `qwen3-8b` 及其输入、输出单价，连同查询日期和价格页链接写入 `results/00-instance.md`。
- [x] **Step 4（2026-10-14 已完成，别名 gpu4090）:** `ssh $GPU_HOST 'sudo mkdir -p /data/lab && sudo chown ubuntu:ubuntu /data/lab'`，然后 `rsync -av --exclude .venv --exclude .env ./ $GPU_HOST:/data/lab/`，并在服务器上手动建 `/data/lab/.env`。

### Task 8: 第 2 天——第 1 课

- [x] **Step 0（2026-10-14 实际在有卡模式下做完：三个模型已下载；除 vLLM 外的镜像已预拉，vLLM 镜像约 10GB，公共镜像站实测最快约 3MB/s）:** 无卡模式开机，`sudo /data/lab/scripts/setup-node.sh`。Expected: 第 1 步提示检测不到 GPU 并跳过，toolkit、k3s、目录、kubeconfig 装好。然后预拉镜像 `grep -rhoE 'image: *[^ ]+' /data/lab/lessons | awk '{print $2}' | sort -u | xargs -n1 sudo k3s crictl pull`（走 CRI，才会用 registries.yaml 的加速），并 `scripts/download-model.sh Qwen/Qwen3-1.7B Qwen/Qwen3-8B Qwen/Qwen3-8B-AWQ`。完成后关机，切回有卡模式开机。
- [x] **Step 1（2026-10-14 已完成：驱动 580.178.04 / CUDA 13.0，doctor 四项 ✅）:** `sudo /data/lab/scripts/setup-node.sh`（装驱动，重启后再跑一次），然后 `scripts/doctor.sh`。Expected: 驱动、toolkit、k3s、RuntimeClass 全部 ✅。
- [x] **Step 2（2026-10-14 已完成）:** apply `00-namespaces.yaml`、`runtimeclass.yaml`、exclusive 配置和 `device-plugin.yaml`。Expected: `kubectl get node -o jsonpath='{.items[0].status.allocatable}'` 含 `"nvidia.com/gpu":"1"`。保存输出到 `results/01-gpu/allocatable.txt`。
- [x] **Step 3（2026-10-14 已完成，文本证据在 results/01-gpu/pending.txt，截图未做）:** apply `gpu-hog-a.yaml` 和 `gpu-hog-b.yaml`。Expected: a Running 且日志有 `GPU 0: NVIDIA GeForce RTX 4090`；b Pending，`kubectl describe` 含 `Insufficient nvidia.com/gpu`。保存到 `results/01-gpu/pending.txt` 并截图。删除两个 Pod。
- [x] **Step 4（2026-10-14 已完成；两个实例同时启动时 a 因显存测量互相干扰重启过一次，见 startup-race-a.log）:** `download-model.sh Qwen/Qwen3-1.7B`；apply timeslice 配置并 `kubectl -n gpu rollout restart ds`。Expected: allocatable 变为 2。apply `timeslice-vllm.yaml`，两个都 Ready。
- [x] **Step 5（2026-10-14 已完成；实测没有 OOM 崩溃，而是后测量的实例 KV Cache 只剩 8,512 token，日志为 overcommit-{a,b}.log，讲解已按实测更新）:** 先只对 a 跑 `CONCURRENCIES=16 TOKENIZER=/models/Qwen3-1.7B scripts/bench.sh ts-single http://vllm-small-a.infer:8000 qwen3-1.7b`，再两个同时跑（label `ts-shared-a`、`ts-shared-b`）。然后按讲解把两个实例改成 0.9，保存第二个实例的 OOM 日志到 `results/01-gpu/oom.log`。恢复 exclusive 配置并删除两个实例。

### Task 9: 第 3 天——第 2 课

- [x] **Step 1（2026-10-14 第 2 天已下载）:** `download-model.sh Qwen/Qwen3-8B Qwen/Qwen3-8B-AWQ`。
- [x] **Step 2（2026-10-15 已完成：KV Cache 30,352 token，可用 4.17 GiB；首次部署因 Service 注入的 VLLM_PORT 与 vLLM 环境变量撞名而启动失败，已加 enableServiceLinks: false）:** apply `vllm-qwen3-8b.yaml`。Expected: Pod Ready；启动日志中有 KV Cache 容量（token 数），抄进 `results/02-vllm/kv-capacity.txt`，与讲解中的估算对比。
- [x] **Step 3（2026-10-15 已完成，results/02-vllm/chat.json）:** 在 bench-client 或节点上 `curl http://vllm.infer:8000/v1/chat/completions`，带 `"chat_template_kwargs": {"enable_thinking": false}`。Expected: 返回中文回答。
- [x] **Step 4（2026-10-15 已完成：vLLM 指标无缺失，只缺第 4 天才部署的 DCGM）:** `curl -s http://vllm.infer:8000/metrics > results/02-vllm/metrics.txt`，然后 `python3 scripts/check_metrics.py results/02-vllm/metrics.txt lessons/03-monitoring/expected-metrics.txt`（DCGM 指标此时还没有，只看 vLLM 部分）。Expected: vLLM 指标无缺失。有缺失时，用 metrics.txt 里的实际名字更新 `expected-metrics.txt`、告警规则和看板，在 Mac 上跑 `make check` 直到通过再提交。

### Task 10: 第 4 天——第 3 课

- [ ] **Step 1:** `scripts/create-secrets.sh`；apply `prometheus.yaml`、`dcgm-exporter.yaml`、`grafana.yaml`，并按讲解从源文件生成 `prometheus-rules`、`grafana-dashboards`。
- [ ] **Step 2:** Prometheus 的 Targets 页面中 vLLM 和 DCGM 都是 UP；Rules 页面三条告警都已加载。截图。
- [ ] **Step 3:** 用 `check_metrics.py` 核对 DCGM 指标（`curl` DCGM 的 9400 端口）。Expected: 无缺失。
- [ ] **Step 4:** Mac 上通过 SSH 端口转发打开 Grafana 的 `vllm-overview` 看板，面板①到⑦都有数据（⑧等第 6 天）。截图。

### Task 11: 第 5 天——第 4 课

- [ ] **Step 1:** apply `bench-client.yaml`；跑基线 `scripts/bench.sh baseline http://vllm.infer:8000 qwen3-8b`。压到 32、64 并发时，在 Prometheus 告警页截图三条告警进入 firing。
- [ ] **Step 2:** 依次修改 Deployment 参数后各跑一轮：`full48g`（`--gpu-memory-utilization 0.90`，不限显存的对照，`CONCURRENCIES="1 4 16 32 64 128"`，看 KV Cache 容量翻倍后拐点后移多少）；`util040`（`--gpu-memory-utilization 0.40`）；`seqs16`、`seqs32`、`seqs64`（`--max-num-seqs`）；`prefix-on` 与 `prefix-off`（加 `--no-enable-prefix-caching`），这两轮都用 `--random-prefix-len 512 --random-input-len 512`。
- [ ] **Step 3（可砍）:** apply `vllm-qwen3-8b-awq.yaml`，label `awq` 跑一轮；结束后换回 BF16。
- [ ] **Step 4:** Mac 上 `rsync` 结果，执行 `.venv/bin/python scripts/plot.py results/bench results/charts > results/bench/summary.md`。根据曲线定出拐点并发（TTFT P95 开始陡增前的最后一档），写入 `results/bench/knee.md`，附拐点那一档的 E2E P95。

### Task 12: 第 6 天——第 5 课

- [ ] **Step 1:** 把 `litellm-config.yaml` 的 `timeout` 设为拐点并发下 E2E P95 的 1.5 倍（向上取整到秒），apply `litellm-config.yaml`、`litellm.yaml`。先单发一个请求确认响应头里有 `x-litellm-model-id: self-hosted`；没有的话换用 LiteLLM 当前版本返回的标识字段，同步修改 `backend_from_headers` 及其测试。
- [ ] **Step 1b（可选，约 5 分钟）:** 核实 `max_parallel_requests` 的行为：在 `qwen3-8b` 组临时加 `max_parallel_requests: 2`，用并发 6 发 12 个请求。没有请求转到 `bailian` 就说明它只让请求排队、不触发 fallback，在第 5 课讲解中写明这个实测结论；然后删掉该配置。
- [ ] **Step 2:** 超载演示。先用 `--total 20 --concurrency 8` 冒烟一次，确认响应头标识正常；探测请求默认与压测同形状（约 1000 token 输入、256 输出）。如果正式运行后 `bailian` 仍为 0，就先在后台对 vLLM 直接加压（`CONCURRENCIES=128 scripts/bench.sh overload-bg http://vllm.infer:8000 qwen3-8b &`），再经网关探测。正式命令：`kubectl -n infer exec deploy/bench-client -- python3 /lab/scripts/gateway_probe.py --base-url http://litellm.gateway:4000 --model qwen3-8b --total 300 --concurrency <拐点的 2 倍> --out /results/gateway/overload.json`。Expected: 汇总里 `bailian` 计数大于 0。
- [ ] **Step 3:** 故障演示：`kubectl -n infer scale deploy/vllm --replicas=0` 后用并发 4、共 40 个请求探测（`--out /results/gateway/vllm-down.json`）。Expected: 全部 `bailian`。恢复 replicas=1 并等 Ready 后再测一次（`vllm-back.json`），Expected: 全部 `self-hosted`。
- [ ] **Step 4:** 看板面板⑧有数据则截图；没有则说明 LiteLLM 指标不可用，删除面板⑧、把 `test_dashboard_identity` 改为 7 个面板并提交。
- [x] **Step 5（2026-10-14 已完成；实测没有 OOM 崩溃，而是后测量的实例 KV Cache 只剩 8,512 token，日志为 overcommit-{a,b}.log，讲解已按实测更新）:** `.venv/bin/python scripts/cost.py --bench results/bench/baseline/c<拐点>.json --gpu-price <实付单价> --input-price <百炼> --output-price <百炼> > results/cost.md`。

### Task 13: 第 7 天——整理结果

- [ ] **Step 1:** 写 `README.md`：一段话说明场景；架构图（文字版）；版本表；复现步骤（按课）；结果（压测表、曲线图、调参对比、告警截图、切换统计、成本表，全部引用 `results/` 下的文件）；已知边界（服务挂掉时三条告警不触发，靠 doctor 和网关兜底；time-slicing 不隔离显存）。
- [ ] **Step 2:** 在 README 末尾写简历条目（3-4 条，每条至少一个 `results/` 中的数字）和面试问答（不少于 10 题，覆盖 KV Cache 估算与实测的差异、拐点怎么定、为什么用 Recreate、time-slicing 与 MIG 的区别、告警为什么不盯 GPU 利用率、切换为什么靠超时、成本口径）。
- [ ] **Step 3:** 核对：README 中的每个数字都能在 `results/` 中找到出处。
- [ ] **Step 4:** 用 docs 工具把简历条目加进简历在线文档的个人项目部分（链接见 `CLAUDE.md`），只加作者确认过的条目。
- [ ] **Step 5:** `make check` 通过后提交。
