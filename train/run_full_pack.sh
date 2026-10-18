#!/usr/bin/env bash
# Full knowledge pack from both books: clean pages -> sections -> teacher notes -> pack.
# Also cleans Agarwal's end-of-book problems + answers (PDF 347-376), used only for evaluation.
# Run detached:  tmux new -d -s full 'bash ~/offline-tutor/train/run_full_pack.sh'
# Progress:      ~/offline-tutor/run/full/status.json (+ DONE / FAILED); per-step status.json under data/full/
set -uo pipefail
ROOT=~/offline-tutor
RUN=$ROOT/run/full
D=$ROOT/data/full
PY=$ROOT/env/bin/python
BOOKS=$ROOT/data/raw/books_all
mkdir -p "$RUN" "$D/agarwal/clean/pages"
rm -f "$RUN/DONE" "$RUN/FAILED"
exec >>"$RUN/full.log" 2>&1
cd "$ROOT"
export PARALLEL=${PARALLEL:-5}   # q8_0 KV: 5 x 24k slots fit; use PARALLEL=1 when the GPU is shared

status() { printf '{"step":"%s","time":"%s"}\n' "$1" "$(date -Is)" > "$RUN/status.json"; echo "== $1 $(date -Is)"; }
fail() { status "FAILED: $1"; touch "$RUN/FAILED"; exit 1; }

cp -n data/pilot/clean/pages/*.md "$D/agarwal/clean/pages/"   # ch. 3 pages from the pilot
status "clean agarwal"
$PY train/clean_pages.py "$BOOKS/Reliability Engineering KK Agarwal.pdf" 13 346 "$D/agarwal/clean" || fail "clean agarwal"
status "clean modarres"
$PY train/clean_pages.py "$BOOKS/Mohammad Modarres.pdf" 24 527 "$D/modarres/clean" || fail "clean modarres"
status "clean agarwal problems"
$PY train/clean_pages.py "$BOOKS/Reliability Engineering KK Agarwal.pdf" 347 376 "$D/agarwal_problems/clean" || fail "clean problems"
status "sections"
$PY train/sections.py "$D/agarwal/clean/pages" auto agarwal "$D/sections_agarwal.jsonl" 0.1 12000 || fail "sections agarwal"
$PY train/sections.py "$D/modarres/clean/pages" auto modarres "$D/sections_modarres.jsonl" 0.1 12000 || fail "sections modarres"
cat "$D/sections_agarwal.jsonl" "$D/sections_modarres.jsonl" > "$D/sections.jsonl"
status "generate"
$PY train/gen.py generate "$D/sections.jsonl" "$D/gen" 1 pack || fail "generate"
status "build"
$PY train/gen.py build "$D/sections.jsonl" "$D/gen" || fail "build"
$PY train/build_pack.py "$D/gen/sft.jsonl" "$D/pack.jsonl" --book "$D/*/clean/pages/*.md" || fail "pack"
status "DONE"
touch "$RUN/DONE"
