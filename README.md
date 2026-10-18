# offline-tutor

An offline Reliability Engineering tutor for ordinary laptops (4 GB GPU). The student clicks a floating circle,
asks by voice, and circles anything on screen. The local model answers in English or Hinglish. There are no uploads,
and screen and voice are captured only during an activation.

- **How the SLM was trained, the full methodology, and the findings:
  [docs/02_METHODOLOGY_AND_FINDINGS.md](docs/02_METHODOLOGY_AND_FINDINGS.md)**
- Plan and full decision log: [docs/01_PLAN_RELIABILITY_TUTOR.md](docs/01_PLAN_RELIABILITY_TUTOR.md)
- Phase 0–1 research report: [docs/00_PHASE0_1_REPORT.md](docs/00_PHASE0_1_REPORT.md)
- Raw evaluation outputs and training curves: [docs/results/](docs/results/)

## Key finding

> **Observation (project owner):** the model did not perform well on mathematics, it hallucinated, and small
> language models are not good as tutors.

The measurements back this up:
- Three LoRA fine-tuning pilots on Gemma-4-E4B fixed the *answer language* but **added no subject knowledge**, and
  they **hurt reasoning on unseen material**.
- Even the best configuration (base model + language hint + offline knowledge pack) answers **about a third of the
  textbook author's own numerical problems wrongly**.

At laptop scale, the SLM works as an offline study *assistant* that explains what is on screen in the student's
language. It is not a tutor that can be trusted with derivations or arithmetic. Details are in the methodology
document.

**No book text, trained weights or book-derived knowledge packs are included.** The books are copyrighted; rebuild
from your own licensed copies.

## The app (Windows)

Install (once, internet needed, no admin):

```powershell
powershell -ExecutionPolicy Bypass -File app\setup_windows.ps1 -ModelPath <gemma-base-Q4_K_M.gguf> -PackPath <pack.jsonl>
```

Then start **Offline Tutor** from the Desktop or Start menu. Everything runs offline after that.

The circle is the only control. Its colour shows the state:

| Circle | What it means | Click does |
|---|---|---|
| Blue **?** | Idle | Starts capture: the circle turns **red ➤**, the screen freezes, the mic turns on, and the pointer becomes a pen |
| Red **➤** | Capturing: speak your question and circle what you mean | **Sends** your voice + screen to the tutor |
| Amber spinner | Thinking | Interrupts and starts a new question |
| Green sound bars | Speaking the answer aloud | Interrupts and starts a new question |

- **Ctrl+Shift+Space** does the same as a click.
- **Esc** cancels a capture, and nothing is kept.
- Right-click or **Backspace** undoes a circle.
- Answers are **spoken** (Kokoro voice, sentence by sentence, while the model is still writing). The chat window stays closed.
- **Right-click the circle** for:
  - Show chat (full history with rendered formulas; you can also type there, without sharing the screen)
  - Stop speaking
  - Speak answers on/off (off = the chat window opens with each answer)
  - Also open the chat window
  - Voice (11 English voices)
  - Answer language
  - Logs, Quit

Pipeline:
- **Model:** Gemma-4-E4B Q4_K_M via llama-server (8k context, q8_0 KV, ~3.0 GB VRAM, ~33 tok/s, first token ~1.5 s).
- **Knowledge pack:** BM25 over 3,219 teacher-written notes, not book text.
- **Speech-to-text:** Whisper-small int8 on the CPU (~1.8 s for 6 s of speech).
- **OCR:** RapidOCR on the CPU (~0.3 s per screen).
- **Voice:** Kokoro en v0.19 on the CPU (~0.36× real time). Formulas are turned into words, e.g. "R equals 1 minus …".

Hindi questions are answered in Hinglish. Code is in `app/tutor_app/`, shared prompt and pack code in `tutor/`, and `app/smoke_test.py` is a headless component check.

## Server pipeline (knowledge pack)

`train/run_full_pack.sh` runs four steps:
1. Book pages → clean LaTeX (teacher vision).
2. Sections.
3. Teacher notes, with numericals verified in Python.
4. Pack, with verbatim-overlap filtering.

`train/run_full_eval.sh` evaluates on the book author's own problems.

## Layout

```
docs/            reports and decisions
bench/hw/        hardware probes (LLM throughput, VRAM/TTFT, speech latency)
bench/hw/results raw benchmark output (JSON/JSONL, nvidia-smi traces)
models/          downloaded model weights (not source; see models/SHA256SUMS)
.venv/           Python env for speech probes (sherpa-onnx)
```

## Reproducing the Phase 0 hardware measurements

Requirements: NVIDIA GPU + CUDA toolkit (tested 12.4), cmake, gcc, Python 3.12.

```bash
# 1. llama.cpp (tested at commit b76b133)
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
git checkout b76b133
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build -j --target llama-bench llama-server
cd ..

# 2. models (Q4_K_M GGUF from huggingface.co/unsloth/*), then verify:
#    Qwen3.5-2B, Qwen3.5-4B, Qwen3.5-9B, gemma-4-E4B-it
(cd models && sha256sum -c SHA256SUMS)

# 3. speech models into models/speech/ from
#    https://github.com/k2-fsa/sherpa-onnx/releases (asr-models, tts-models):
#    sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8, kokoro-en-v0_19, kokoro-int8-en-v0_19,
#    vits-piper-en_US-lessac-medium, silero_vad.onnx
python3 -m venv .venv && .venv/bin/pip install sherpa-onnx soundfile numpy

# 4. run
LLAMA_BIN=llama.cpp/build/bin bench/hw/llm_bench.sh models bench/hw/results
python3 bench/hw/server_probe.py llama.cpp/build/bin models/Qwen3.5-4B-Q4_K_M.gguf 8192 99
CUDA_VISIBLE_DEVICES= llama.cpp/build/bin/llama-bench -m models/Qwen3.5-4B-Q4_K_M.gguf -ngl 0 -p 512 -n 64   # true CPU fallback
.venv/bin/python bench/hw/speech_bench.py models/speech 4 kokoro      # or kokoro-int8 | piper
```

Note: with a CUDA build, `-ngl 0` still offloads large prompt batches to the GPU. Hide the GPU
(`CUDA_VISIBLE_DEVICES=`) to measure real CPU-only performance.
