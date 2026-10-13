#!/usr/bin/env bash
# 第 1 课 · 把一台裸 GPU 虚机变成单节点 k3s
#
# 30 分钟（含一次重启）。在 GPU 机器上执行：
#
#   sudo /data/lab/scripts/setup-node.sh
#
# 脚本可以反复运行，已经完成的步骤会跳过。
#
# ── 它做了什么 ────────────────────────────────────────────────
#
#   1. 驱动：出厂是 570，vLLM v0.30.0 的镜像基于 CUDA 13，要求 580 以上。
#      出厂驱动是用 NVIDIA 官方 .run 装的，所以升级也用 .run（.run 会自动卸载旧版）；
#      混用 apt 的 nvidia-driver-* 包会导致两套驱动打架。装完需要重启，重启后再跑一次本脚本。
#   2. NVIDIA Container Toolkit：让容器运行时能把 GPU 和驱动库注入容器。
#      必须先于 k3s 安装：k3s 启动时检测到 nvidia-container-runtime，才会注册 nvidia 运行时。
#   3. 镜像加速：registries.yaml 放到 k3s 读取的位置。
#   4. k3s：用国内镜像安装，系统组件镜像从阿里云拉，不装用不到的 traefik。
#   5. 目录：/data/models（模型）、/data/results（压测结果）、/data/lab（本仓库）、
#      /data/prometheus（Prometheus 以 65534 用户运行，属主要对）。
#   6. kubeconfig：复制给 sudo 前的那个用户，并在 ~/.bashrc 里设置 KUBECONFIG。
#      k3s 自带的 kubectl 在没有 KUBECONFIG 时会去读 /etc/rancher/k3s/k3s.yaml，那个文件只有 root 能读；
#      设了 KUBECONFIG 之后，重新登录（或 source ~/.bashrc）就能不加 sudo 用 kubectl。
#
# ── 应该看到什么 ──────────────────────────────────────────────
#
# 最后一行提示运行 scripts/doctor.sh；doctor 里驱动、toolkit、k3s、RuntimeClass 都是 ✅。
set -euo pipefail

DRIVER_VERSION=580.178.04
TOOLKIT_VERSION=1.20.1-1
K3S_VERSION=v1.35.8+k3s1
LAB_DIR="$(cd "$(dirname "$0")/.." && pwd)"

