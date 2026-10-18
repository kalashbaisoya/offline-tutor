# Plan — Distributable Offline Reliability Engineering Tutor Model (EN / Hindi / Hinglish)

Status: approved by the user on 2026-10-16. Supersedes the earlier retrieval-library draft of this file.

Server (S0, measured 2026-10-16): `hci-ws02`, Ubuntu 24.04, RTX 4090 24 GB, 125 GB RAM, 32 cores, 161 GB free disk, internet available. Shared machine (10 user sessions); all work lives in `~/offline-tutor`.

## Context
The user's intent, as clarified:
- Train a 4B-class model on **one or two Reliability Engineering books**, so that subject knowledge lives **in the weights**.
- Distribute that model to other users of the same offline multimodal app. They will **not** have the books.
- A user opens **any** PPT or PDF on the subject (unknown in advance) and asks about it: "explain this slide", "yeh derivation samjhao", "solve this numerical".
  - The screen supplies the context.
  - The model's trained knowledge supplies the expertise.
- Users are university students preparing for semester exams. Languages are English, Hindi, and Hinglish. Usage must be unlimited and offline.
- Training runs on the user's GPU server. The user judges quality from their own experience.

Phase 0 constraints (measured):
- Qwen3.5-4B and Gemma-4-E4B (Q4_K_M, 8k context) fit the laptop's 4 GB GPU at about 40 tok/s, with about 2.8 s to first token.
- 9B models and contexts above 16k silently slow down 7–12×.
- **So the specialist must be about 4B.**

What this means:
- **Fine-tuning is now the central bet**, not an option.
- Known risk: plain fine-tuning on book text injects facts poorly (Ovadia et al. 2024).
- The pipeline therefore uses methods that do work for knowledge injection:
  - heavy synthetic augmentation of the book: many rephrasings, and Q&A covering every concept and formula (cf. "synthetic continued pretraining");
  - training directly on the **target task**, which is explaining slide or OCR content;
  - mixing in general data so the model doesn't forget general skills.
- Success = the fine-tuned model beats the same base model, in a **blind A/B judged by the user**, on questions about **real slides that weren't used in training**.

## Course change (2026-10-18): knowledge pack instead of knowledge-in-weights

Three fine-tuning pilots (Agarwal ch. 3) were measured against inference-time alternatives on the same 73-item
quiz (`run/eval_pilot/eval_report.md`):

| Arm | Content, trained sections | Useful* | Right language | Content, held-out section |
|---|---|---|---|---|
| Base Gemma-4-E4B | 48 % | 25 % | 44 % | 52 % |
| Fine-tunes v1–v3 (best) | 39–46 % | 38–45 % | 96 % | 30–37 % |
| Base + per-turn language hint | 43 % | 31 % | 86 % | 63 % |
| **Base + hint + knowledge pack** | **81 %** | **78 %** | **92 %** | 63 % |

\*Useful = correct and in the student's language/script. Caveat: the same teacher wrote the notes and the quiz.
The author-written problems evaluation (`train/eval_problems.py`) removes that bias.

**What ships now:** the unchanged base Gemma-4-E4B Q4_K_M, plus:
- the per-turn language hint (`tutor/context.py`);
- an offline knowledge pack, i.e. BM25 over teacher-written, re-worded notes, not book text (`tutor/knowledge.py`);
- the calculator tool.

Fine-tuning stays possible later for tutoring *behaviour* (hint-first, honesty), not facts.

**Why:** across three pilots, fine-tuning never added content, even on trained material, and always cost reasoning on unseen material. This matches Ovadia et al. 2024. The pack also:
- updates without retraining;
- survives base-model upgrades;
- carries less copyright risk than weights trained on the books.

The original closed-book design follows for reference.

## Approach

