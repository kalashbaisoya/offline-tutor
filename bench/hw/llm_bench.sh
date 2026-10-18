#!/usr/bin/env bash
# Phase 0 hardware probe: LLM prompt-processing / generation throughput and VRAM
# on the local GPU, using llama.cpp's llama-bench.
#
# Usage: LLAMA_BIN=/path/to/llama.cpp/build/bin bench/hw/llm_bench.sh [models_dir] [out_dir]
#
# Prompt sizes approximate the tutor workload:
#   512  = short chat turn
#   3072 = system prompt + teaching policy + ~5 retrieved chunks + history
# TTFT can be estimated as prompt_tokens / pp_tokens_per_sec (plus sampling overhead).
set -euo pipefail

LLAMA_BIN=${LLAMA_BIN:?set LLAMA_BIN to the llama.cpp build/bin directory}
MODELS=${1:-models}
OUT=${2:-bench/hw/results}
mkdir -p "$OUT"
STAMP=$(date +%Y%m%dT%H%M%S)

{
  echo "# host"
  lscpu | grep -E 'Model name' || true
  free -h | head -2
  nvidia-smi --query-gpu=name,memory.total,driver_version,power.limit --format=csv
  (cd "$LLAMA_BIN/../.." && git log -1 --format='llama.cpp %h %cd') || true
} > "$OUT/env_$STAMP.txt"

# Sample peak VRAM in the background while benchmarks run.
nvidia-smi --query-gpu=timestamp,memory.used,power.draw,clocks.sm --format=csv -lms 250 \
  > "$OUT/gpu_trace_$STAMP.csv" &
SMI_PID=$!
trap 'kill $SMI_PID 2>/dev/null || true' EXIT

run() { # model ngl label
  echo ">> $1 ngl=$2" >&2
  "$LLAMA_BIN/llama-bench" -m "$1" -ngl "$2" -fa on -p 512,3072 -n 128 -r 3 -t 8 -o jsonl \
    2>>"$OUT/stderr_$STAMP.log" | sed "s/^{/{\"label\":\"$3\",/" >> "$OUT/llm_$STAMP.jsonl" || echo "FAILED $1 ngl=$2" >&2
}

for m in "$MODELS"/*.gguf; do
  name=$(basename "$m" .gguf)
  run "$m" 99 "${name}_gpu-all"   # full offload (may OOM on 4 GB for >4B models)
  run "$m" 0  "${name}_cpu-only"  # CPU fallback
done
# Partial-offload sweep for models that don't fit.
for m in "$MODELS"/Qwen3.5-9B*.gguf "$MODELS"/gemma-4-E4B*.gguf; do
  [ -f "$m" ] || continue
  name=$(basename "$m" .gguf)
  for ngl in 12 20 28; do run "$m" "$ngl" "${name}_ngl$ngl"; done
done

echo "results: $OUT/llm_$STAMP.jsonl"
