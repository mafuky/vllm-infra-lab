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
