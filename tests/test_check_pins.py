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
