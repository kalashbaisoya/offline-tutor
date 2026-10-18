# Phase 0 + Phase 1 Report — Offline Expert AI Tutor

Date: 2026-10-16 · Status: first deliverable · Author: Claude (with repository + local hardware access)

Conventions used throughout:
- **[measured]**: produced on this machine in this session; raw output is in `bench/hw/results/`.
- **[cited]**: from a source linked in §C.
- **[estimate]**: my judgement, not yet tested.

---

## A. Verdict (short)

**Technically feasible, but the stated hypothesis is the wrong thing to bet on.**

- An offline, source-grounded study tutor for **one subject** runs on the target hardware: a 4 GB RTX 3050 Ti laptop with 16 GB RAM. Measured on this machine: Qwen3.5-4B (Q4_K_M, 8k context) runs fully on the GPU in 3.25 GB of VRAM at ~40 tok/s, with ~2.8 s time-to-first-token on a 4k-token retrieval prompt. Speech recognition (Parakeet) and synthesis (Kokoro fp32 or Piper) run on the CPU fast enough. The **estimated** voice turn latency is ~2–2.5 s typical, which is acceptable for tutoring but not cloud-grade. See §F.
- The claim "a fine-tuned *specialized SLM* is better than a general model" is **unlikely to be the main source of value**, for three reasons:
  - Published evidence consistently finds retrieval beats fine-tuning for injecting factual knowledge [cited: Ovadia et al. 2024; Soudani et al. 2024].
  - The learning-outcome evidence says *pedagogical design* determines whether an AI tutor helps or harms [cited: Bastani et al. 2025; Kestin et al. 2025; Liu et al. 2026].
  - Fine-tuning may still help *behaviour*: hint-first discipline, abstention, citation format, and teaching moves. That is testable, cheap, and secondary.
- **What deserves to exist:** a *tutor system*. It combines a modest general local model, curated retrieval over the learner's material, deterministic tools for exact maths, and an explicit teaching controller, all running privately offline. The "expert" lives in the package (knowledge + exercises + policy + evaluation), not mainly in the weights.
- **The marketplace is premature.** It depends on a validated runtime and package format, and it has unresolved copyright problems around redistributing textbook indexes.

**Recommendation (detailed in §J): NARROW the concept, then BUILD.** Run the §E experiment (about 2–3 weeks of work) before writing any app code beyond the benchmark harness.

---

## B. Strongest reasons this might fail

Ranked by how likely each is to kill the project, not by how interesting it is.

1. **The tutor may teach wrong maths confidently.** Small models still make multi-step reasoning and arithmetic errors. A tutor is held to a *higher* bar than a chatbot, because the learner cannot tell a wrong derivation from a right one. If the best local configuration cannot reach a low severe-error rate on core content, it should not be marketed as a tutor. Mitigation (to be tested, not assumed): route every numerical claim through a deterministic tool (SymPy/SciPy), and check retrieved worked solutions.
2. **AI tutors frequently *reduce* learning.**
   - A 2026 RCT at a large US public university (2,379 students, 30 instructors) found that access to a course-integrated GenAI tutor *lowered* final grades by 0.37 SD and LMS participation by 0.90 SD. First-generation students were hurt more [cited: Liu et al. 2026].
   - Unrestricted GPT-4 access raised practice scores but lowered later unassisted exam scores in Bastani et al. (PNAS 2025). A guarded "GPT Tutor" variant largely avoided the harm.
   - Conversely, a tightly scaffolded physics tutor beat active-learning classes (effect ≈ 0.63 SD) [cited: Kestin et al. 2025].

   **Implication:** answer-giving convenience is the default failure mode. The teaching controller is the product, not a feature.
3. **Free cloud tutors are strong and getting stronger.** ChatGPT Study Mode, Gemini Guided Learning, Claude learning mode, and NotebookLM are free or cheap and far more capable than anything that fits in 4 GB. An offline product wins only where *offline/private/no-quota/no-cost-at-scale* matters more than peak quality. Plausible segments: low-connectivity regions, institutions with privacy constraints, exam prep with long daily sessions, and users who object to cloud recording of voice or screen. This is a market assumption the code cannot validate.
4. **The 4 GB VRAM budget fits one model, not a stack.** The LLM, KV cache, embeddings, ASR, TTS, and any vision model compete for it. Laptops on battery also throttle the GPU (this one is capped at 52 W). **Measured (§F):** the 4B model at 8k context leaves ~0.8 GB of headroom. Raising context to 32k, or loading a 9B model, does not fail; the Windows driver silently pages to system RAM, making the system **7–12× slower**. The workable design is: LLM on the GPU at ≤ 8–16k context, speech on the CPU, and a runtime that checks VRAM itself. CPU-only machines cannot run a grounded 4B tutor at acceptable latency (~30 s to first token).
5. **Model churn makes per-subject fine-tunes perishable.** A LoRA adapter is tied to one base checkpoint. Base models have turned over every 6–12 months (Qwen3 in 2025, Qwen3.5 in March 2026, Gemma 4 in April 2026). A marketplace of fine-tuned weights becomes a graveyard of obsolete adapters. A package of knowledge + exercises + policy + eval survives a base-model upgrade.
6. **Copyright and licensing.** "Upload a textbook and publish a tutor" means redistributing chunked copyrighted text in an index. Learners importing their *own* material locally is fine. Publishing it in a marketplace generally is not, unless the source is openly licensed or the creator owns the rights. This constrains marketplace design from day one.
7. **Voice barge-in on laptop speakers is hard.** Without acoustic echo cancellation, the tutor hears itself and "interrupts" itself. Practitioner reports confirm that echo, not ASR speed, is the main barge-in obstacle [cited]. Headphones or the OS's echo cancellation are needed; true full-duplex on open speakers is an unsolved engineering risk.
8. **Screen-aware step verification is unreliable for maths.** OCR of equations is error-prone, and "the correct text appeared on screen" is weak evidence of understanding. This is the highest-effort, lowest-certainty subsystem and should come last.
9. **Evaluation cost is real.** Credible claims need expert-written items, blinded human grading of explanations, and learning studies with real participants. Without a domain expert and a participant pool, the project can validate *technical* quality but not *learning* claims.