### Model training pipeline (server; scripts in `train/`, each step resumable, run via tmux + `status.json`)
| Step | What | Output |
|---|---|---|
| S0 | Server inventory (GPU/VRAM, CUDA, disk, internet); book inventory (pages, scanned?, chapters) | `inventory.json` |
| S1 | Extract book text per chapter/page (pypdfium2; OCR for scanned pages); formulas kept as text/LaTeX | `corpus.jsonl` |
| S2 | **Split by chapter section:** ~10 % of sections are held out from training and used only for evaluation. A set of **real PPTs/PDFs from other sources** (lecture slides, not from the books) is also held out as the A/B test. | split manifests |
| S3 Knowledge augmentation | A teacher LLM on the server rewrites each section into diverse forms: explanations at 3 levels, concept Q&A, formula and derivation walkthroughs, worked numericals (**answers re-computed in Python; mismatches discarded**), comparisons, common misconceptions, and Hindi/Hinglish versions. | `knowledge_sft.jsonl` |
| S4 Target-task data | The teacher turns sections into **synthetic slides** (bullets, formula fragments, figure captions), adds **OCR-style noise** (broken symbols, merged lines), and writes "student question about this slide → tutor explanation" pairs in EN/HI/Hinglish. It also writes "off-topic/unknown → honest answer" examples. Inputs mirror the app: **marked region** (the focus) + **full-slide text** (context) + **spoken-style question** (ASR-like, often Hinglish, deictic: "yeh wala formula", "is graph ka matlab"). | `slide_sft.jsonl` |
| S5 Replay mix | ~20–30 % general instruction/chat data, so general reasoning and language skills don't degrade | `mix.jsonl` |
| S6 Train | Base = **Gemma-4-E4B**: it won the blind base pick 11–4 (`eval/out/base_pick_v1`, judged by Claude at the user's request on 2026-10-17; weak evidence, n=20). High-rank LoRA on all linear layers to start; full fine-tune if the server's VRAM allows and LoRA under-learns. Early stopping on held-out sections. | adapter / weights + curves |
| S7 Export | Merge → GGUF → Q4_K_M → SHA-256 + model card (books used, base model, languages, limits) | `reliability-tutor-4b-Q4_K_M.gguf` |
| S8 Checks | (a) Closed-book quiz on held-out sections: base vs fine-tuned, auto-scored where answers are numeric. (b) **Verbatim-regurgitation test:** does the model reproduce long book passages? Needed before giving it to others. (c) General-skill regression test on a small standard set. | `eval_report.md` |

**Pilot first:** S1–S8 on **one chapter**, a short server run, before the full books.

### Laptop app (offline, model-agnostic; the trained model is a drop-in GGUF)
- **Chat:**
  - llama-server running the specialist at ≤ 8k context, with a rolling conversation summary.
  - A Python **calculator tool**, so numericals (λ, MTTF, R(t), availability, Weibull) are computed exactly.
  - Hinglish in → Hinglish out.
- **Screen awareness, for any PPT or PDF** (interaction decided by the user on 2026-10-17):
  - **Nothing is captured until the hotkey is pressed.** The hotkey starts **voice and screen together**: the mic records the student's question, and the screen is captured. There is no background capture and no always-listening mic; there is a visible indicator.
  - While the hotkey turn is active, the **mouse becomes a special pointer** (highlighter/crosshair). The student can drag a box or circle a sub-section ("yeh wala part samjhao"). The marked region is cropped at full resolution as the primary context. A downscaled full screenshot is kept as secondary context, so the tutor knows which slide the region came from. If nothing is marked, the whole active window is used.
  - The turn ends on hotkey release or a second press (push-to-talk), or on VAD end-of-speech. The pointer then returns to normal.
  - **CPU OCR** (ONNX RapidOCR) extracts the text.
  - If the window is a local `.pptx`/`.pdf` and the path can be found from the window title, the slide or page text is read **directly from the file**. This needs no library or pre-indexing; it is simply more accurate than OCR.
  - For diagram- or equation-heavy slides, an **on-demand vision pass** runs (the base model's mmproj). Its VRAM cost (+0.7–1 GB) must be measured; it may require dropping context to 4k while active.
  - The tutor first states what it read ("Slide: Weibull distribution, β = 1.5 …"), then explains using its trained knowledge.
  - Explaining needs nothing but the model.
- **Blind A/B tool:** the same question/slide goes to two models, answers are shown in random order, and the user picks the better one or "both bad". Tallies are kept per question type.

### Order of work
1. User provides books + a few real lecture PPTs (for testing only) + server SSH access.
2. Quick laptop test: base Qwen3.5-4B vs Gemma-4-E4B on ~20 Hinglish reliability questions → pick the base model. A minimal chat + hotkey screen prototype also gets real use **with the base model** (this becomes the baseline).
3. Server pilot (one chapter) → full training.
4. Export → laptop → blind A/B on held-out real slides. **Ship the fine-tune only if it wins.**
5. Windows packaging (installer, model download + hash check, model card).
6. Hinglish voice (multilingual ASR + Hindi-capable TTS, measured first).

### Honest risks
- A closed-book 4B model will sometimes state wrong facts confidently. Without book retrieval there is nothing to cite, except the slide itself. Mitigations: training on honest-uncertainty examples, the calculator tool for numbers, and the user's A/B judgement.
- Gains over the base model may be modest. Reliability engineering basics are already in general models; the gains should appear in book-specific depth, exam-style format, and Hinglish.
- **Redistributing a model trained on copyrighted books** carries legal risk, and memorised passages make it worse. The S8b regurgitation test reduces the risk but does not remove it. Check book licences or obtain permission before sharing beyond personal use.

### Server access
- I generate `~/.ssh/offline_tutor_ed25519`. The user adds the public key to a **non-root** account. No password goes into the chat.
- The user also provides the IP address, username, GPU/VRAM, disk space, whether there is internet access, and whether long-running jobs are allowed.

### Saving tokens during long server jobs
- Jobs run detached in tmux and write `status.json` (step, %, loss, ETA, error). Closing the chat doesn't stop them.
- I wait with **one blocking background SSH command** that exits on DONE or FAILED. **No tokens are used while waiting** and there is no polling.
- I read only `status.json`, or `tail -n 20` of a log on error. Logs are never streamed into the chat.
- No `/loop` keep-alive pings.
- One-chapter pilot first; shard-level and step-level checkpoints so a crash resumes.
- Use `/model` to switch to Sonnet or Haiku for status checks, and `/compact` between phases.
- Long runs: close the session, then come back and say "check the training run" (one turn).

## Critical files (new)
- `train/`: `inventory.py`, `extract.py`, `split.py`, `gen_knowledge.py`, `gen_slides.py`, `mix.py`, `sft.py`, `export_gguf.sh`, `eval_closedbook.py`, `regurgitation.py`, `configs/*.yaml`, `run.sh` (tmux + status.json)
- `tutor/`: `engine.py` (llama-server client + summary + calculator tool), `screen/` (hotkey capture, OCR, direct pptx/pdf read, vision fallback), `ui/`
- `eval/ab.py`: blind A/B tool
- Reuse `bench/hw/server_probe.py` (llama-server launch/health/stream logic) for the client.
- After approval: update `docs/01_PLAN_RELIABILITY_TUTOR.md` and the project memory to this closed-book, distributable-model design. Both currently describe the retrieval-library design.

## Verification
- **Pilot:**
  - The GGUF loads on the laptop with a matching hash.
  - On held-out sections of the pilot chapter, the closed-book quiz score is ≥ the base model's.
  - The regurgitation test is clean.
  - The general-skill test shows no large drop.
- **Full model:**
  - The user's blind A/B on real held-out slides favours the fine-tuned model.
  - Numericals match the calculator tool's results.
  - Hinglish questions get Hinglish answers.
- **Screen:**
  - On native Windows, a hotkey capture of an unseen PPT/PDF page is read correctly (the stated slide content is right) on ≥ 9 of 10 pages.
  - No capture happens without the hotkey.
  - VRAM is ≤ 3.8 GB during a vision pass.
- **Offline:** every feature works with networking disabled.

## Decision log
The user delegated technical decisions to Claude on 2026-10-17. Each decision is listed with its reason, so it can be reviewed or reversed.

| Date | Decision | Reason |
|---|---|---|
| 10-17 | No user-supplied test PPTs or past-year papers. The user evaluates the final system personally. | User's instruction. |
| 10-17 | Base model: **Gemma-4-E4B**, not Qwen3.5-4B. | Blind A/B 11–4 (`eval/out/base_pick_v1`). Qwen answers Hinglish in Devanagari. |
| 10-17 | Books: **K.K. Agarwal** (primary) and **Modarres** (depth). Ebeling is deferred because it is fully scanned. The rest are too specialised or not reliability books. | Agarwal fits Indian semester syllabi and has problems with answers. Modarres covers FTA, Markov models and statistics more deeply. |
| 10-17 | The teacher (Qwen3.5-27B + mmproj) transcribes **page images** into LaTeX before any generation. | The books' text layers are old OCR with broken formulas. A two-page test gave exact formulas and equation numbers. |
| 10-17 | Pilot chapter: Agarwal **ch. 3, Series-Parallel Systems**. | Most exam-heavy and numerical. Ch. 2 is generic probability. |
| 10-17 | Screen interaction: hotkey → voice + screen together. The mouse becomes a marker for a sub-region. | User's instruction. The training format mirrors it (`tutor/context.py`). |
| 10-17 | Numericals are kept only if the teacher's Python reproduces its stated answers (rel. tol. 2e-3). | Wrong worked numbers are the most harmful tutor error. |
| 10-17 | Q4_K_M export uses `--token-embedding-type q4_K --tensor-type per_layer_token_embd=q5_K`. | Matches the unsloth file measured on the laptop (3.35 GB VRAM). Plain Q4_K_M makes the tied embedding/output layer Q6_K, adding 0.17 GB of VRAM, and the laptop has only about 0.65 GB of headroom. |
| 10-17 | The section splitter accepts only the next section number. | Agarwal prints subsection 3.5.1 as "3.51". The pilot's sections 3.6–3.8 were merged into 3.51/3.71/3.81: content is complete, titles are wrong. Fixed for the full run. |
| 10-17 | Generated data must never cite the book (author, chapter, section, equation or figure numbers, "the text"). The prompt forbids it and the build drops any leftovers. The first pilot generation was discarded (`data/pilot/gen_v0_bookrefs`). | 45 % of first-pass examples said things like "Agarwal 3.1 ke hisaab se". Users won't have the book, and citations also hint at memorised source text. |
| 10-17 | Replay = self-distillation. Teacher-written general prompts (EN/Hinglish/HI) are answered by the untouched base GGUF; wrong-language replies are dropped. | Preserves general skills without importing another model's style, and avoids reinforcing the base's Hinglish→English habit. |
| 10-17 | Training memory: loss computed in 1,024-token chunks with recomputation, 4,096-token batches, and the audio/vision towers unloaded during text training. SDPA attention for text, eager for audio. Peak 22.1 GB of 23.5 GB, ~1,650 tok/s. | The first attempt hit OOM at 23.1 GB: one 5.2 GB logits tensor from the 262k vocabulary. The towers are unused in text training; the export merges into the full base, so the GGUF keeps them. |
| 10-17 | The replay language filter measures Devanagari share among **letters**, with a threshold of 0.5 for Hindi. | Measuring over all characters let digits, spaces and markup dilute it, so only 6/100 Hindi answers passed. |
| 10-17 | **Pilot v1 result:** right language 100% (base 37%), but held-out quiz 29% vs base 53% (n=19). No regurgitation (max run 7 words vs base 8). Archived in `archive/pilot_v1`. | The answers learned brevity (3–5× shorter) and lost reasoning. Example: it claimed "MTBF ∝ exp(m)" where the base correctly said logarithmic. Val loss bottomed at epoch ~2. |
| 10-17 | **Pilot v2:** teacher answers show full step-by-step reasoning (250–600 words); lr 5e-5, 2 epochs; 3 generation rounds; 36-item quiz with no book references; the system prompt mentions the screen only when a [SCREEN] block exists. | Fixes the v1 causes: the style-over-knowledge shortcut, overfitting, and the base model saying "The marked region shows…" with no screen. |
| 10-17 | Hindi (Devanagari) replay is dropped from self-distillation: base Gemma answers Hindi prompts in English (98/100). Hindi comes only from teacher data. | The language filter was right. Self-distilled Hindi doesn't exist. |
| 10-17 | **Pilot v2 extended eval** (`run/eval_pilot`, 73 fresh items: 23 held-out, 50 on trained sections). Content: base 50/49 %, base + strict language prompt **67/49 %**, v2 fine-tune 30/46 %. Right language: base 35–44 %, strict 60 %, v2 **96 %**. | Fine-tuning on teacher-written answers taught language, but **no knowledge gain even on trained sections**, and a loss on unseen material. A prompt alone reaches only 60 % right language. |
| 10-17 | **Pilot v3 = on-policy correction** (`train/onpolicy.py`). The base model drafts every training answer (strict prompt). The teacher edits the draft, fixing errors and converting language/script while keeping structure and wording. Kept only if the language/script is right, there are no book references, and numericals match the verified answers. | Targets stay close to the base model's own distribution, so it learns language and corrections without restyling. This is the standard remedy for style-drift damage in small-model SFT. |
| 10-17 | **Pilot v3 result:** content 37 % held-out / 39 % trained (base 50/49, base-strict 67/51), right language 96 %. 614/866 teacher fixes were "major". | On-policy correction degenerates into a rewrite, because base drafts are mostly in the wrong language. Across v1–v3, fine-tuning always fixed language and never added content. This is consistent with Ovadia et al. 2024. |
| 10-17 | Stop tuning SFT knobs. Test inference-time alternatives on the same quiz: (a) a **per-turn language hint** from rule-based language detection (`tutor/context.py`); (b) an **offline knowledge pack**: BM25 over the teacher's re-worded Q&A notes, not book text (`tutor/knowledge.py`); (c) the v2 fine-tune + pack. | They target the two measured gaps (language, book-specific content) without touching the weights' reasoning. A pack of synthetic notes carries less copyright risk than book text and can be updated without retraining. |
| 10-18 | **Pivot to base + language hint + knowledge pack.** Fine-tuning paused. See "Course change" above. | Hint + pack: useful 78 % vs 25 % base and 44 % best fine-tune (trained sections). The hint alone lifts held-out useful from 22 % to 50 % at no content cost. |
| 10-18 | Full pack from both books: Agarwal PDF 13–346 and Modarres PDF 24–527. Pack plan: per section 10 EN + 3 Hinglish + 2 Hindi Q&A and 5 EN + 2 Hinglish verified numericals. Long sections are split into parts of ≤ 12k chars. | The notes are retrieved and the model answers in the student's language itself, so notes are mostly English. Parts keep each teacher prompt within context. |
| 10-18 | Main evaluation = **Agarwal's own end-of-book problems**, scored against the author's printed answers (EN + Hinglish versions). These are excluded from the pack. | Independent of the teacher. It removes the "the same model wrote the notes and the quiz" bias. |
| 10-18 | Teacher runs with a **q8_0 KV cache**, all layers on the GPU. Shared GPU: `PARALLEL=1` (~17.9 GB used with vision, ~7.5 GB free text-only). Free GPU: `PARALLEL=5` (5 × 24k slots, 21.3 GB). | User's instruction: KV quantization instead of CPU offload. q8_0 lets 5 slots fit where f16 fitted 4. |
| 10-18 | **Full pack built:** 207 section parts (Agarwal 104, Modarres 103); 810 teacher jobs, 0 failed; 3,303 notes. Numericals 925 verified / 196 rejected (17 %); 378 dropped for book references. | — |
| 10-18 | Notes sharing a ≥ 20-word run with the books are removed (`build_pack.py --book`): 84 dropped, **3,219 notes** ship. The unfiltered pack is kept as `pack_unfiltered.jsonl`. | 11 % of notes shared a 12-word run, and some copied up to 72 words. Long verbatim runs must not be distributed. |
| 10-18 | **Author-problem eval** (Agarwal end-of-book, 34 problems with printed numeric answers × EN/Hinglish). Correct and right language: base 32 %, + hint 46 %, + hint + pack **63–65 %**. Correct EN/Hinglish: 59–71 % in every arm. Hinglish right-language: 0 % → 47 % → 97–100 %. | Independent of the teacher. The gain is mostly **language**. Numeric correctness is within noise across arms (n=34, about ±8 pp), so the pack's content benefit on hard numericals is not yet shown. Only 34 of ~180 odd answers were extracted as numeric, so extraction should improve. |
| 10-18 | **Hindi questions → Hinglish answers**; pure Hindi is not required. | User's instruction. `APP_SYSTEM` and `LANG_HINT["hi"]` ask for Hinglish. |
| 10-18 | **App = floating circle**. Activation = click (optional hotkey Ctrl+Shift+Space). The full screen is captured and the mic opened only then, with a frozen-screen overlay where the pointer is a pen for circling. No file upload exists. | User's instruction. The circle is the extension point for future triggers. |
| 10-18 | App stack (Windows, `%LOCALAPPDATA%\OfflineTutor`): PySide6 UI with WebEngine chat (offline KaTeX + marked); llama.cpp **b11714** CUDA 12.4 llama-server (8k ctx, q8_0 KV, `-np 1`); RapidOCR (CPU); sherpa-onnx Whisper-small int8 (CPU, auto language); uv-managed Python 3.12. | Measured on the laptop (RTX 3050 Ti 4 GB): ready 4–5 s, 3.0 GB VRAM, first token 1.4 s, ~33 tok/s; OCR 0.3 s; speech-to-text 1.8 s for 6.4 s audio. Speech and OCR stay on the CPU to leave the GPU to the LLM. |
| 10-18 | Deferred: calculator tool-calling, vision pass on diagram-heavy regions. | Not requested for this build. The OCR + LLM path already handles formula slides. |
| 10-18 | **The app talks back** (user's request). Kokoro en v0.19 (sherpa-onnx, CPU, default voice af_sarah) speaks each sentence as the model streams it. Long sentences are cut at commas for a faster first sound. Markdown/LaTeX is converted to words (`speech_text.py`). The chat window no longer opens by itself; it opens automatically only if speaking is turned off. | Measured: TTS load 2.6 s, 8.5 s of speech in 3.0 s. Kokoro-English reads Hinglish (Roman script) with an English accent. It's intelligible, but a Hindi-capable voice is a possible later upgrade. |
| 10-18 | **Hindi voice for Hinglish answers** (user's request). The answer text goes through a Roman → Devanagari "speech spelling" (`translit.py`):
- Hindi words get Hindi spelling.
- English terms get Indian-English pronunciation.
- Numbers are read in English.

The spelling comes from a 4,892-word lexicon built by the teacher (`train/build_lexicon.py`), with spelling rules as fallback. The default voice is **Piper hi_IN priyamvada**; the Kokoro v1.0 Hindi voices and the other Piper voices are in the menu. English answers keep Kokoro-en. | Voice comparison (`app/voice_eval.py`, 30 real Hinglish sentences): Piper priyamvada had the lowest Whisper-Hindi round-trip error (0.76 vs 0.86 for the old English voice) and synthesizes 9× faster than Kokoro (0.05× vs 0.34–0.46× real time). **Caveat:** absolute error rates are unreliable because sherpa-onnx Whisper-small decodes Devanagari badly (dropped vowel signs, early stops), so the user's ear decides. Samples are in `%LOCALAPPDATA%\OfflineTutor\voice_samples`. |
| 10-18 | **Open issue found by the voice test:** Whisper-small (sherpa-onnx int8) mangles **Hindi/Devanagari speech-to-text**. English and Hinglish-spoken-as-English transcribe fine. Spoken Hindi questions may reach the model garbled. | To fix next: force language detection to Hinglish-friendly output (e.g. `language="en"` for code-mixed speech) or use a better multilingual ASR, measured on real recordings. |
| 10-18 | **The circle is the send button** (user's request). Click → red ➤ capture mode, with the circle kept above the frozen-screen overlay. Click again → send. A click while thinking or speaking interrupts and starts a new question. The overlay's Send button was removed. | One control for the whole interaction, and it's the extension point for future triggers. |
| 10-17 | LoRA r=64 on all language-model linear layers (bf16 base; 4-bit fallback). Loss on replies only. Logits computed only at reply positions. | 8.6B-parameter checkpoint (mostly per-layer embeddings) on a 24 GB GPU; the 262k vocabulary makes full logits too large. |
