#!/usr/bin/env bash
# 第 4 课 · 一张卡到底能扛多少并发
#
# 4-6 小时（大部分时间是等压测跑完）。在 GPU 机器上执行。
#
# ── 你在做什么 ────────────────────────────────────────────────
#
# 第 2 课算过：显存限制在 0.45 时，KV Cache 只够同时服务二十来个请求。
# 这一课用数据验证这笔账：并发从 1 加到 64，看吞吐什么时候不再上涨、首字延迟什么时候开始陡增。
# 那个转折点就是「拐点」，也是第 5 课网关超时和第二周扩容阈值的依据。
#
#   scripts/bench.sh <label> <base_url> <模型名> [额外的 vllm bench serve 参数...]
#   scripts/bench.sh baseline http://vllm.infer:8000 qwen3-8b
#
# 每档并发 c 发 max(10c, 50) 个请求，输入 1024、输出 256 个 token（--ignore-eos 保证每个请求都输出满 256），
# 结果写到宿主机 /data/results/bench/<label>/c<c>.json。环境变量：
#   CONCURRENCIES  并发档位，默认 "1 4 16 32 64"
#   TOKENIZER      分词器目录，默认 /models/Qwen3-8B
#
# random 数据集走 /v1/completions，不经过聊天模板，Qwen3 的思考模式不会掺进来，几轮之间可比。
#
# ── 第 5 天的实验矩阵 ─────────────────────────────────────────
#
# 每改一次 Deployment 参数，等 Pod 重新 Ready 后跑一轮，label 用下面的名字：
#
#   baseline     显存 0.45，前缀缓存开（vllm-qwen3-8b.yaml 原样）
#   full48g      显存 0.90，不限显存的对照：CONCURRENCIES="1 4 16 32 64 128"
#   util040      显存 0.40：KV Cache 再少一点，拐点会提前多少
#   seqs16/32/64 --max-num-seqs 16、32、64：同时调度的请求数上限
#   prefix-on    前缀缓存开，所有请求共享 512 token 的前缀：
#                  scripts/bench.sh prefix-on http://vllm.infer:8000 qwen3-8b --random-prefix-len 512 --random-input-len 512
#   prefix-off   同上，但 Deployment 加 --no-enable-prefix-caching
#   awq          apply vllm-qwen3-8b-awq.yaml 后跑（可砍）
#
# 压到 32、64 并发时打开 Prometheus 的 Alerts 页，三条告警会依次进入 Firing，截图存到 results/screenshots/。
#
# 全部跑完后在 Mac 上：
#   rsync -av $GPU_HOST:/data/results/ results/
#   .venv/bin/python scripts/plot.py results/bench results/charts > results/bench/summary.md
set -euo pipefail

[ "$#" -ge 3 ] || { echo "用法：$0 <label> <base_url> <模型名> [额外参数...]"; exit 1; }
label="$1"; base_url="$2"; model="$3"; shift 3

CONCURRENCIES="${CONCURRENCIES:-1 4 16 32 64}"
TOKENIZER="${TOKENIZER:-/models/Qwen3-8B}"

kubectl -n infer exec deploy/bench-client -- mkdir -p "/results/bench/${label}"

for c in $CONCURRENCIES; do
  n=$((c * 10))
  [ "$n" -lt 50 ] && n=50
  echo "==> ${label}：并发 ${c}，共 ${n} 个请求"
  kubectl -n infer exec deploy/bench-client -- vllm bench serve \
    --backend openai --base-url "$base_url" --model "$model" --tokenizer "$TOKENIZER" \
    --dataset-name random --random-input-len 1024 --random-output-len 256 --ignore-eos \
    --num-prompts "$n" --max-concurrency "$c" \
    --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,95 \
    --save-result --result-dir "/results/bench/${label}" --result-filename "c${c}.json" \
    "$@"
done

echo "完成：/data/results/bench/${label}/"