---

## C. Evidence: existing products and research

### C.1 Research most relevant to the architecture decision

| Question | Evidence | What it implies here |
|---|---|---|
| Fine-tuning vs retrieval for knowledge | Ovadia et al., EMNLP 2024: RAG beat unsupervised fine-tuning for both existing and new knowledge; LLMs struggle to learn new facts via unsupervised FT. Soudani et al. 2024: RAG beat FT by a large margin, especially on less-popular facts; FT+RAG helped smaller models. A 2026 multi-hop study (7B models): RAG gave consistent gains, while *supervised* FT gave the highest accuracy on that task. | Retrieval is the default for "know the textbook". Supervised FT on *task format* (e.g. the multi-step solution style) may still add value. This is a measurable question, not a premise. |
| Does AI tutoring improve learning? | Kestin et al. 2025 (Sci. Reports, Harvard physics RCT): scaffolded AI tutor > active learning, ≈0.63 SD (0.73–1.3 by quantile regression), 2-week unit. Bastani et al. 2025 (PNAS): unguarded GPT-4 harmed later exam performance; guarded tutor mitigated it. Liu et al. 2026 (EdWorkingPaper 26-1598, 2,379 students): tutor access −0.37 SD grades. | Effects range from strongly positive to negative. **Design (guardrails, scaffolding, integration) is the dominant variable, not model IQ.** The controller deserves as much engineering as the model. |
| Small-model capability | Qwen3.5 small series (0.8B/2B/4B/9B, Apache-2.0, multimodal, March 2026). Gemma 4 E2B/E4B (April 2026, Apache-2.0 per secondary sources, text+image+audio input; ~4.5B effective/8B total params for E4B; reported GPQA-Diamond 58.6%, MMLU-Pro 69.4%). Phi-4-mini 3.8B (MIT). | Off-the-shelf 4B-class models are already strong enough that a narrow fine-tune must beat a *moving* baseline. All candidates are permissively licensed, which matters for packaging. |
| Offline voice | Moonshine v2 (streaming encoder, low TTFT on edge). Parakeet TDT (higher accuracy). Kokoro (82M TTS). Practitioner builds on CPU-only 16 GB hardware report 1–4 s time-to-first-audio with sentence-level TTS streaming; barge-in needs echo cancellation. | A fully local voice loop is feasible on CPU; the latency target must be measured, not assumed (§F). |

### C.2 Products (competitive landscape, Oct 2026)

| Product | Offline | Grounded in user's docs | Teaching behaviour | Voice | Screen | Gap vs this concept |
|---|---|---|---|---|---|---|
| ChatGPT Study Mode / Gemini Guided Learning / Claude learning mode | No | Partially (uploads) | Yes: Socratic, hint-first, quizzes | Yes (cloud) | Partial | Strongest pedagogy and models, but cloud-only, quota-limited, voice/screen sent to the cloud |
| Khanmigo | No | Khan content only | Strong, curriculum-aligned | Limited | No | Closed curriculum, cloud |
| NotebookLM | No | Yes, citation-grounded | Weak (summaries, audio overviews, quizzes) | Audio overviews | No | Excellent grounding UX to learn from; not a tutor |
| AnythingLLM, GPT4All, Open Notebook, Khoj, LM Studio, Jan | **Yes** | Yes (local RAG) | **None**: document chat only | Mostly no / bolt-on TTS | No | No pedagogy, no learner model, no mastery tracking, no evaluation |
| Anki and other SRS apps | Yes | Manual decks | Spaced repetition only | No | No | Proven retention mechanism, no explanation/tutoring |

**The empty quadrant is real:** *offline + grounded + pedagogically structured + learner model + evaluated*. Nobody occupies it, but it is empty partly because the market for it is unproven (§B.3).

