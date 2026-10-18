#!/usr/bin/env bash
# S7: merge a LoRA adapter into the base model -> GGUF (bf16) -> Q4_K_M for the 4 GB laptop -> SHA-256.
# Usage: bash train/export_gguf.sh ADAPTER_DIR NAME      e.g.  bash train/export_gguf.sh run/sft_pilot/best tutor-pilot
# Output: export/NAME/NAME-Q4_K_M.gguf (+ NAME-Q4_K_M.gguf.sha256). The vision/audio towers stay as in the base model.
set -euo pipefail
ROOT=~/offline-tutor
cd "$ROOT"
ADAPTER=$1
NAME=$2
OUT=export/$NAME
LC=third_party/llama.cpp
mkdir -p "$OUT"

CUDA_VISIBLE_DEVICES= env/bin/python - "$ADAPTER" "$OUT/merged" <<'EOF'
import sys, json, shutil, torch
from pathlib import Path
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
adapter, out = sys.argv[1], Path(sys.argv[2])
base = json.load(open(Path(adapter).parent / "config.json"))["base"]
model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16)
model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
model.save_pretrained(out, safe_serialization=True)
AutoTokenizer.from_pretrained(base).save_pretrained(out)
for f in ("chat_template.jinja", "processor_config.json", "generation_config.json"):  # keep the base model's files
    if (Path(base) / f).exists():
        shutil.copy(Path(base) / f, out / f)
print("merged ->", out)
EOF

env/bin/python $LC/convert_hf_to_gguf.py "$OUT/merged" --outtype bf16 --outfile "$OUT/$NAME-bf16.gguf"
# Same embedding types as the unsloth Q4_K_M that was benchmarked on the 4 GB laptop (3.35 GB VRAM at 8k ctx).
# Plain Q4_K_M would make token_embd (tied, also the output layer on the GPU) Q6_K: +0.17 GB VRAM, +0.36 GB file.
$LC/build/bin/llama-quantize --token-embedding-type q4_K --tensor-type per_layer_token_embd=q5_K \
  "$OUT/$NAME-bf16.gguf" "$OUT/$NAME-Q4_K_M.gguf" Q4_K_M
(cd "$OUT" && sha256sum "$NAME-Q4_K_M.gguf" > "$NAME-Q4_K_M.gguf.sha256" && cat "$NAME-Q4_K_M.gguf.sha256")
rm -rf "$OUT/merged" "$OUT/$NAME-bf16.gguf"
ls -la "$OUT"
