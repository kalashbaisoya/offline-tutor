#!/usr/bin/env bash
# After run_full_pack.sh: evaluate on Agarwal's own problems (printed answers) - base / +hint / +hint+pack.
# Run detached (waits for the pack):  tmux new -d -s feval 'bash ~/offline-tutor/train/run_full_eval.sh'
set -uo pipefail
ROOT=~/offline-tutor
RUN=$ROOT/run/full_eval
PY=$ROOT/env/bin/python
E=run/eval_problems
B=export/gemma-base/gemma-base-Q4_K_M.gguf
mkdir -p "$RUN"
rm -f "$RUN/DONE" "$RUN/FAILED"
exec >>"$RUN/eval.log" 2>&1
cd "$ROOT"
export PARALLEL=${PARALLEL:-4}   # use PARALLEL=1 when the GPU is shared

status() { printf '{"step":"%s","time":"%s"}\n' "$1" "$(date -Is)" > "$RUN/status.json"; echo "== $1 $(date -Is)"; }
fail() { status "FAILED: $1"; touch "$RUN/FAILED"; exit 1; }

status "waiting for pack"
while [ ! -e run/full/DONE ]; do [ -e run/full/FAILED ] && fail "pack run failed"; sleep 60; done
status "extract"
[ -s $E/problems.json ] || $PY train/eval_problems.py extract data/full/agarwal_problems/clean/pages $E || fail "extract"
status "answer"
$PY train/eval_problems.py answer $E base $B || fail "base"
$PY train/eval_problems.py answer $E base-hint $B strict yes || fail "base-hint"
$PY train/eval_problems.py answer $E base-hint-pack $B strict yes data/full/pack.jsonl || fail "base-hint-pack"
$PY train/eval_problems.py report $E || fail "report"
status "DONE"
touch "$RUN/DONE"
