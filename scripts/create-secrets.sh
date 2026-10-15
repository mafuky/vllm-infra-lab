#!/usr/bin/env bash
# 从 .env 创建 K8s Secret（在 GPU 机器上执行，可重复执行）
#
#   cp .env.example .env && vim .env      # 填好密钥
#   scripts/create-secrets.sh
#
# 每个 Secret 只放自己需要的键：
#   monitoring/grafana-admin     admin-password
#   gateway/litellm-secrets      DASHSCOPE_API_KEY、LITELLM_MASTER_KEY
#
# 密钥只存在于本机 .env 和集群的 Secret 里，不进 Git，脚本也不会把值打印出来。
set -euo pipefail

# k3s 自带的 kubectl 在没有 KUBECONFIG 时读 root 才能读的 /etc/rancher/k3s/k3s.yaml
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "❌ 没有 .env：cp .env.example .env 后填写"; exit 1; }

# 按行读取 KEY=VALUE，不把 .env 当脚本执行
env_value() {
  grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/^["'\'']//; s/["'\'']$//'
}

require() {
  local v
  v="$(env_value "$1")"
  [ -n "$v" ] || { echo "❌ .env 里 $1 为空" >&2; exit 1; }
  printf '%s' "$v"
}

grafana_pw="$(require GRAFANA_ADMIN_PASSWORD)"

kubectl -n monitoring create secret generic grafana-admin \
  --from-literal=admin-password="$grafana_pw" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "✅ 已创建 monitoring/grafana-admin"

# 网关第 6 天才用；百炼 key 还没填时先跳过，第 4 天只需要 Grafana 密码
dashscope="$(env_value DASHSCOPE_API_KEY)"
master="$(env_value LITELLM_MASTER_KEY)"
if [ -z "$dashscope" ] || [ -z "$master" ]; then
  echo "⚠️  .env 里 DASHSCOPE_API_KEY 或 LITELLM_MASTER_KEY 为空，跳过 gateway/litellm-secrets（第 6 天前填好再跑一次）"
  exit 0
fi
kubectl -n gateway create secret generic litellm-secrets \
  --from-literal=DASHSCOPE_API_KEY="$dashscope" \
  --from-literal=LITELLM_MASTER_KEY="$master" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "✅ 已创建 gateway/litellm-secrets"
