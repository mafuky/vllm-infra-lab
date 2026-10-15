# vllm-infra-lab 第二周实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在第一周的单卡 k3s 平台上，完成 vLLM 与 SGLang 对比、Agent 式多轮负载、冷启动分段计时与优化、KEDA 在单张 48G 卡上扩到两个副本，以及三篇 vLLM 源码笔记，并更新 README 和简历。

**Architecture:** 任务 1-5 在 Mac 上写配置、两个新脚本（`agent_workload.py`、`coldstart.py`）、一个时间线记录脚本和 KEDA 配置，纯计算部分全部单测，`make check` 离线验证。任务 6-9 在 GPU 机器上按天执行，结果写入 `results/`。任务 10 读源码写笔记（晚上，不需要 GPU），任务 11 汇总进 README 和简历。

**Tech Stack:** SGLang v0.5.20、vLLM v0.30.0、KEDA 2.21.0、k3s、Prometheus、LiteLLM、Python 3（pytest、httpx）、Bash。

**Spec:** `docs/superpowers/specs/2026-10-13-vllm-infra-lab-week2-design.md`（已评审，先读第 0 节）

## Global Constraints

- 硬件：优云智算华北二A RTX 4090 48G 单卡，按量 3.17 元/小时；双卡无货，全部实验在这张卡上做。不需要 GPU 的活放在无卡模式或 Mac 上。
- 预算：两周合计不超过 150 元；第一周实际约 37 元；两周累计超过 120 元时停止所有可砍项。
- 镜像（已在服务器上，digest 已核对）：`lmsysorg/sglang:v0.5.20-cu130`、`ghcr.io/kedacore/keda:2.21.0`、`ghcr.io/kedacore/keda-metrics-apiserver:2.21.0`、`ghcr.io/kedacore/keda-admission-webhooks:2.21.0`；vLLM 等沿用第一周版本。禁止浮动标签。
- 模型 `/models/Qwen3-8B`（BF16），服务名一律 `qwen3-8b`，上下文 8192。
- vLLM 对照配置：第一周的 `lessons/02-vllm/vllm-qwen3-8b.yaml`（显存 0.45，KV Cache 30,352 token）。SGLang 调 `--mem-fraction-static` 使启动日志的 KV 容量与 30,352 token 偏差不超过 10%。
- 标准负载：复用 `scripts/bench.sh`（每档不同 seed），random 输入 1024、输出 256，`--ignore-eos`，并发 1、4、16、32、64；vLLM 一侧复用 `results/bench/baseline/`，不重跑。
- Agent 负载：每会话 6 轮；共享前缀 4,000 token，每轮新增 500 token，每轮输出 150 token；并发会话 8、16、32，每档会话数 = 4 × 并发；共享前缀与不共享前缀两组；关闭思考模式；固定随机种子。
- 冷启动：每种条件 3 次，取中位数。
- KEDA：`minReplicaCount: 1`、`maxReplicaCount: 2`，触发查询 `avg_over_time(sum(vllm:num_requests_waiting)[1m:])`，阈值 5，缩容稳定窗口 300 秒；设备插件切 time-slicing（replicas 2），每个副本仍是显存 0.45。
- 所有跑推理引擎镜像的 Deployment 必须 `enableServiceLinks: false`（`tests/test_manifests.py` 已覆盖 sglang 镜像）。
- 密钥只在 K8s Secret 和本地 `.env`。README、简历中的每个数字必须来自 `results/`，不得编造或预测。
- 讲解写在每个新配置和脚本的头部注释里，沿用第一周的写法（「第 N 课 · 标题」、预计时长、你在做什么、为什么、怎么执行、应该看到什么），中文。
- 砍需求顺序：投机解码 → 缩容到 0 → 第三篇源码笔记 → 冷启动的 `--enforce-eager`。SGLang 标准对比、Agent 负载、冷启动前两项优化、KEDA 扩到 2 个副本不砍。

## Rulings（写计划时定下的实现决定）

1. 冷启动重建用「缩到 0、等旧 Pod 消失、再扩到 1」，不直接删 Pod：直接删时新 Pod 要等旧 Pod 释放 GPU，这段等待会被算进「调度」。
2. 冷启动拆 6 段而不是规格书的 5 段：在「容器启动」和「加载权重」之间单列「进程初始化」（Python 导入、CUDA 初始化），否则各段相加不等于总时长。
3. 缩容冷却用 HPA 的 `scaleDown.stabilizationWindowSeconds: 300`：KEDA 的 `cooldownPeriod` 只作用于缩到 0。
4. Agent 负载历史里的助手回答用固定的合成文本，不用模型真实输出，保证两个引擎收到完全相同的请求序列。
5. KEDA 官方清单 vendored 进仓库（`lessons/08-autoscaling/keda-2.21.0.yaml`，与服务器 `/data/downloads/keda-2.21.0.yaml` 同一文件），服务器连 GitHub 不稳定。
6. 副本数在 Prometheus 里用 `count(vllm:num_requests_running)` 表示（每个被采集的 vLLM Pod 一条序列），本项目没有 kube-state-metrics。

