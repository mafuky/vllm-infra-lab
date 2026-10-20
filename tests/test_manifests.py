from pathlib import Path

LESSONS = Path("lessons")
ENGINE_IMAGES = ("vllm/vllm-openai", "lmsysorg/sglang")


def _engine_deployments():
    for path in sorted(LESSONS.rglob("*.yaml")):
        for doc in path.read_text(encoding="utf-8").split("\n---"):
            if "kind: Deployment" in doc and any(f"image: {i}" in doc for i in ENGINE_IMAGES):
                yield path, doc


def test_engine_pods_disable_service_links():
    # K8s 会把同命名空间每个 Service 注入成 <服务名>_PORT=tcp://IP:端口 这样的环境变量。
    # Service 叫 vllm 时注入的 VLLM_PORT 和 vLLM 自己读的 VLLM_PORT 撞名，引擎启动即报错退出。
    found = list(_engine_deployments())
    assert found, "lessons/ 里没找到跑推理引擎的 Deployment"
    missing = [str(p) for p, doc in found if "enableServiceLinks: false" not in doc]
    assert not missing, f"这些 Deployment 没有关 enableServiceLinks：{missing}"


def test_litellm_metrics_scrapable_without_master_key():
    # LiteLLM v1.102.1 默认给 /metrics 也加鉴权，Prometheus 采集拿到 401，看板面板⑧没有数据。
    # 不把 master key 交给 Prometheus（那是管理员密钥），而是只对集群内的指标端点关掉鉴权
    text = (LESSONS / "05-gateway" / "litellm-config.yaml").read_text(encoding="utf-8")
    assert "require_auth_for_metrics_endpoint: false" in text


def _sglang_doc():
    text = (LESSONS / "06-sglang" / "sglang.yaml").read_text(encoding="utf-8")
    return next(d for d in text.split("\n---") if "kind: Deployment" in d)


def test_sglang_matches_vllm_serving_contract():
    doc = _sglang_doc()
    for needle in ["--served-model-name", "qwen3-8b", "--context-length", "8192", "--enable-metrics",
                   "--mem-fraction-static", "type: Recreate", "nvidia.com/gpu: 1", "prometheus.io/scrape"]:
        assert needle in doc, needle


def test_scaledobject_follows_spec():
    text = (LESSONS / "08-autoscaling" / "scaledobject.yaml").read_text(encoding="utf-8")
    for needle in ["minReplicaCount: 1", "maxReplicaCount: 2", "avg_over_time(sum(vllm:num_requests_waiting)[1m:])",
                   'threshold: "5"', "stabilizationWindowSeconds: 300", "serverAddress: http://prometheus.monitoring:9090",
                   "name: vllm"]:
        assert needle in text, needle


def test_sglang_readiness_does_not_generate():
    # SGLang 的 /health 会真的生成 1 个 token：探针要排在推理队列后面，默认 1 秒超时必挂，
    # 高并发压测时 Pod 会被摘出 Service、请求全部失败。就绪探针改用不做推理的 /v1/models
    doc = _sglang_doc()
    readiness = doc[doc.index("readinessProbe:"):]
    startup = doc[doc.index("startupProbe:"):doc.index("readinessProbe:")]
    assert "path: /v1/models" in readiness.split("volumeMounts:")[0]
    assert "timeoutSeconds: 10" in startup
