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