## Review Focus

1. **流式响应的第一个块不带内容**：OpenAI 兼容接口的第一个 SSE 块通常只有 `role`，TTFT 必须从第一个带非空 `content` 的块算起，否则 TTFT 被低估。由任务 2 的 `test_role_only_chunk_is_not_first_token` 覆盖。
2. **会话中途某一轮失败**（非 200、超时、连接断开）：记为失败、该会话后续轮次不再发送，汇总里按轮次列出失败数，脚本不能崩溃。由任务 2 的 `test_failed_turn_stops_session_and_is_counted` 覆盖。
3. **拼接后的上下文超长**：各段单独校准长度，拼起来的最后一轮可能超过 8,192。生成会话时检查，超过就报错而不是让引擎截断或拒绝。由任务 2 的 `test_context_over_limit_raises` 覆盖。
4. **kubectl 的纳秒时间戳**：`kubectl logs --timestamps` 输出 9 位小数的 RFC3339（`2026-10-15T07:54:10.123456789Z`），Python 的 `fromisoformat` 不能直接解析；日志里缺少某个标记行（格式变了、容器重启只剩半截日志）时，对应阶段为空并提示缺哪一行，不能崩溃。由任务 3 的 `test_parses_nanosecond_timestamp`、`test_missing_mark_gives_none_phase` 覆盖。
5. **KEDA 没有扩容**：阈值从没越过、或副本数从没到 2 时，时间线汇总给出「未触发 / 未扩容」而不是崩溃或给出负数耗时。由任务 4 的 `test_no_scale_gives_none` 覆盖。

---

## 文件结构

```
lessons/
├── 06-sglang/             sglang.yaml（Deployment + Service）、expected-metrics.txt
├── 07-coldstart/          compile-cache.patch.json、enforce-eager.patch.json
├── 08-autoscaling/        keda-2.21.0.yaml（官方清单）、scaledobject.yaml
└── 03-monitoring/dashboards/vllm-overview.json   新增「vLLM 副本数」面板（共 9 个）
scripts/
├── agent_workload.py      Agent 式多轮负载：生成、发送、汇总、命中率、报告
├── coldstart.py           冷启动：重建 Pod、取 K8s 时间和日志标记、拆 6 段、取中位数
├── autoscale_timeline.py  扩缩容：按间隔记录副本数和排队数，汇总关键时间点
└── gateway_probe.py       每个请求记录发出时间，可按时间点切分统计
notes/vllm-source/         01-scheduler.md、02-kv-cache.md、03-prefix-cache.md
results/
├── bench/sglang-baseline/
├── agent/<engine>-<shared|unique>/c<N>.json
├── coldstart/<条件>.json
└── autoscaling/
```

---

## Task 1: 第 6 课配置——SGLang 部署

**Files:**
- Create: `lessons/06-sglang/sglang.yaml`、`lessons/06-sglang/expected-metrics.txt`
- Modify: `tests/test_manifests.py`

**Interfaces:**
- Produces: Deployment `infer/sglang`、Service `infer/sglang` 端口 8000，服务名 `qwen3-8b`；`expected-metrics.txt` 供任务 6 用 `scripts/check_metrics.py` 核对。

- [ ] **Step 1: 写失败的测试**

```python
def _sglang_doc():
    text = (LESSONS / "06-sglang" / "sglang.yaml").read_text(encoding="utf-8")
    return next(d for d in text.split("\n---") if "kind: Deployment" in d)


def test_sglang_matches_vllm_serving_contract():
    doc = _sglang_doc()
    for needle in ["--served-model-name", "qwen3-8b", "--context-length", "8192", "--enable-metrics",
                   "--mem-fraction-static", "type: Recreate", "nvidia.com/gpu: 1", "prometheus.io/scrape"]:
        assert needle in doc, needle
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/pytest tests/test_manifests.py -v`
Expected: FAIL，`FileNotFoundError`。

- [ ] **Step 3: 写 `sglang.yaml` 和 `expected-metrics.txt`**