---

## D. Where the value actually lives

| Layer | What it contributes | Evidence strength | Cost | Portability |
|---|---|---|---|---|
| **Specialized (fine-tuned) model** | Possibly: domain style, abstention habits, hint-first behaviour, shorter prompts (lower latency). Unlikely: new factual knowledge. | Weak to moderate; must be shown on *our* test set | High: data curation, GPU time, re-done every base-model upgrade | Low: tied to one base checkpoint |
| **Retrieval over source material** | Grounding, page-level citations, answers specific to the learner's textbook, honest abstention when material is silent | Strong in the literature | Low | High: index + chunk store survives model swaps (only the embedding model must match) |
| **Deterministic tools** (SymPy/SciPy, unit checks) | Exact numbers, verified algebra, eliminates the most embarrassing error class | Strong for numeric tasks [estimate; will measure] | Low | High |
| **Teaching controller + learner model** | Hint-first, retrieval practice, spacing, mastery estimation, prevents answer-dumping | The decisive factor in the RCTs above | Medium: this is the core IP | High: model-agnostic |
| **Complete offline system** | Privacy, no quotas, no per-minute cost, works without internet, unlimited sessions within local limits | Value is a market question | High engineering cost (voice, packaging, desktop) | — |

**Reframed hypothesis to test:**
> *Under 4 GB VRAM offline constraints, does a tutor system (small general model + retrieval + tools + teaching controller) reach a predeclared accuracy, grounding, and latency bar on one subject? And does subject-specific fine-tuning add a statistically and practically significant improvement on top of that system?*

---

## E. The smallest experiment that could change the architecture

**Subject:** Probability & Statistics (introductory university level). Reasons: clear correct answers, numeric verifiability, a well-studied misconception literature, and good **openly licensed** sources. Candidates: OpenStax *Introductory Statistics* (CC BY 4.0) and Grinstead & Snell *Introduction to Probability* (GNU FDL). Openly licensed sources keep the experiment and any demo package redistributable. Reliability Engineering lacks open textbooks of comparable quality.

**Configurations (one base family for controlled comparisons):**

| ID | System | Purpose |
|---|---|---|
| A | Qwen3.5-4B Q4_K_M, no retrieval | Baseline small general model |
| B | A + hybrid retrieval (BM25 + small embedding model) over the source textbooks, with page-cited chunks | Value of retrieval |
| B+T | B + calculator/SymPy tool | Value of deterministic tools |
| C | Qwen3.5-4B + LoRA (supervised FT on *training split* of worked solutions), no retrieval | Value of fine-tuning alone |
| D | C + same retrieval as B (+T if B+T wins) | FT on top of the best non-FT system |
| E | Gemma-4-E4B Q4_K_M (fits T0, measured) with the **identical** retriever + tools; Qwen3.5-9B run as an offline-quality reference only (6.7 tok/s on T0, i.e. a T1-hardware model) | "Different/stronger general local model, same knowledge" |
| F | BM25 search returning the top passage + SymPy for numeric items | Non-generative baseline |
| G | Cloud reference model (optional), same retrieval context | Ceiling reference, reported separately |

Controls: identical retriever, chunking, top-k, and prompt template across B/D/E/G; same decoding settings (greedy or fixed seed); context length fixed at 8k. C/D see **only** the training split. Exact model hashes are recorded (`models/SHA256SUMS`).

**The experiment proceeds in two stages** so fine-tuning compute is spent only if it can matter:
1. **Stage 1 (no training):** A, B, B+T, E, F (and G if available). This alone decides retrieval, tools, and model size.
2. **Stage 2 (conditional):** C and D, run **only if** B+T misses a gate on a category where fine-tuning plausibly helps (format, abstention, multi-step solution style). Otherwise fine-tuning is dropped for now, and that conclusion is recorded.

Full protocol, dataset spec, metrics, and predeclared thresholds: §G.

---

## F. Initial hardware and software configuration

### F.1 Target hardware (Tier T0 = this machine) [measured]

| Component | Value |
|---|---|
| CPU | Intel i7-11800H, 8C/16T, AVX2 + AVX-512 (incl. VNNI) |
| GPU | NVIDIA **RTX 3050 Ti** Laptop (Ampere, sm_86), **4096 MiB**, 52 W power cap, driver 610.62; display driven by the iGPU (0 MiB VRAM used at idle) |
| RAM | 16 GB host; **7.6 GiB visible to WSL2** (default WSL limit) |
| OS | Windows 11 Home host; development in WSL2 Ubuntu 22.04 |
| Toolchain | CUDA 12.4, gcc 11.4, cmake 3.22, Python 3.12, Node 22, Docker 24 |
| Missing | ffmpeg, Ollama, PyTorch (not needed for Phase 0–3) |

> **Caveat:** WSL2 is fine for LLM benchmarks (CUDA passthrough works), but it is **not representative** for microphone/speaker latency or screen capture. Voice and screen work must be measured on native Windows.

