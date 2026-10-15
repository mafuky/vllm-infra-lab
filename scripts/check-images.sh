#!/usr/bin/env bash
# 镜像存在性检查（在 Mac 上跑，开 GPU 之前做）
#
# ── 为什么要有它 ──────────────────────────────────────────────
#
# 版本号写错一个字，Pod 就会卡在 ImagePullBackOff。在 GPU 机器上才发现，
# 等于花钱排查一个本可以在 Mac 上一秒钟查出来的笔误。
#
# ── 怎么执行 ──────────────────────────────────────────────────
#
#   scripts/check-images.sh
#
# 逐个对 lessons/ 下所有 YAML 里的 image: 执行 skopeo inspect，只查清单不下载镜像。
# 全部存在退出码 0；任一不存在，列出来并以退出码 1 结束。
set -euo pipefail

cd "$(dirname "$0")/.."

# macOS 自带的 bash 3.2 没有 mapfile，用 while read 收集
images=()
while IFS= read -r ref; do
  images+=("$ref")
done < <(grep -rhoE '^[[:space:]]*image:[[:space:]]*["'\'']?[^"'\''[:space:]]+' lessons --include='*.yaml' \
  | sed -E 's/^[[:space:]]*image:[[:space:]]*["'\'']?//' | sort -u)

if [ "${#images[@]}" -eq 0 ]; then
  echo "lessons/ 下没有找到 image: 字段"
  exit 0
fi

missing=0
for ref in "${images[@]}"; do
  if skopeo inspect --raw "docker://${ref}" >/dev/null 2>&1; then
    echo "✅ ${ref}"
  else
    echo "❌ ${ref}"
    missing=$((missing + 1))
  fi
done

if [ "$missing" -gt 0 ]; then
  echo "${missing} 个镜像不存在或无法访问"
  exit 1
fi