照 `lessons/02-vllm/vllm-qwen3-8b.yaml` 的结构：`replicas: 0`（与 vllm 二选一，跑 SGLang 时把 vllm 缩到 0、把它扩到 1），`runtimeClassName: nvidia`，`enableServiceLinks: false`，挂 `/data/models` 和 4Gi 的 `/dev/shm`，prometheus 注解端口 8000。命令 `python3 -m sglang.launch_server --model-path /models/Qwen3-8B --served-model-name qwen3-8b --host 0.0.0.0 --port 8000 --context-length 8192 --mem-fraction-static 0.42 --enable-metrics`。0.42 的来历写进讲解：权重约 15.3 GiB + 30,352 token × 144KB ≈ 4.2 GiB ≈ 19.5 GiB，÷ 47.4 GiB ≈ 0.41；SGLang 的这个参数只算权重加 KV 池，与 vLLM 的总显存占比语义不同。startupProbe/readinessProbe 用 `/health`，startup 允许 10 分钟。`expected-metrics.txt` 先写 `sglang:max_total_num_tokens`、`sglang:num_running_reqs`、`sglang:num_queue_reqs`、`sglang:cache_hit_rate`，任务 6 按实际指标名修正。

- [ ] **Step 4: 运行，确认通过**

Run: `make check`
Expected: 全部通过（含 `test_engine_pods_disable_service_links` 覆盖 sglang）。

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat: 第 6 课 SGLang 部署配置"
```

## Task 2: 第 6 课脚本——Agent 式多轮负载

**Files:**
- Create: `scripts/agent_workload.py`
- Test: `tests/test_agent_workload.py`

**Interfaces:**
- Produces: `@dataclass Session(prefix: str, users: list[str], assistants: list[str])`；`make_text(tokenizer, n_tokens: int, rng: random.Random) -> str`；`build_sessions(tokenizer, n_sessions: int, shared: bool, seed: int, turns: int = 6, prefix_tokens: int = 4000, user_tokens: int = 500, assistant_tokens: int = 150, max_context: int = 8192) -> list[Session]`；`messages_for_turn(session: Session, turn: int) -> list[dict]`（turn 从 1 开始）。
- Produces: `@dataclass TurnRecord(session: int, turn: int, ok: bool, ttft_ms: float | None, e2e_ms: float | None, prompt_tokens: int | None, completion_tokens: int | None)`；`is_content_chunk(chunk: dict) -> bool`；`percentile(values: list[float], q: float) -> float`（最近秩法：排序后取第 ceil(q/100·n) 个）；`summarize(records: list[TurnRecord]) -> list[dict]`（每轮一行：`turn`、`ok`、`failed`、`ttft_p50_ms`、`ttft_p95_ms`）；`metric_sum(metrics_text: str, name: str) -> float`；`hit_rate(before: str, after: str, hits: str, queries: str) -> float | None`；`HIT_METRICS = {"vllm": ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_queries_total"), "sglang": ("sglang:cached_tokens_total", "sglang:prompt_tokens_total")}`（sglang 一项任务 6 核对）；`report(paths: list[Path]) -> str`。
- Produces（CLI，在 bench-client Pod 内）：`python3 /lab/scripts/agent_workload.py run --base-url URL --engine vllm|sglang --condition shared|unique --concurrency N [--seed 0] --out /results/agent/<engine>-<condition>/c<N>.json`；`python3 scripts/agent_workload.py report results/agent`。
- Consumes: 分词器只要求 `encode(text, add_special_tokens=False) -> list[int]` 和 `decode(ids) -> str`，真实运行用 `transformers.AutoTokenizer.from_pretrained("/models/Qwen3-8B")`。

- [ ] **Step 1: 写失败的测试**

测试里用一个按空格切词的假分词器（`encode` 把每个词映射成 id，`decode` 用空格拼回）。

```python
def test_text_has_exact_token_count():
    assert len(TOK.encode(make_text(TOK, 37, random.Random(0)), add_special_tokens=False)) == 37

def test_shared_sessions_share_prefix_unique_do_not():
    shared = build_sessions(TOK, 3, shared=True, seed=1, prefix_tokens=20, user_tokens=5, assistant_tokens=3)
    unique = build_sessions(TOK, 3, shared=False, seed=1, prefix_tokens=20, user_tokens=5, assistant_tokens=3)
    assert len({s.prefix for s in shared}) == 1 and len({s.prefix for s in unique}) == 3
    assert shared[0].users != shared[1].users

def test_same_seed_same_sessions():
    a = build_sessions(TOK, 2, shared=True, seed=7, prefix_tokens=20, user_tokens=5, assistant_tokens=3)
    assert a == build_sessions(TOK, 2, shared=True, seed=7, prefix_tokens=20, user_tokens=5, assistant_tokens=3)

def test_turn_messages_grow_with_history():
    s = build_sessions(TOK, 1, shared=True, seed=0, prefix_tokens=20, user_tokens=5, assistant_tokens=3)[0]
    roles = [m["role"] for m in messages_for_turn(s, 3)]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]

def test_context_over_limit_raises():
    with pytest.raises(ValueError):
        build_sessions(TOK, 1, shared=True, seed=0, prefix_tokens=60, user_tokens=10, assistant_tokens=5, max_context=100)

def test_role_only_chunk_is_not_first_token():
    assert not is_content_chunk({"choices": [{"delta": {"role": "assistant"}}]})
    assert not is_content_chunk({"choices": [{"delta": {"content": ""}}]})
    assert is_content_chunk({"choices": [{"delta": {"content": "好"}}]})

def test_percentile_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5

def test_failed_turn_stops_session_and_is_counted():
    recs = [TurnRecord(0, 1, True, 100, 900, 4100, 150), TurnRecord(0, 2, False, None, None, None, None),
            TurnRecord(1, 1, True, 120, 950, 4100, 150), TurnRecord(1, 2, True, 80, 800, 4750, 150)]
    rows = {r["turn"]: r for r in summarize(recs)}
    assert rows[2]["failed"] == 1 and rows[2]["ok"] == 1 and rows[2]["ttft_p50_ms"] == 80

def test_hit_rate_from_counter_deltas():
    before = 'vllm:prefix_cache_hits_total{model_name="q"} 100\nvllm:prefix_cache_queries_total{model_name="q"} 1000\n'
    after = 'vllm:prefix_cache_hits_total{model_name="q"} 600\nvllm:prefix_cache_queries_total{model_name="q"} 2000\n'
    assert hit_rate(before, after, *HIT_METRICS["vllm"]) == 0.5
    assert hit_rate(before, before, *HIT_METRICS["vllm"]) is None
```

`test_failed_turn_stops_session_and_is_counted` 另需一条运行层面的测试：用 `httpx.MockTransport` 让第 2 轮返回 500，断言该会话只产生 2 条记录（第 1 轮成功、第 2 轮失败），其余会话不受影响。

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/pytest tests/test_agent_workload.py -v`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现**

`make_text`：从固定词表（中英混合常见词）按 rng 取词，编码后截到 `n_tokens` 再解码，循环直到编码长度恰好等于 `n_tokens`。`build_sessions`：rng 由 `seed` 派生；共享组所有会话复用同一个前缀；生成后用分词器计最后一轮所有消息内容的 token 数，加每条消息 10 个 token 的模板开销，超过 `max_context` 抛 `ValueError`。运行时：`httpx.AsyncClient`（连接上限 = 并发），`POST /v1/chat/completions`，`stream: true`、`stream_options: {"include_usage": true}`、`max_tokens: 150`、`temperature: 0`、`ignore_eos: true`、`chat_template_kwargs: {"enable_thinking": false}`；TTFT 取第一个 `is_content_chunk` 的时刻；token 数取最后的 usage 块；任何异常或非 200 记为失败并结束该会话。用 `asyncio.Semaphore(concurrency)` 控制同时进行的会话数，会话内各轮串行。运行前后各取一次 `/metrics` 算命中率。输出 JSON：`args`、`turns`（`summarize` 结果）、`overall`（成功/失败请求数、总耗时、输出 tok/s）、`hit_rate`、`records`。`report` 汇总成 markdown 表：引擎、条件、并发、轮次、TTFT P50/P95、失败数，外加每个文件一行命中率。头部讲解写清负载模型和为什么历史回答用固定文本（Rulings 4）。

- [ ] **Step 4: 运行，确认通过**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat: 第 6 课 Agent 式多轮负载脚本"
```

## Task 3: 第 7 课——冷启动计时脚本和优化补丁

**Files:**
- Create: `scripts/coldstart.py`、`lessons/07-coldstart/compile-cache.patch.json`、`lessons/07-coldstart/enforce-eager.patch.json`
- Test: `tests/test_coldstart.py`、`tests/fixtures/coldstart/pod.json`、`tests/fixtures/coldstart/vllm.log`

**Interfaces:**
- Produces: `parse_ts(s: str) -> datetime`（接受最多 9 位小数和 `Z` 后缀，返回带时区的时间）；`pod_times(pod: dict) -> dict[str, datetime | None]`（键 `created`、`scheduled`、`started`、`ready`，分别取 `metadata.creationTimestamp`、`PodScheduled` 条件、`containerStatuses[0].state.running.startedAt`、`Ready` 条件）；`log_marks(log_text: str) -> dict[str, datetime | float | None]`（`load_done` 与 `load_seconds` 取 `Model loading took ... and <秒> seconds` 那一行，`engine_ready` 与 `compile_seconds` 取 `init engine ... took <秒> s (compilation: <秒> s)` 那一行）；`phases(pod: dict, marks: dict) -> dict[str, float | None]`，键依次为 `调度`、`容器启动`、`进程初始化`、`加载权重`、`引擎初始化`、`API 就绪`，以及 `总计`、`其中编译`；`median_phases(runs: list[dict]) -> dict`。
- Produces（CLI，GPU 机器上执行）：`python3 scripts/coldstart.py --label <条件> --runs 3 [--drop-caches] --out /data/results/coldstart/<条件>.json`。
- Consumes: 第一周的 `vllm` Deployment（`infer` 命名空间，`app=vllm`）。

- [ ] **Step 1: 准备 fixtures 并写失败的测试**

`vllm.log` 用 `kubectl logs --timestamps` 的格式，包含第一周实际出现过的两行：`2026-10-15T07:54:10.500000000Z (EngineCore pid=135) INFO 10-15 07:54:10 [model_runner.py:428] Model loading took 15.27 GiB memory and 7.213097 seconds` 和 `2026-10-15T07:55:05.250000000Z (EngineCore pid=135) INFO 10-15 07:55:05 [core.py:372] init engine (profile, create kv cache, warmup model) took 55.41 s (compilation: 35.13 s)`。`pod.json` 的时间：创建 07:53:58、调度 07:53:58、容器启动 07:54:00、Ready 07:55:12。

```python
def test_parses_nanosecond_timestamp():
    assert parse_ts("2026-10-15T07:54:10.123456789Z").microsecond == 123456

def test_phases_add_up_to_total():
    p = phases(json.loads(POD.read_text()), log_marks(LOG.read_text()))
    assert p["加载权重"] == pytest.approx(7.213, abs=0.01)
    assert p["其中编译"] == pytest.approx(35.13)
    parts = ["调度", "容器启动", "进程初始化", "加载权重", "引擎初始化", "API 就绪"]
    assert sum(p[k] for k in parts) == pytest.approx(p["总计"], abs=0.01)
    assert p["总计"] == pytest.approx(74.0, abs=0.01)

def test_missing_mark_gives_none_phase():
    p = phases(json.loads(POD.read_text()), log_marks("no useful lines\n"))
    assert p["加载权重"] is None and p["总计"] == pytest.approx(74.0, abs=0.01)

def test_median_of_three_runs():
    runs = [{"总计": 70.0}, {"总计": 90.0}, {"总计": 74.0}]
    assert median_phases(runs)["总计"] == 74.0
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/pytest tests/test_coldstart.py -v`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `coldstart.py` 和两个补丁**

`进程初始化` = `started` 到（`load_done` − `load_seconds`）；`引擎初始化` = `load_done` 到 `engine_ready`；`API 就绪` = `engine_ready` 到 `ready`；`总计` = `created` 到 `ready`；缺标记时依赖它的段为 `None`，`main` 打印缺了哪一行。CLI 每次运行：可选 `sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'`；`kubectl -n infer scale deploy/vllm --replicas=0`，等 `app=vllm` 的 Pod 全部消失；`--replicas=1`，`rollout status` 等就绪；`kubectl get pod -o json` 和 `kubectl logs --timestamps` 取数据；每次的六段和原始日志都写进输出 JSON（`runs`、`median`）。头部讲解写 Rulings 1、2，以及三项优化分别作用在哪一段。
补丁（`kubectl -n infer patch deploy vllm --type=json --patch-file ...`）：`compile-cache.patch.json` 加环境变量 `VLLM_CACHE_ROOT=/cache/vllm`、hostPath 卷 `/data/cache/vllm`（`DirectoryOrCreate`）挂到 `/cache/vllm`；`enforce-eager.patch.json` 在 args 末尾追加 `--enforce-eager`。

- [ ] **Step 4: 运行，确认通过**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat: 第 7 课 冷启动分段计时脚本和优化补丁"
```

## Task 4: 第 8 课——KEDA 扩缩容配置、时间线记录和副本数面板

**Files:**
- Create: `lessons/08-autoscaling/keda-2.21.0.yaml`（从 `/Users/huangzhipeng/Desktop/java/vllm-infra-lab-src/keda-2.21.0.yaml` 复制，不改动）、`lessons/08-autoscaling/scaledobject.yaml`、`scripts/autoscale_timeline.py`
- Modify: `lessons/03-monitoring/dashboards/vllm-overview.json`、`tests/test_dashboard.py`、`Makefile`
- Test: `tests/test_autoscale_timeline.py`、`tests/test_manifests.py`

**Interfaces:**
- Produces: ScaledObject `infer/vllm-queue`；`events(rows: list[dict], threshold: float = 5) -> dict`，`rows` 每行 `{"t": float, "replicas": int, "ready": int, "waiting_avg": float | None}`，返回 `t_cross`、`t_scale`、`t_ready`、`t_scale_down`（未发生为 `None`）和 `scale_delay_s`、`ready_delay_s`、`total_s`（任一端为 `None` 则为 `None`）。
- Produces（CLI，GPU 机器上执行）：`python3 scripts/autoscale_timeline.py record --duration 1800 --interval 5 --out /data/results/autoscaling/timeline.jsonl`（副本数取 `kubectl -n infer get deploy vllm -o json` 的 `spec.replicas`、`status.readyReplicas`；排队数用 `kubectl get --raw` 走 Service 代理查 Prometheus 的同一条查询）；`python3 scripts/autoscale_timeline.py summarize <timeline.jsonl>`。

- [ ] **Step 1: 写失败的测试**

```python
ROWS = [{"t": 0, "replicas": 1, "ready": 1, "waiting_avg": 0.0},
        {"t": 30, "replicas": 1, "ready": 1, "waiting_avg": 6.2},
        {"t": 45, "replicas": 2, "ready": 1, "waiting_avg": 9.0},
        {"t": 140, "replicas": 2, "ready": 2, "waiting_avg": 3.0},
        {"t": 900, "replicas": 1, "ready": 1, "waiting_avg": 0.0}]

def test_events_and_delays():
    e = events(ROWS)
    assert (e["t_cross"], e["t_scale"], e["t_ready"], e["t_scale_down"]) == (30, 45, 140, 900)
    assert (e["scale_delay_s"], e["ready_delay_s"], e["total_s"]) == (15, 95, 110)

def test_no_scale_gives_none():
    e = events([{"t": 0, "replicas": 1, "ready": 1, "waiting_avg": 7.0}])
    assert e["t_cross"] == 0 and e["t_scale"] is None and e["total_s"] is None
```

`tests/test_manifests.py`：ScaledObject 含 `minReplicaCount: 1`、`maxReplicaCount: 2`、`avg_over_time(sum(vllm:num_requests_waiting)[1m:])`、`threshold: "5"`、`stabilizationWindowSeconds: 300`、`serverAddress: http://prometheus.monitoring:9090`。`tests/test_dashboard.py`：`test_dashboard_identity` 改为 9 个面板，并断言有一个面板的表达式是 `count(vllm:num_requests_running)`。

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/pytest tests/test_autoscale_timeline.py tests/test_manifests.py tests/test_dashboard.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

ScaledObject：`scaleTargetRef.name: vllm`，`pollingInterval: 15`，Prometheus 触发器，`advanced.horizontalPodAutoscalerConfig.behavior.scaleDown.stabilizationWindowSeconds: 300`。头部讲解写：为什么选 KEDA 不选 HPA、为什么用 1 分钟平均（第一周排队数在 5 上下摆动）、Rulings 3、在一张 48G 卡上扩出第二个副本的做法和代价（time-slicing replicas 2，两个副本分时共享算力，KV 容量翻倍）、Service 随机分发对前缀缓存的影响、缩容到 0 为什么不做（副本为 0 时 vLLM 指标消失，需要改用网关侧指标触发）。看板在「③ 容量」行加「vLLM 副本数」面板。`Makefile` 的 kubeconform 加 `-skip ScaledObject`，`-ignore-filename-pattern` 加 `keda-2`。`events` 按时间顺序扫描：`t_cross` = 第一个 `waiting_avg > threshold`，`t_scale` = 第一个 `replicas >= 2`，`t_ready` = 第一个 `ready >= 2`，`t_scale_down` = `t_ready` 之后第一个 `replicas == 1`。

- [ ] **Step 4: 运行，确认通过**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat: 第 8 课 KEDA 扩缩容配置、时间线记录和副本数面板"
```

## Task 5: 网关探测按时间切分

**Files:**
- Modify: `scripts/gateway_probe.py`
- Test: `tests/test_gateway_probe.py`

**Interfaces:**
- Produces: `ProbeResult` 增加字段 `started_at: float = 0.0`（请求发出时的 Unix 时间，默认值保证第一周的调用不受影响）；`split_at(results: list[ProbeResult], t: float) -> tuple[dict[str, int], dict[str, int]]`，返回 `t` 之前、之后发出的请求各自的 `summarize` 结果。任务 9 用它统计新副本就绪前后各有多少请求转到百炼。

- [ ] **Step 1: 写失败的测试**

```python
def test_split_at_ready_time():
    rs = [ProbeResult(200, "bailian", 12.0, started_at=100.0), ProbeResult(200, "self-hosted", 3.0, started_at=101.0),
          ProbeResult(200, "self-hosted", 3.0, started_at=250.0)]
    before, after = split_at(rs, 200.0)
    assert before == {"bailian": 1, "self-hosted": 1} and after == {"self-hosted": 1}
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/pytest tests/test_gateway_probe.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

`probe` 在发请求前记 `time.time()`；输出 JSON 的 `results` 带上 `started_at`；已有调用方（第一周的结果文件）不受影响。

- [ ] **Step 4: 运行，确认通过并提交**

Run: `make check`
Expected: 全部通过。

```bash
git add -A && git commit -m "feat: 网关探测记录发出时间，可按时间点切分统计"
```

## Task 6: 第 9 天——部署 SGLang、对齐 KV 容量、跑标准负载

- [ ] **Step 1:** 同步仓库到 `/data/lab`；`kubectl apply -f lessons/06-sglang/sglang.yaml`；`kubectl -n infer scale deploy/vllm --replicas=0`，等 vllm Pod 消失后 `kubectl -n infer scale deploy/sglang --replicas=1`。Expected: Ready；启动日志里有 `max_total_num_tokens`。
- [ ] **Step 2:** 对齐 KV 容量：日志里的 token 数与 30,352 偏差超过 10% 时，按比例调整 `--mem-fraction-static` 后重建，直到偏差不超过 10%。每次尝试的参数和容量记入 `results/06-sglang/kv-alignment.md`。
- [ ] **Step 3:** 单发核对：经 bench-client 发一个 `/v1/completions` 请求带 `ignore_eos: true`、`max_tokens: 256`，确认输出恰好 256 token；发一个 `/v1/chat/completions` 请求带 `chat_template_kwargs: {"enable_thinking": false}`，确认没有思考内容。任一不成立就按规格书风险表改用 `sglang.bench_serving` 对两个引擎各跑一次，并在结果中注明。
- [ ] **Step 4:** 指标核对：`curl -s http://sglang.infer:8000/metrics > results/06-sglang/metrics.txt`，`python3 scripts/check_metrics.py results/06-sglang/metrics.txt lessons/06-sglang/expected-metrics.txt`。缺失时按实际指标名修正 `expected-metrics.txt` 和 `agent_workload.py` 的 `HIT_METRICS["sglang"]`（找一对「命中 token 数、提示 token 数」计数器；没有的话改用响应 usage 里的 `cached_tokens`，并改测试），在 Mac 上 `make check` 后提交。
- [ ] **Step 5:** 标准负载：`scripts/bench.sh sglang-baseline http://sglang.infer:8000 qwen3-8b`。Expected: 5 档全部 0 失败。Mac 上 rsync 后 `.venv/bin/python scripts/plot.py results/bench results/charts > results/bench/summary.md`，在 `results/06-sglang/compare.md` 写 vLLM `baseline` 与 `sglang-baseline` 的对比表（两边的 KV 容量写在表头），提交。

## Task 7: 第 10 天——Agent 式负载

- [ ] **Step 1:** SGLang 仍在运行。对 `shared`、`unique` 两组、并发 8、16、32 各跑一次 `agent_workload.py run --engine sglang`（`--base-url http://sglang.infer:8000`）。先用并发 8 冒烟，检查输出 JSON 的 `overall` 失败数为 0、最后一轮 `prompt_tokens` 在 7,500~8,192 之间、`hit_rate` 不为空。
- [ ] **Step 2:** 切回 vLLM（sglang 缩到 0，vllm 扩到 1，等 Ready），同样 6 次 `--engine vllm`。
- [ ] **Step 3:** Mac 上 rsync，`python3 scripts/agent_workload.py report results/agent > results/agent/summary.md`，在其中写结论：每个引擎共享前缀相对不共享前缀的 TTFT 收益、两个引擎之间的差别、命中率差别。提交。

## Task 8: 第 11 天——冷启动

- [ ] **Step 1:** 基准：`python3 scripts/coldstart.py --label baseline --runs 3 --out /data/results/coldstart/baseline.json`。Expected: 中位数各段齐全；「加载权重」接近第一周的 7 秒、「其中编译」接近 35 秒。
- [ ] **Step 2:** 冷盘：同上加 `--drop-caches`，label `drop-caches`。
- [ ] **Step 3:** 编译缓存：打 `compile-cache.patch.json`；先跑 1 次 label `compile-cache-first`，加 `--clear-compile-cache`（打补丁触发的重建已经把缓存写满，必须在旧 Pod 退出后清空），再跑 3 次 label `compile-cache-warm`。Expected: warm 的「其中编译」明显低于 baseline；写下 `/data/cache/vllm` 的大小。
- [ ] **Step 4（可砍）:** 用 `kubectl replace -f lessons/02-vllm/vllm-qwen3-8b.yaml` 去掉编译缓存补丁（`apply` 删不掉补丁加的字段），打 `enforce-eager.patch.json`，跑 3 次 label `enforce-eager`；再 `CONCURRENCIES=16 scripts/bench.sh eager http://vllm.infer:8000 qwen3-8b`，与 `baseline` 并发 16 的 ITL P95 对比。
- [ ] **Step 5:** 交叉核对：`kubectl -n infer exec deploy/bench-client -- vllm bench startup --help` 查看用法，按用法对 `/models/Qwen3-8B` 跑一次冷启动和热启动，结果存 `results/coldstart/bench-startup.txt`，与 `coldstart.py` 的「加载权重」「引擎初始化」对比。
- [ ] **Step 6:** `kubectl replace -f lessons/02-vllm/vllm-qwen3-8b.yaml` 恢复基线配置（不能用 `apply`，理由见 `coldstart.py` 头部）。Mac 上 rsync，写 `results/coldstart/summary.md`：各条件六段中位数对比表、每项优化省了哪一段多少秒、`--enforce-eager` 的启动收益与 ITL 代价。提交。
- [ ] **Step 7（可砍，投机解码）:** 给 vllm 的 args 追加 `--speculative-config '{"method":"ngram","num_speculative_tokens":4,"prompt_lookup_max":4}'`；`vllm bench serve --help=dataset-name` 确认可用的数据集类型，分别用 random 和一组「请原样复述下面这段文字」的提示词各跑并发 1、16，对比 ITL。结果存 `results/specdec/`，恢复基线后提交。

## Task 9: 第 12 天——单卡上的 KEDA 扩缩容与网关联动

- [ ] **Step 1:** 设备插件切 time-slicing（第一周的 `device-plugin-config-timeslice.yaml`，`rollout restart ds/nvidia-device-plugin`），确认 allocatable 为 2。`kubectl apply --server-side -f lessons/08-autoscaling/keda-2.21.0.yaml`，等 `keda` 命名空间的 3 个 Pod Ready。
- [ ] **Step 2:** 先手动把 vllm 扩到 2，确认第二个副本启动日志里的 KV 容量与 30,352 token 偏差不超过 10%（不够就按规格书风险表把两个副本都降到 0.42，结果中注明），然后缩回 1。
- [ ] **Step 3:** 更新看板 ConfigMap（含副本数面板），`kubectl apply -f lessons/08-autoscaling/scaledobject.yaml`，确认 `kubectl -n infer get hpa` 出现 KEDA 创建的 HPA 且目标指标有值。
- [ ] **Step 4:** 实验：先确认 vllm 是 1 个副本、时间线文件是新文件名（已存在会拒绝运行）；后台启动 `autoscale_timeline.py record --duration 1800`；稳态并发 8 跑 3 分钟（`vllm bench serve` 直连 vllm，`--max-concurrency 8`，请求数按 3 分钟估算）；阶跃并发 48 持续约 10 分钟（约 1,700 个请求）；同时经网关以并发 4 发 `gateway_probe.py --total 200 --out /results/autoscaling/gateway.json`；阶跃结束后回到并发 8，等到缩回 1 个副本。期间在 Grafana 截图：排队数上升、副本数从 1 到 2、排队数回落。
- [ ] **Step 5:** `autoscale_timeline.py summarize`，并用 `split_at(..., t_ready)` 统计新副本就绪前后转到百炼的请求数。写 `results/autoscaling/summary.md`：越过阈值 → KEDA 改副本数 → 新副本就绪的各段耗时、阶跃期间 TTFT P95、就绪前后的转出统计、单卡扩容的收益和代价。
- [ ] **Step 6:** 收尾：删除 ScaledObject，vllm 回到 1 个副本，设备插件切回 exclusive。提交。

## Task 10: vLLM 源码笔记（晚上，不需要 GPU）

- [ ] **Step 1:** Mac 上取得 v0.30.0 源码：`gh api repos/vllm-project/vllm/tarball/v0.30.0 > vllm.tar.gz` 解压到 `/Users/huangzhipeng/Desktop/java/vllm-infra-lab-src/vllm`（仓库外，不提交）。
- [ ] **Step 2:** `notes/vllm-source/01-scheduler.md`：按「要解决的问题、关键数据结构、调用路径、对应本项目的哪个现象」四段写；必须回答 `results/bench/knee.md` 待查问题 2，引用至少一个 `results/` 中的数字，代码位置写成 `vllm/<路径>:<行号>`。
- [ ] **Step 3:** `notes/vllm-source/02-kv-cache.md`：同样四段；必须回答待查问题 1。
- [ ] **Step 4（可砍）:** `notes/vllm-source/03-prefix-cache.md`：块哈希的计算、命中和淘汰，与 SGLang 基数树的对比，引用 `results/agent/summary.md` 的命中率。
- [ ] **Step 5:** 提交。

## Task 11: 汇总进 README 和简历

- [ ] **Step 1:** README 新增「第二周」章节：引擎对比、Agent 负载、冷启动、扩缩容四组结果（全部引用 `results/` 下的文件）、踩坑表补充、已知边界补充（单卡扩容不是真正的横向扩展），面试问答至少新增 5 题（vLLM 与 SGLang 怎么选、KV 容量怎么对齐、Agent 负载为什么看前缀缓存、冷启动哪段最长怎么优化、为什么用 KEDA 和 1 分钟平均、单卡扩容的收益和代价）。
- [ ] **Step 2:** 核对 README 中每个新数字在 `results/` 中的出处。
- [ ] **Step 3:** 起草简历条目，作者确认后再用 docs 工具写进在线简历的项目 3（链接见 `CLAUDE.md`）。
- [ ] **Step 4:** `make check` 通过后提交，更新 `CLAUDE.md` 当前状态。