### F.2 Measured local inference [measured]

**Setup:**
- llama.cpp commit `b76b133` (2026-10-15), CUDA 12.4 build for sm_86, flash attention on, 8 CPU threads.
- All models are Q4_K_M GGUF from `unsloth/*`. Hashes are in `models/SHA256SUMS`.
- Raw data: `bench/hw/results/llm_20261016T010111.jsonl`, `llm_cpu_true.jsonl`, `server_probe.jsonl`.

**Throughput (llama-bench, mean of 3 reps; tok/s):**

| Model (file size) | Placement | Prefill 512 | Prefill 3072 | Decode (tg128) |
|---|---|---|---|---|
| Qwen3.5-2B (1.28 GB) | full GPU | 3,752 | 3,774 | **87.1** |
| Qwen3.5-4B (2.74 GB) | full GPU | 1,708 | 1,683 | **43.3** |
| Gemma-4-E4B (4.98 GB) | full GPU | 1,506 | 1,552 | **40.8** |
| Qwen3.5-9B (5.68 GB) | "full GPU" (spills to shared memory) | 91 | 95 | 3.5 |
| Qwen3.5-9B | 12 / 20 / 28 layers on GPU | 376 / 486 / 93 | 400 / 486 / 93 | 4.2 / 6.7 / 6.1 |
| Qwen3.5-2B | **true CPU only** (GPU hidden) | 235 | — | 13.4 |
| Qwen3.5-4B | **true CPU only** (GPU hidden) | 101 | — | 5.9 |

