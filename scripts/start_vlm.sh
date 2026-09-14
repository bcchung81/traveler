#!/usr/bin/env bash
# 영수증을 읽는 로컬 AI(llama-server). 운영 기본 모델은 Qwen3-VL 4B — src/receipt_evidence/vlm_models.py와 같은 값.
#   VLM_VARIANT=4b(기본) | 8b      VLM_MODEL·VLM_MMPROJ로 파일을 직접 지정할 수도 있다
#   VLM_DRY_RUN=1 이면 실행하지 않고 쓸 모델 경로만 출력, VLM_LOG로 로그 파일 지정(기본 out/vlm.log)
set -euo pipefail
VARIANT="${VLM_VARIANT:-4b}"
case "$VARIANT" in
  4b) REPO="Qwen/Qwen3-VL-4B-Instruct-GGUF"; MODEL_FILE="Qwen3VL-4B-Instruct-Q4_K_M.gguf"; MMPROJ_FILE="mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf" ;;
  8b) REPO="Qwen/Qwen3-VL-8B-Instruct-GGUF"; MODEL_FILE="Qwen3VL-8B-Instruct-Q4_K_M.gguf"; MMPROJ_FILE="mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf" ;;
  *) echo "VLM_VARIANT는 4b 또는 8b여야 해요: $VARIANT" >&2; exit 2 ;;
esac
HUB="${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub}"
SNAP=""
for d in "$HUB/models--${REPO//\//--}/snapshots/"*/; do
  if [ -f "$d$MODEL_FILE" ] && [ -f "$d$MMPROJ_FILE" ]; then SNAP="${d%/}"; break; fi
done
MODEL="${VLM_MODEL:-${SNAP:+$SNAP/$MODEL_FILE}}"
MMPROJ="${VLM_MMPROJ:-${SNAP:+$SNAP/$MMPROJ_FILE}}"
if [ -z "$MODEL" ] || [ -z "$MMPROJ" ] || [ ! -f "$MODEL" ] || [ ! -f "$MMPROJ" ]; then
  echo "로컬 AI 모델 파일을 찾지 못했어요($VARIANT: $REPO $MODEL_FILE). huggingface-cli download $REPO $MODEL_FILE $MMPROJ_FILE" >&2
  exit 1
fi
PORT="${VLM_PORT:-8088}"
PARALLEL="${VLM_PARALLEL:-1}"          # receipt-evidence run --workers 와 같은 값
CTX="${VLM_CTX:-$((12288 * PARALLEL))}"  # 슬롯당 약 12k 토큰
if [ "${VLM_DRY_RUN:-0}" = "1" ]; then
  echo "variant=$VARIANT"; echo "model=$MODEL"; echo "mmproj=$MMPROJ"; exit 0
fi
LOG="${VLM_LOG:-out/vlm.log}"
mkdir -p "$(dirname "$LOG")"
exec llama-server -m "$MODEL" --mmproj "$MMPROJ" --host 127.0.0.1 --port "$PORT" \
  -c "$CTX" -ngl 99 -np "$PARALLEL" --jinja --temp 0 --alias qwen3-vl --no-warmup > "$LOG" 2>&1
