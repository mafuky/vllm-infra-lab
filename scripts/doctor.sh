#!/usr/bin/env bash
# 环境自检：跑一下就知道现在卡在哪一步
#
# 在 GPU 机器上以普通用户执行（不需要 sudo）：
#
#   /data/lab/scripts/doctor.sh
#
# 只读，不改任何东西。每一项输出 ✅ 或 ❌，❌ 后面跟着修复提示。
# 后面几课部署的命名空间存在时，会顺带列出没有就绪的 Pod。
set -uo pipefail

ok() { echo "✅ $*"; }
bad() { echo "❌ $1"; [ -n "${2:-}" ] && echo "   → $2"; }

echo "== 节点 =="

if command -v nvidia-smi >/dev/null && ver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"; then
  if [ "${ver%%.*}" -ge 580 ]; then ok "NVIDIA 驱动 ${ver}"; else bad "NVIDIA 驱动 ${ver} 低于 580" "sudo scripts/setup-node.sh 会升级驱动"; fi
else
  bad "nvidia-smi 不可用" "sudo scripts/setup-node.sh"
fi

if command -v nvidia-ctk >/dev/null; then
  ok "NVIDIA Container Toolkit $(nvidia-ctk --version 2>/dev/null | head -1 | awk '{print $NF}')"
else
  bad "没有 nvidia-ctk" "sudo scripts/setup-node.sh"
fi

if systemctl is-active --quiet k3s; then ok "k3s 运行中"; else bad "k3s 没有运行" "sudo systemctl status k3s；没装过就跑 setup-node.sh"; fi

if ! kubectl get nodes >/dev/null 2>&1; then
  bad "kubectl 连不上集群" "检查 ~/.kube/config，setup-node.sh 第 6 步会复制它"
  exit 1
fi

if kubectl get runtimeclass nvidia >/dev/null 2>&1; then
  ok "RuntimeClass nvidia 存在"
else
  bad "没有 RuntimeClass nvidia" "kubectl apply -f lessons/01-gpu-scheduling/runtimeclass.yaml"
fi

gpus="$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null)"
if [ -n "$gpus" ] && [ "$gpus" != 0 ]; then
  ok "节点可分配 nvidia.com/gpu: ${gpus}"
else
  bad "节点上没有 nvidia.com/gpu" "部署设备插件：kubectl apply -f lessons/01-gpu-scheduling/device-plugin.yaml；已部署就看 kubectl -n gpu logs ds/nvidia-device-plugin"
fi

if [ -d /data/models ]; then
  models="$(find /data/models -mindepth 1 -maxdepth 1 -type d -printf '%f ' 2>/dev/null)"
  if [ -n "$models" ]; then ok "/data/models：${models}"; else bad "/data/models 是空的" "scripts/download-model.sh Qwen/Qwen3-8B"; fi
else
  bad "没有 /data/models" "sudo scripts/setup-node.sh"
fi

echo
echo "== 各命名空间的 Pod =="
for ns in gpu infer gateway monitoring; do
  kubectl get ns "$ns" >/dev/null 2>&1 || continue
  total=0; notready=0
  while read -r name ready status _; do
    total=$((total + 1))
    if [ "$status" = Completed ]; then continue; fi
    if [ "$status" != Running ] || [ "${ready%%/*}" != "${ready##*/}" ]; then
      notready=$((notready + 1))
      case "$status" in
        ImagePullBackOff|ErrImagePull)
          bad "${ns}/${name}：${status}" "镜像拉不下来：检查 /etc/rancher/k3s/registries.yaml，或在 Mac 上跑 scripts/check-images.sh 确认标签存在" ;;
        Pending)
          bad "${ns}/${name}：Pending" "kubectl -n ${ns} describe pod ${name} 看 Events（GPU 被占用时是 Insufficient nvidia.com/gpu）" ;;
        *)
          bad "${ns}/${name}：${status}（就绪 ${ready}）" "kubectl -n ${ns} logs ${name}" ;;
      esac
    fi
  done < <(kubectl -n "$ns" get pods --no-headers 2>/dev/null)
  if [ "$notready" -eq 0 ]; then ok "${ns}：${total} 个 Pod 全部就绪"; fi
done
