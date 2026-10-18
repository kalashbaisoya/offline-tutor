#!/usr/bin/env bash
# Pilot: KK Agarwal ch. 3 (Series-Parallel Systems, PDF pages 71-98) -> clean pages -> sections -> teacher data.
# Run detached:  tmux new -d -s pilot 'bash ~/offline-tutor/train/run_pilot.sh'
# Progress:      ~/offline-tutor/run/pilot/status.json  (+ DONE / FAILED; per-step status.json in data/pilot/*)
set -uo pipefail
ROOT=~/offline-tutor
RUN=$ROOT/run/pilot
D=$ROOT/data/pilot
PY=$ROOT/env/bin/python
BOOK="$ROOT/data/raw/books_all/Reliability Engineering KK Agarwal.pdf"
mkdir -p "$RUN" "$D"
rm -f "$RUN/DONE" "$RUN/FAILED"
exec >>"$RUN/pilot.log" 2>&1
cd "$ROOT"

status() { printf '{"step":"%s","time":"%s"}\n' "$1" "$(date -Is)" > "$RUN/status.json"; echo "== $1 $(date -Is)"; }
fail() { status "FAILED: $1"; touch "$RUN/FAILED"; exit 1; }

status "clean_pages"
$PY train/clean_pages.py "$BOOK" 71 98 "$D/clean" || fail "clean_pages"
status "sections"
$PY train/sections.py "$D/clean/pages" 3 agarwal "$D/sections.jsonl" 0.1 || fail "sections"
status "generate"
$PY train/gen.py generate "$D/sections.jsonl" "$D/gen" 3 || fail "generate"
status "build"
$PY train/gen.py build "$D/sections.jsonl" "$D/gen" || fail "build"
status "DONE"
touch "$RUN/DONE"