die() { echo "❌ $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || die "请用 sudo 运行"
[ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] || die "请以普通用户（如 ubuntu）执行 sudo，需要 SUDO_USER 来设置目录属主和 kubeconfig"
USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"

# 只在拿到形如 580.178.04 的版本号时才输出主版本号；nvidia-smi 出错时会把错误信息打到标准输出，
# 不校验的话错误文本会被当成版本号，坏掉的驱动反而显示成 ✅
driver_major() {
  local v
  v="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"
  [[ "$v" =~ ^[0-9]+\. ]] || return 1
  echo "${v%%.*}"
}

step "1/6 NVIDIA 驱动"
major="$(driver_major)" || major=""
if [ -z "$major" ] || [ "$major" -lt 580 ]; then
  echo "当前驱动：${major:-无}，升级到 ${DRIVER_VERSION}"
  apt-get update
  # dkms：内核自动升级后重启时会自动重编驱动模块，不用重新跑 .run
  apt-get install -y build-essential dkms "linux-headers-$(uname -r)"
  run="/tmp/NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run"
  curl -fL --retry 3 -o "$run" \
    "https://download.nvidia.com/XFree86/Linux-x86_64/${DRIVER_VERSION}/NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run"
  systemctl stop nvidia-persistenced 2>/dev/null || true
  sh "$run" --silent --dkms
  echo
  echo "驱动已安装。请执行 sudo reboot，重启后重新运行本脚本。"
  exit 0
fi
echo "✅ 驱动 $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"

step "2/6 NVIDIA Container Toolkit ${TOOLKIT_VERSION}"
runtime_changed=0
add_toolkit_repo() {
  local base="$1"
  curl -fsSL --max-time 30 "${base}/gpgkey" \
    | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg || return 1
  curl -fsSL --max-time 30 "${base}/stable/deb/nvidia-container-toolkit.list" \
    | sed "s#deb https://nvidia.github.io/libnvidia-container#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] ${base}#g" \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
}
if dpkg-query -W -f='${Version}' nvidia-container-toolkit 2>/dev/null | grep -qx "$TOOLKIT_VERSION"; then
  echo "✅ 已安装"
else
  # 官方源在 GitHub Pages 上，国内经常连不上，失败就换中科大的镜像
  add_toolkit_repo https://nvidia.github.io/libnvidia-container \
    || add_toolkit_repo https://mirrors.ustc.edu.cn/libnvidia-container \
    || die "两个 toolkit 软件源都连不上"
  apt-get update
  apt-get install -y \
    "nvidia-container-toolkit=${TOOLKIT_VERSION}" \
    "nvidia-container-toolkit-base=${TOOLKIT_VERSION}" \
    "libnvidia-container-tools=${TOOLKIT_VERSION}" \
    "libnvidia-container1=${TOOLKIT_VERSION}"
  runtime_changed=1
fi

step "3/6 镜像加速"
mkdir -p /etc/rancher/k3s
if ! cmp -s "${LAB_DIR}/lessons/01-gpu-scheduling/registries.yaml" /etc/rancher/k3s/registries.yaml; then
  install -m 0644 "${LAB_DIR}/lessons/01-gpu-scheduling/registries.yaml" /etc/rancher/k3s/registries.yaml
  runtime_changed=1
  echo "已更新 /etc/rancher/k3s/registries.yaml"
else
  echo "✅ 未变化"
fi

step "4/6 k3s ${K3S_VERSION}"
if command -v k3s >/dev/null && k3s --version | grep -qF "$K3S_VERSION"; then
  echo "✅ 已安装"
  if [ "$runtime_changed" -eq 1 ]; then
    echo "toolkit 或镜像配置有变化，重启 k3s 使其生效"
    systemctl restart k3s
  fi
else
  curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh \
    | INSTALL_K3S_MIRROR=cn INSTALL_K3S_VERSION="$K3S_VERSION" \
      INSTALL_K3S_EXEC="--disable=traefik --system-default-registry=registry.cn-hangzhou.aliyuncs.com" sh -
fi
echo "等待节点就绪..."
for _ in $(seq 1 60); do
  if k3s kubectl get nodes 2>/dev/null | grep -q " Ready "; then break; fi
  sleep 5
done
k3s kubectl get nodes | grep -q " Ready " || die "k3s 节点 5 分钟内没有就绪，查看 journalctl -u k3s"

step "5/6 数据目录"
install -d -o "$SUDO_USER" -g "$SUDO_USER" /data/models /data/results /data/lab
install -d -o 65534 -g 65534 /data/prometheus
echo "✅ /data/models /data/results /data/lab /data/prometheus"

step "6/6 kubeconfig"
install -d -o "$SUDO_USER" -g "$SUDO_USER" -m 700 "${USER_HOME}/.kube"
install -o "$SUDO_USER" -g "$SUDO_USER" -m 600 /etc/rancher/k3s/k3s.yaml "${USER_HOME}/.kube/config"
# 写进 .bashrc 的是字面量 $HOME，由用户登录时展开
# shellcheck disable=SC2016
if ! grep -qF 'export KUBECONFIG=$HOME/.kube/config' "${USER_HOME}/.bashrc" 2>/dev/null; then
  echo 'export KUBECONFIG=$HOME/.kube/config' >> "${USER_HOME}/.bashrc"
  chown "$SUDO_USER":"$SUDO_USER" "${USER_HOME}/.bashrc"
fi
echo "✅ ${USER_HOME}/.kube/config（已在 ~/.bashrc 设置 KUBECONFIG，重新登录或 source ~/.bashrc 后生效）"

echo
echo "完成。下一步：${LAB_DIR}/scripts/doctor.sh"
