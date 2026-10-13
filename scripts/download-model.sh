#!/usr/bin/env bash
# 从 ModelScope 下载模型到 /data/models
#
# 在 GPU 机器上以普通用户执行（不需要 GPU，可以在无卡模式下跑，省钱）：
#
#   scripts/download-model.sh Qwen/Qwen3-8B Qwen/Qwen3-8B-AWQ
#
# 每个模型下载到 /data/models/<模型 ID 斜杠后面的部分>，例如 /data/models/Qwen3-8B。
# 目录里已经有 config.json 就当作下载完成，跳过；中断后重跑会接着下载。
set -euo pipefail

[ "$#" -ge 1 ] || { echo "用法：$0 <ModelScope 模型 ID>..."; exit 1; }

if ! command -v modelscope >/dev/null && [ ! -x "${HOME}/.local/bin/modelscope" ]; then
  python3 -m pip --version >/dev/null 2>&1 || { echo "❌ 没有 pip：sudo apt-get install -y python3-pip"; exit 1; }
  python3 -m pip install --user "modelscope>=1.25,<2"
fi
MS="$(command -v modelscope || echo "${HOME}/.local/bin/modelscope")"

for id in "$@"; do
  dir="/data/models/${id##*/}"
  if [ -f "${dir}/config.json" ]; then
    echo "✅ ${id} 已存在：${dir}"
    continue
  fi
  echo "==> 下载 ${id} 到 ${dir}"
  "$MS" download --model "$id" --local_dir "$dir"
done
du -sh /data/models/* 2>/dev/null || true
