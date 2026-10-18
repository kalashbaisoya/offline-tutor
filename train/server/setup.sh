#!/usr/bin/env bash
# One-time setup of the training server. Idempotent: re-running skips finished steps.
# Run detached:  tmux new -d -s setup 'bash ~/offline-tutor/train/server/setup.sh'
# Progress:      ~/offline-tutor/run/setup/status.json   (DONE / FAILED marker files alongside)
set -uo pipefail

ROOT=~/offline-tutor
RUN=$ROOT/run/setup
mkdir -p "$RUN" "$ROOT/models" "$ROOT/third_party"
rm -f "$RUN/DONE" "$RUN/FAILED"
LOG=$RUN/setup.log
exec >>"$LOG" 2>&1

status() { printf '{"step":"%s","time":"%s"}\n' "$1" "$(date -Is)" > "$RUN/status.json"; echo "== $1 $(date -Is)"; }
fail() { status "FAILED: $1"; touch "$RUN/FAILED"; exit 1; }

# 1. Python env (conda-provided interpreter; Ubuntu python lacks venv/pip)
status "env"
if [ ! -x "$ROOT/env/bin/python" ]; then
  ~/miniconda3/bin/conda create -y -p "$ROOT/env" python=3.12 || fail "conda create"
fi
PY=$ROOT/env/bin/python
$PY -m pip install -q --upgrade pip || fail "pip upgrade"

status "pip-torch"
$PY -c "import torch" 2>/dev/null || $PY -m pip install -q torch --index-url https://download.pytorch.org/whl/cu128 || fail "torch"

status "pip-train"
$PY -m pip install -q transformers peft trl datasets accelerate bitsandbytes sentencepiece \
  "huggingface_hub[cli]" hf_transfer pypdfium2 python-pptx sympy scipy cmake gguf || fail "train deps"
$PY -m pip freeze > "$ROOT/train/server/requirements.lock.txt"
$PY -c "import torch;assert torch.cuda.is_available();print(torch.__version__, torch.cuda.get_device_name(0))" || fail "torch cuda"

# 2. llama.cpp: teacher inference server + GGUF conversion/quantization
status "llama.cpp"
LC=$ROOT/third_party/llama.cpp
LC_SHA=b76b13379d3b7d67ab0bc424516bb6fd2522bd8e   # same commit as the laptop benchmarks
if [ "$(git -C "$LC" rev-parse HEAD 2>/dev/null)" != "$LC_SHA" ]; then
  rm -rf "$LC" && mkdir -p "$LC" && cd "$LC" && git init -q && git remote add origin https://github.com/ggml-org/llama.cpp \
    && timeout 900 git fetch -q --depth 1 origin "$LC_SHA" && git checkout -q FETCH_HEAD && cd - >/dev/null || fail "fetch llama.cpp"
fi
if [ ! -x "$LC/build/bin/llama-server" ]; then
  PATH=$ROOT/env/bin:$PATH cmake -S "$LC" -B "$LC/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89 \
    -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release || fail "cmake configure"
  PATH=$ROOT/env/bin:$PATH cmake --build "$LC/build" -j 16 --target llama-server llama-quantize llama-cli || fail "cmake build"
fi

# 3. Models
export HF_HUB_ENABLE_HF_TRANSFER=1
HF=$ROOT/env/bin/hf
status "download-base"
$HF download Qwen/Qwen3.5-4B --local-dir "$ROOT/models/Qwen3.5-4B" || fail "download base"
status "download-teacher"
$HF download unsloth/Qwen3.5-27B-GGUF --include "*Q4_K_M*" --local-dir "$ROOT/models/Qwen3.5-27B-GGUF" || fail "download teacher"
(cd "$ROOT/models" && find . -name '*.gguf' -o -name '*.safetensors' | sort | xargs sha256sum > SHA256SUMS) || fail "hash"

status "DONE"
touch "$RUN/DONE"
