#!/usr/bin/env bash
# Pilot v3: on-policy correction (base drafts -> teacher fixes) -> train -> export -> same 73-item eval as v2.
# Run detached:  tmux new -d -s v3 'bash ~/offline-tutor/train/run_pilot_v3.sh'
# Progress:      ~/offline-tutor/run/pilot_v3/status.json (+ DONE / FAILED); step status in data/pilot/onpolicy/
set -uo pipefail
ROOT=~/offline-tutor
RUN=$ROOT/run/pilot_v3
PY=$ROOT/env/bin/python
O=data/pilot/onpolicy
E=run/eval_pilot
mkdir -p "$RUN"
rm -f "$RUN/DONE" "$RUN/FAILED"
exec >>"$RUN/v3.log" 2>&1
cd "$ROOT"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True PARALLEL=6  # teacher: 6 x 12k slots fit in 24 GB

status() { printf '{"step":"%s","time":"%s"}\n' "$1" "$(date -Is)" > "$RUN/status.json"; echo "== $1 $(date -Is)"; }
fail() { status "FAILED: $1"; touch "$RUN/FAILED"; exit 1; }

status "rebuild_v2"   # adds verified numeric answers to each numerical example (no teacher calls)
$PY train/gen.py build data/pilot/sections.jsonl data/pilot/gen || fail "rebuild v2"
status "draft"
$PY train/onpolicy.py draft data/pilot/gen/sft.jsonl $O export/gemma-base/gemma-base-Q4_K_M.gguf || fail "draft"
status "fix"
$PY train/onpolicy.py fix data/pilot/gen/sft.jsonl data/pilot/sections.jsonl $O || fail "fix"
status "build"
$PY train/onpolicy.py build data/pilot/gen/sft.jsonl $O || fail "build"
status "sft"
$PY train/sft.py train/configs/pilot_v3.json || fail "sft"
status "export"
bash train/export_gguf.sh run/sft_pilot_v3/best tutor-v3 || fail "export"
status "eval"
$PY train/eval_pilot.py answer $E tutor-v3 export/tutor-v3/tutor-v3-Q4_K_M.gguf || fail "eval answer"
$PY train/eval_pilot.py judge $E || fail "eval judge"
$PY train/eval_pilot.py report $E data/pilot/sections.jsonl || fail "eval report"
status "DONE"
touch "$RUN/DONE"
