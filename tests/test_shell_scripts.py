import re
import os
import stat
import subprocess
from pathlib import Path

SCRIPTS = Path("scripts")
KUBECONFIG_DEFAULT = 'export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"'


def test_scripts_using_kubectl_default_kubeconfig():
    # k3s 自带的 kubectl 在没有 KUBECONFIG 时读 root 才能读的 /etc/rancher/k3s/k3s.yaml
    for path in SCRIPTS.glob("*.sh"):
        text = path.read_text(encoding="utf-8")
        if path.name == "setup-node.sh" or "kubectl " not in text:
            continue
        assert KUBECONFIG_DEFAULT in text, path.name


def test_setup_node_exports_kubeconfig_for_login_shells():
    text = (SCRIPTS / "setup-node.sh").read_text(encoding="utf-8")
    assert "export KUBECONFIG=$HOME/.kube/config" in text
    assert ".bashrc" in text


def _fake_bin(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def _driver_major(tmp_path, nvidia_smi_body):
    _fake_bin(tmp_path, "nvidia-smi", nvidia_smi_body)
    func = subprocess.run(["sed", "-n", "/^driver_major()/,/^}/p", str(SCRIPTS / "setup-node.sh")],
                          capture_output=True, text=True, check=True).stdout
    assert func, "setup-node.sh 里找不到 driver_major 函数"
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    return subprocess.run(["bash", "-c", func + "\ndriver_major"], capture_output=True, text=True, env=env)


def test_driver_major_parses_version(tmp_path):
    r = _driver_major(tmp_path, 'echo "580.178.04"')
    assert r.returncode == 0
    assert r.stdout.strip() == "580"


def test_driver_major_rejects_error_output(tmp_path):
    r = _driver_major(tmp_path, 'echo "Failed to initialize NVML: Driver/library version mismatch"; exit 18')
    assert r.returncode != 0
    assert r.stdout.strip() == ""


def _run_download(tmp_path, models_dir):
    log = tmp_path / "calls.log"
    _fake_bin(tmp_path, "modelscope", f'echo "$@" >> "{log}"')
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", MODELS_DIR=str(models_dir))
    subprocess.run(["bash", str(SCRIPTS / "download-model.sh"), "Qwen/Qwen3-8B"], env=env, check=True,
                   capture_output=True, text=True)
    return log.read_text() if log.exists() else ""


def test_download_resumes_when_only_config_exists(tmp_path):
    models = tmp_path / "models"
    (models / "Qwen3-8B").mkdir(parents=True)
    (models / "Qwen3-8B" / "config.json").write_text("{}")
    calls = _run_download(tmp_path, models)
    assert "Qwen/Qwen3-8B" in calls
    assert (models / "Qwen3-8B" / ".download-complete").exists()


def test_download_skips_when_marked_complete(tmp_path):
    models = tmp_path / "models"
    (models / "Qwen3-8B").mkdir(parents=True)
    (models / "Qwen3-8B" / ".download-complete").write_text("")
    assert _run_download(tmp_path, models) == ""


def _has_nvidia_gpu(tmp_path, vendors):
    pci = tmp_path / "pci"
    for i, vendor in enumerate(vendors):
        (pci / f"0000:00:0{i}.0").mkdir(parents=True)
        (pci / f"0000:00:0{i}.0" / "vendor").write_text(vendor + "\n")
    pci.mkdir(exist_ok=True)
    func = subprocess.run(["sed", "-n", "/^has_nvidia_gpu()/,/^}/p", str(SCRIPTS / "setup-node.sh")],
                          capture_output=True, text=True, check=True).stdout
    assert func, "setup-node.sh 里找不到 has_nvidia_gpu 函数"
    env = dict(os.environ, PCI_DEVICES_DIR=str(pci))
    return subprocess.run(["bash", "-c", func + "\nhas_nvidia_gpu"], capture_output=True, text=True, env=env)


def test_gpu_detected_by_pci_vendor(tmp_path):
    # 0x10de 是 NVIDIA 的 PCI 厂商号；不依赖驱动，驱动坏了也能判断卡在不在
    assert _has_nvidia_gpu(tmp_path, ["0x1af4", "0x10de"]).returncode == 0


def test_no_gpu_in_cardless_mode(tmp_path):
    # 无卡模式开机时虚机上只剩 virtio 等设备
    assert _has_nvidia_gpu(tmp_path, ["0x1af4", "0x8086"]).returncode != 0


def _run_create_secrets(tmp_path, env_text):
    # 脚本会 cd 到自己上一级目录读 .env，所以复制到临时目录里跑，不碰仓库里真正的 .env
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "create-secrets.sh"
    script.write_text((SCRIPTS / "create-secrets.sh").read_text(encoding="utf-8"))
    (tmp_path / ".env").write_text(env_text)
    log = tmp_path / "kubectl.log"
    fakes = tmp_path / "bin"
    fakes.mkdir()
    _fake_bin(fakes, "kubectl", f'echo "$@" >> "{log}"; [ "$1" = apply ] && cat >/dev/null; true')
    env = dict(os.environ, PATH=f"{fakes}:{os.environ['PATH']}", KUBECONFIG="/dev/null")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
    return r, (log.read_text() if log.exists() else "")


def test_create_secrets_skips_gateway_without_dashscope_key(tmp_path):
    # 第 4 天只需要 Grafana 密码，百炼的 key 第 6 天才用得上
    r, calls = _run_create_secrets(tmp_path, "GRAFANA_ADMIN_PASSWORD=pw\nLITELLM_MASTER_KEY=\nDASHSCOPE_API_KEY=\n")
    assert r.returncode == 0, r.stderr
    assert "grafana-admin" in calls
    assert "litellm-secrets" not in calls
    assert "DASHSCOPE_API_KEY" in r.stdout + r.stderr


def test_create_secrets_requires_grafana_password(tmp_path):
    r, calls = _run_create_secrets(tmp_path, "GRAFANA_ADMIN_PASSWORD=\n")
    assert r.returncode != 0
    assert "grafana-admin" not in calls


def test_create_secrets_creates_both_when_complete(tmp_path):
    r, calls = _run_create_secrets(tmp_path, "GRAFANA_ADMIN_PASSWORD=pw\nLITELLM_MASTER_KEY=sk-x\nDASHSCOPE_API_KEY=sk-y\n")
    assert r.returncode == 0, r.stderr
    assert "grafana-admin" in calls and "litellm-secrets" in calls


def test_bench_uses_distinct_seed_per_concurrency():
    # 不传 --seed 时每档并发生成同一批「随机」提示词，开着前缀缓存会跨档命中，低并发的 TTFT 被低估
    text = (SCRIPTS / "bench.sh").read_text(encoding="utf-8")
    assert re.search(r'--seed "\$\(\(\s*\w+\s*\+\s*c\s*\)\)"', text), "bench.sh 要按并发档位传不同的 --seed"
