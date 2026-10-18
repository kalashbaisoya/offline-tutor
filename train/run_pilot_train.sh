#!/usr/bin/env bash
# Pilot, part 2 (after run_pilot.sh): replay data -> LoRA training -> Q4_K_M export.
# Run detached:  tmux new -d -s train 'bash ~/offline-tutor/train/run_pilot_train.sh'
# Progress:      ~/offline-tutor/run/pilot_train/status.json (+ DONE / FAILED); training curve in run/sft_pilot/
set -uo pipefail
ROOT=~/offline-tutor
RUN=$ROOT/run/pilot_train
PY=$ROOT/env/bin/python
mkdir -p "$RUN"
rm -f "$RUN/DONE" "$RUN/FAILED"
exec >>"$RUN/train.log" 2>&1
cd "$ROOT"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

status() { printf '{"step":"%s","time":"%s"}\n' "$1" "$(date -Is)" > "$RUN/status.json"; echo "== $1 $(date -Is)"; }
fail() { status "FAILED: $1"; touch "$RUN/FAILED"; exit 1; }

[ -e run/pilot/DONE ] || fail "run_pilot.sh has not finished"
[ -s data/replay/prompts.jsonl ] || { status "replay_prompts"; $PY train/replay.py prompts data/replay 10 1 || fail "replay prompts"; }
[ -s data/replay/replay.jsonl ] || { status "replay_answers"; $PY train/replay.py answers data/replay export/gemma-base/gemma-base-Q4_K_M.gguf || fail "replay answers"; }
status "sft"
$PY train/sft.py train/configs/pilot.json || fail "sft"
status "export"
bash train/export_gguf.sh run/sft_pilot/best tutor-pilot || fail "export"
E=run/eval_pilot
status "eval"
[ -s $E/quiz.json ] || $PY train/eval_pilot.py quiz data/pilot/sections.jsonl $E || fail "eval quiz"
$PY train/eval_pilot.py answer $E base export/gemma-base/gemma-base-Q4_K_M.gguf || fail "eval answer base"
$PY train/eval_pilot.py answer $E tutor-pilot export/tutor-pilot/tutor-pilot-Q4_K_M.gguf || fail "eval answer tuned"
$PY train/eval_pilot.py judge $E || fail "eval judge"
$PY train/eval_pilot.py report $E data/pilot/sections.jsonl || fail "eval report"
status "DONE"
touch "$RUN/DONE"