**Real server behaviour (llama-server):**
- Prompt: 4,009-token RAG-sized prompt (4,443 for Gemma's tokenizer), streaming 96 tokens.
- TTFT is client-side. Each value is the median of the 2 warm runs.

| Model | ctx | VRAM used | TTFT (4k prompt) | Decode | Load time |
|---|---|---|---|---|---|
| Qwen3.5-2B | 8k | 1.55 GB | 1.2–1.3 s | 75–77 tok/s | 20 s |
| **Qwen3.5-4B** | **8k** | **3.25 GB** | **2.8 s** | **40–41 tok/s** | 6 s (warm cache) |
| Gemma-4-E4B | 8k | 3.35 GB | 3.2 s | 25–34 tok/s (drifting) | 58 s (cold) |
| Qwen3.5-4B | **32k** | 3.92 GB | **21–22 s** | **24 tok/s** | 38 s |
| Qwen3.5-4B + concurrent Kokoro TTS on CPU | 8k | 3.25 GB | 2.7–4.3 s | 30–36 tok/s | — |

**What these numbers change:**
1. **A 4B model fits; the configuration is fragile.**
   - At 8k context, Qwen3.5-4B leaves ~0.8 GB of VRAM headroom, so ASR/TTS/embeddings must stay on the CPU.
   - At 32k context, it still "fits" (3.9 GB), but everything slows 7–8× because the Windows driver silently pages GPU memory to system RAM.
   - **Long sessions must use summarisation plus a bounded window (≤ 8–16k), not a large context.** Treat this as a hard design constraint.
2. **Oversubscription does not fail; it crawls.** Qwen3.5-9B at ngl=99 did not OOM. It ran at 3.5 tok/s. The runtime must measure free VRAM and choose `ngl`/ctx itself, and must never trust "it loaded" as success.
3. **Gemma-4-E4B is a viable alternative at the same VRAM.** Despite a 4.98 GB file, it uses ~3.35 GB of VRAM; per-layer embeddings appear to stay in host RAM (this is an inference from the numbers, not verified in code). It is a good candidate for config E. Qwen3.5-9B is **not** viable on T0 (≤ 6.7 tok/s at best).
4. **CPU-only fallback cannot run a grounded tutor.** With 4B on CPU, a 3k-token prompt takes ~30 s before the first token. A CPU-only tier would need 2B, short prompts, and text mode (~13 s for 3k tokens; still poor).
5. **Gate G1 (§G.3): pass for Qwen3.5-4B at 8k.**
   - Decode is 40–43 tok/s (target ≥ 15).
   - Prefill is ~1,600–1,700 tok/s, so a 3k prompt takes ≈ 1.8–1.9 s (target ≤ 2.0). This is a **marginal** pass that can fail under concurrent load.
   - VRAM is 3.25 GB (target ≤ 3.8).
   - Honest caveat: G1's thresholds were written after I had seen the first llama-bench numbers, so G1 is not a blind gate. Gates G2–G7 are fixed before any accuracy data exists.

### F.3 Measured CPU speech stack [measured]

**Setup:**
- sherpa-onnx 1.13.8 (pip, CPU build), 4 threads.
- Sentences are tutor-style, 2.4–5.9 s of audio.
- Synthetic TTS audio is fed back into ASR. This tests latency, **not** accuracy on real microphones.
- Raw data: `bench/hw/results/speech_*.json`.

| Component | Model | Latency | RTF (lower = faster) | Note |
|---|---|---|---|---|
| ASR (after end of speech) | Parakeet-TDT 0.6B v2 int8 | 0.14–0.30 s per utterance (≤ 0.42 s under LLM load) | 0.05 | Excellent. Round-trip WER 5 %. Errors: "P of A" → "a"; "0.12" ↔ "zero point one two" normalisation |
| TTS | Kokoro 82M **int8** | 2.9 s first sentence | **1.07–1.08** (4 or 8 threads) | **Slower than real time: unusable.** More threads do not help. |
| TTS | Kokoro 82M **fp32** | 1.1–1.3 s first sentence | 0.41–0.42 | Viable; natural voice |
| TTS | Piper (VITS, lessac-medium) | 0.13 s first sentence | 0.04 | Very fast; less natural |
| VAD | Silero (632 KB) | not yet measured | — | Negligible cost expected |

**Estimated voice turn latency (end of user speech → first tutor audio).** This is composed from the measured parts, **not yet measured end-to-end**:

| Step | Typical | Worst observed/assumed |
|---|---|---|
| VAD end-of-speech hangover (config) | 0.3 s | 0.5 s |
| ASR | 0.15 s | 0.4 s |
| LLM TTFT, with prompt-cache reuse of system prompt + history (~1.5k new tokens) vs no cache (4k tokens) | ~1.0 s | 4.3 s |
| First clause (~12 tokens @ 35–40 tok/s) | 0.3 s | 0.4 s |
| TTS of first clause: Kokoro fp32 / Piper | 0.6 s / 0.1 s | 1.3 s / 0.15 s |
| **Total: Kokoro / Piper** | **≈ 2.4 s / ≈ 1.9 s** | **≈ 6.9 s / ≈ 5.8 s** |

**Implication.** On T0, a voice turn will feel like "a thoughtful pause" (~2 s), not a cloud-grade (~0.5–1 s) conversation. Required mitigations:
- Make prompt caching mandatory.
- Retrieve only when needed.
- Keep the first clause short.
- Play an immediate acknowledgement sound or brief filler on end-of-speech.
- Make Piper the "fast voice" option.

Barge-in itself is cheap: ASR is fast, and cancelling a llama-server request and a TTS queue is immediate. **Echo cancellation is the real risk and is untested.**

### F.4 Proposed software stack (provisional; alternatives recorded)

| Concern | Choice | Why | Alternatives considered |
|---|---|---|---|
| LLM runtime | **llama.cpp `llama-server`** as a managed child process (OpenAI-compatible HTTP) | Best GGUF/quantization coverage, CUDA + Vulkan + CPU builds, Windows/macOS/Linux, supports new models first (Qwen3.5 GGUF works in llama.cpp but [cited] not in Ollama due to separate mmproj files), partial offload, multimodal via mmproj | Ollama (convenient, lags on new architectures), ONNX Runtime GenAI, MLC-LLM, vLLM (not for 4 GB consumer laptops) |
| Default model | **Qwen3.5-4B Q4_K_M** (2.74 GB, Apache-2.0, text+image) | Measured 3.25 GB VRAM @ 8k ctx, 40 tok/s; one model covers text and screenshots; permissive licence | Gemma-4-E4B (measured same VRAM/speed class → config E), Qwen3.5-2B (fallback tier, 1.55 GB, 77 tok/s), Qwen3.5-9B (not viable on T0), Phi-4-mini (text-only) |
| Retrieval | SQLite (+FTS5 for BM25) + small embedding model on CPU; vectors in SQLite or a NumPy file | Single-file, offline, transactional, trivially packaged | FAISS, LanceDB, Chroma (extra deps without need at textbook scale) |
| PDF ingest | **pypdfium2** (Apache/BSD) for text + page numbers | Permissive licence, keeps page mapping for citations | PyMuPDF (AGPL: a problem for a distributed app), Marker/Nougat for math OCR (later, GPU-heavy) |
| Exact maths | SymPy / SciPy in a restricted tool interface | Deterministic, verifiable numbers | LLM arithmetic (rejected) |
| Speech | **sherpa-onnx** (Apache-2.0): Silero VAD + Parakeet-TDT 0.6B int8 ASR + **Kokoro fp32** TTS (natural) or Piper (fast), all CPU; *not* Kokoro int8 (measured slower than real time) | One cross-platform runtime for VAD/ASR/TTS, no PyTorch, streaming APIs | whisper.cpp (good, but not streaming-native), faster-whisper (CTranslate2, competes for GPU), Piper TTS |
| Core language | **Python 3.12** core engine shared by the benchmark harness and the app | Experiment code becomes product code; best ML/eval ecosystem | Rust core (later, if profiling demands) |
| Desktop shell | **Decide at Phase 4** (provisional: Tauri + web UI talking to a local Python engine) | Small installer, native webview, good Windows support. The engine stays UI-agnostic, so the choice is reversible. | PySide6/Qt (single language, heavier), Electron (large footprint) |

---

## G. Reproducible experiment plan with predeclared decision criteria

### G.1 Dataset specification (target ≈ 420 items; stratified)

| Category | Dev | Test | Source of items | Auto-scorable? |
|---|---|---|---|---|
| Factual recall / definitions | 15 | 40 | Expert-written from textbook sections | Rubric (judge) |
| Multi-step derivations | 10 | 40 | Held-out textbook exercises with published solutions + expert-written | Final answer auto + derivation rubric |
| Numerical problems (units/tolerance) | 15 | 60 | **Parametric templates** with programmatic answers (fresh numbers per test run, so they can't be memorised) | Yes (relative tol 1e-3 or stated rounding) |
| Cross-section synthesis | 5 | 25 | Expert-written, requires ≥2 chapters | Rubric + citation check |
| Ambiguous / false-premise | 5 | 25 | Expert-written | Rubric (must challenge premise) |
| Unanswerable from material | 5 | 25 | Out-of-scope but plausible questions | Abstention label |
| Misconception diagnosis | 10 | 30 | Student answers containing documented misconceptions (gambler's fallacy, P(A\|B) vs P(B\|A), base-rate neglect, independence vs mutual exclusivity, …) | Rubric: identifies the specific error |
| Level-adapted explanation | 5 | 20 | Same concept at 3 learner levels | Human/judge rubric |
| Multi-turn follow-ups | 5 | 20 dialogues | Scripted conversations with dependent turns | Rubric per turn |
| Adversarial / OOD | 5 | 20 | Prompt-injection inside documents, off-topic, wrong-subject | Rule + rubric |
| **Total** | **80** | **≈ 325** | | |

- **Leakage controls:**
  - The training split (only for Stage 2) is drawn from *different* exercises/chapters than the test split.
  - Test items are hashed and stored under `data/test/` with a canary string.
  - Numeric items are regenerated from templates with a fixed seed per release, and a fresh seed for the final run.
  - Any test passage that appears verbatim in fine-tuning data is flagged by n-gram overlap.
- **Provenance per item:** id, category, difficulty (1–3), source + page, answer key, acceptable reasoning, rubric, author (expert / template / LLM-drafted-then-expert-verified).
- **Rule:** no test item may be generated by a model under evaluation. LLM-drafted items need expert verification and are labelled as such.

### G.2 Metrics

- **Correctness:** exact/numeric accuracy; derivation rubric (0–2); **severe-error rate** (a confidently stated wrong core claim).
- **Grounding:**
  - Citation precision (cited chunk supports the claim; judged).
  - Citation recall vs gold passages.
  - Abstention: correct on unanswerable items; false-abstention rate on answerable ones.
- **Teaching (subset, human-rated, blinded):** misconception identified; hint quality before reveal; follow-up appropriateness.
- **Performance:** TTFT, tokens/s, peak VRAM/RAM, end-to-end latency at a fixed prompt size.

**Scoring:**
- Numeric items are auto-scored.
- Rubric items use an LLM judge **calibrated against two human raters on a 60-item subset**. The judge is accepted only if Cohen's κ ≥ 0.6 vs the human consensus; otherwise rubric items are human-scored.
- All headline numbers are reported with **95 % bootstrap CIs**.
- Paired comparisons use McNemar (binary) or paired permutation tests (rubric).

**Power check:** with ~300 paired binary test items and ~25 % discordant pairs, a **~8 pp** difference is detectable at 80 % power (α = 0.05). Smaller differences are reported as "not distinguishable", not as wins.

### G.3 Predeclared decision gates (fixed before any test-set run)

| Gate | Criterion | If passed | If failed |
|---|---|---|---|
| **G1 Hardware** | Default model fully on GPU. Generation ≥ 15 tok/s. Prefill of a 3k-token RAG prompt ≤ 2.0 s. Peak VRAM ≤ 3.8 GB at 8k ctx. | 4B default on T0 | Drop to 2B default, or reduce ctx; 4B becomes T1 |
| **G2 Retrieval** | B − A ≥ 10 pp on material-dependent categories (CI excludes 0) **and** citation precision ≥ 0.80 | Retrieval is core | Investigate chunking/retriever before anything else |
| **G3 Tools** | B+T cuts numeric error rate by ≥ 50 % relative vs B | Tool use mandatory for numeric work | Tools optional |
| **G4 Model size** | E − (B+T) ≥ 10 pp overall, **and** E meets latency ≤ 2× G1 limits on T0 | Bigger model as default for capable hardware; T0 tier labelled "limited" | 4B remains default |
| **G5 Fine-tuning** (Stage 2 only) | D − best(B+T) ≥ 5 pp on targeted categories (CI excludes 0), no category regresses > 3 pp, severe-error rate not worse | FT becomes an *optional* package component | **FT not pursued**; record the result |
| **G6 Abstention** | Correct abstention ≥ 80 %, false abstention ≤ 10 % | — | Add abstention-specific controller logic, then re-test on dev only |
| **G7 Product floor** | Best T0 config ≥ 85 % on core (recall + numeric + derivation) **and** severe-error rate ≤ 5 % | Proceed to Phase 4 tutor MVP | **Narrow to a study assistant** (search + verified tools + flashcards) or require T1 hardware; do not ship as "tutor" |

Development iterations (prompting, chunking) use **dev only**. The test split runs **once per frozen configuration**, logged with git commit + model hashes.

### G.4 Learning pilot (Phase 7, outlined now so it can be planned)

- **Arms:** (1) self-study with the textbook, (2) same model, plain chat prompt, (3) full tutor system (controller + retrieval + tools).
- **Unit:** one 60–90 min topic (e.g. conditional probability and Bayes).
- **Measures:** randomised assignment, a pre-test, a held-out immediate post-test, a 7-day delayed post-test, and transfer items. Time on task and answer-reveal counts are logged.
- **Analysis:** ANCOVA on the post-test, controlling for the pre-test.
- **Sample size:** detecting d = 0.4 between two arms needs ~100 per arm, so anything smaller is labelled a **pilot**. A pilot of 15–25 per arm is for estimating variance and finding usability failures only.

### G.5 Reproducibility

- Everything is pinned: llama.cpp commit, model SHA-256, sampler settings, seeds, retriever config, prompt templates (versioned files), and dataset version.
- One command runs a configuration and writes JSONL results plus a summary table.
- Offline verification: the benchmark runs with networking disabled (`unshare -n` / Docker `--network none`) to prove there are no hidden cloud calls.

---

## H. Prioritised roadmap and explicit exclusions

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **2 (≈1 wk)** | Dataset v0: dev split + 60 % of test split; scoring code; judge calibration set | Items reviewed; scorer unit-tested; κ measured |
| **3 (≈2 wk)** | Benchmark harness: ingestion (pypdfium2 → chunks with page ids), hybrid retriever, llama-server driver, SymPy tool, configs A/B/B+T/E/F; **Stage 1 results + go/no-go**; Stage 2 LoRA only if gated in | Gates G1–G4, G6, G7 evaluated on test; report published |
| **4** | Text-first tutor MVP: document import, grounded Q&A with page citations, teaching controller (explain / hint-first / practice / quiz), learner model (per-concept mastery with uncertainty), SRS scheduling, local persistence | Controller vs plain-prompt comparison on teaching rubric; crash-free 2-hour session |
| **5a** | Voice: VAD → streaming ASR → controller → sentence-streamed TTS; barge-in with cancellation; push-to-talk default, hands-free with headphones/AEC | Measured end-of-speech → first-audio p50/p90 on native Windows |
| **5b** | Screen: user-triggered region capture → OCR / multimodal model → one-step guidance; explicit pause; no background capture | Task-completion verification accuracy on a scripted test suite |
| **6** | Package format v1 (manifest, hashes, provenance, eval results), installer, compatibility check, static catalogue | Package built on machine X runs identically on machine Y |
| **7** | Full benchmarks + learning pilot | Pilot report |
| **8** | Hardening: package signing, sandboxed tools, creator CLI | Security review |

**Explicitly out of scope until Phase 7 evidence exists:**
- Payments, creator monetisation, social features, recommendations.
- Hosted marketplace backend (a static catalogue is enough).
- Training from scratch.
- Multi-subject coverage.
- Mobile apps.
- Continuous/background screen capture.
- Full-duplex voice on open speakers without AEC.
- Arbitrary executable code in packages: packages are data + declarative config; tools come only from a fixed, runtime-provided allow-list.

---

## I. Unknowns that need your input

Provisional assumptions are in brackets; I will proceed on them unless you correct them.

1. **Who is the first user?** Possible answers: university students, exam-prep learners, self-learners, schools in low-connectivity areas. This decides the subject level, language, and whether offline is the main selling point. *[Assumed: English-speaking university students on mid-range laptops.]*
2. **Languages.** English only, or Hindi/other Indian languages too? This affects ASR/TTS choice substantially. *[Assumed: English only for Phases 2–5.]*
3. **Subject and source.** Probability & Statistics on openly licensed textbooks (OpenStax / Grinstead & Snell), or a specific course textbook you own? *[Assumed: open textbooks.]*
4. **A domain expert.** Do you have access to one (yourself, a TA, a lecturer) for ~10–15 hours to write and verify test items and blind-rate explanations? Without one, results are labelled "author-verified", which is weaker.
5. **Cloud use for evaluation only.** Is using a cloud LLM acceptable as a rubric judge (calibrated against humans) and as the optional reference baseline G? Is renting a cloud GPU (~US$10–50) acceptable for Stage 2 LoRA if it is triggered? The product stays offline either way. *[Assumed: yes to judge, ask again before any spend.]*
6. **Target OS.** Native Windows first? *[Assumed: yes, Windows 11 first, Linux second; macOS later.]*
7. **Open source or commercial?** This affects dependency licence choices (e.g. avoiding AGPL). *[Assumed: keep everything permissive so either remains possible.]*
8. **Learning-pilot participants.** Is there any realistic access to 30–100 learners for Phase 7? If not, the learning claims stay at the pilot level.

---

## J. Recommendation

**Narrow the concept, then build: proceed to Phase 2.**

- **Keep:** offline, private, grounded tutoring with voice and an explicit teaching controller on ordinary laptops.
- **Narrow:**
  - One subject.
  - Text first, then voice, then screen.
  - "Expert" means **an expert package**: curated knowledge, exercises, teaching policy, evaluation, and a reference to a general base model. It does not mean "expert weights".
- **Defer:** the marketplace, creator platform, and fine-tuning. Fine-tuning re-enters only through gate G5.
- **Abandon (for now):** the premise that a specialized SLM is the core differentiator. It may turn out to be a useful optional component; the experiment will say.

The project should stop, or pivot to a study assistant, if gate G7 fails on T0 hardware. That would mean no local configuration reaches 85 % core accuracy with ≤ 5 % severe errors, even with retrieval and tools.

---

## Phase 0–1 close-out (per §12 of the mandate)

1. **What was learned**
   - The 4 GB target can run a 4B grounded tutor. The binding constraints are VRAM headroom and context length, not decode speed.
   - Driver spillover makes "it fits" misleading.
   - Kokoro int8 is unusable on CPU, while fp32 Kokoro and Piper are fine.
   - CPU-only fallback is not viable for grounded answers.
   - In the literature, retrieval beats fine-tuning for knowledge, and pedagogical design (not model strength) determines learning gains or losses.
2. **What was implemented**
   - `bench/hw/llm_bench.sh`: llama-bench sweep over models, placement, and prompt sizes.
   - `bench/hw/server_probe.py`: real VRAM, TTFT, and decode via llama-server.
   - `bench/hw/speech_bench.py`: TTS/ASR latency and RTF with selectable TTS engine.
   - Pinned model hashes in `models/SHA256SUMS`, and this report.
3. **Tests actually executed**
   - The hardware probes above, all raw output in `bench/hw/results/`.
   - No accuracy or learning experiments have been run yet. **No claims about tutoring quality are made.**
4. **Known defects / open questions**
   - All runs were in WSL2; native Windows numbers may differ, especially audio I/O.
   - The voice-turn latency is a composed estimate, not measured end-to-end.
   - Echo cancellation and barge-in are untested.
   - VAD and the embedding model have not been benchmarked.
   - The 4B decode under thermal/battery conditions is unmeasured. Gemma decode drifted 34 → 25 tok/s within one minute, a possible thermal effect.
   - The llama.cpp build lives in a session scratch directory and must be rebuilt (instructions in `README.md`).
5. **Decisions supported by evidence**
   - Qwen3.5-4B Q4_K_M at ≤ 8k context as the T0 default; Gemma-4-E4B as the comparison arm E.
   - Speech on the CPU via sherpa-onnx with Kokoro fp32/Piper.
   - Bounded context with summarisation.
   - Runtime-managed VRAM checks.
   - No 9B on T0.
6. **Recommendation:** proceed to Phase 2 (dataset + scoring), with the narrowed hypothesis of §D.

## Sources

- Ovadia et al., *Fine-Tuning or Retrieval? Comparing Knowledge Injection in LLMs*, EMNLP 2024 — https://aclanthology.org/2024.emnlp-main.15/
- Soudani et al., *Fine Tuning vs. Retrieval Augmented Generation for Less Popular Knowledge* — https://www.alphaxiv.org/abs/2403.01432
- *Fine-Tuning vs. RAG for Multi-Hop Question Answering with Novel Knowledge* (2026) — https://arxiv.org/html/2601.07054v1
- Kestin et al., *AI tutoring outperforms in-class active learning*, Scientific Reports 2025 — https://www.nature.com/articles/s41598-025-97652-6
- Liu et al., *The Effects of Course-Integrated AI Tutoring on Student Performance and Engagement*, EdWorkingPaper 26-1598 (2026) — https://edworkingpapers.com/ai26-1598
- Bastani et al., *Generative AI without guardrails can harm learning*, PNAS 2025 (cited from memory; verify before external citation)
- Qwen3.5 small models — https://unsloth.ai/docs/models/qwen3.5
- Gemma 4 technical report — https://arxiv.org/html/2607.02770v1 ; E4B GGUF card — https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF ; licence (secondary) — https://en.wikipedia.org/wiki/Gemma_(language_model)
- Moonshine v2 — https://arxiv.org/abs/2602.12241
- Local voice practitioner reports — https://heyneo.com/blog/local-voice-research-assistant-cpu-16gb , https://github.com/Ianodad/voice-stack
- Product comparisons (secondary) — https://aicompetence.org/best-ai-tutors/ , https://glasp.co/articles/ai-study-modes-compared , https://dev.to/vigoss_luke_3604c1d0e9b4a/i-tested-5-open-source-notebooklm-alternatives-heres-what-actually-works-23em
