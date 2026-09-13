#!/usr/bin/env bash
set -euo pipefail
SNAP="$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-VL-8B-Instruct-GGUF/snapshots/f982a07559d4a2f6c8744d840bf6fccab30eea96"
MODEL="${VLM_MODEL:-$SNAP/Qwen3VL-8B-Instruct-Q4_K_M.gguf}"
MMPROJ="${VLM_MMPROJ:-$SNAP/mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf}"
PORT="${VLM_PORT:-8088}"
PARALLEL="${VLM_PARALLEL:-1}"          # receipt-evidence run --workers 와 같은 값
CTX="${VLM_CTX:-$((12288 * PARALLEL))}"  # 슬롯당 약 12k 토큰
mkdir -p out
exec llama-server -m "$MODEL" --mmproj "$MMPROJ" --host 127.0.0.1 --port "$PORT" \
  -c "$CTX" -ngl 99 -np "$PARALLEL" --jinja --temp 0 --alias qwen3-vl --no-warmup > out/vlm.log 2>&1
