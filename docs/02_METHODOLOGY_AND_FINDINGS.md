# How we trained a small language model (SLM) as an offline Reliability Engineering tutor, and what we found

Status: October 2026. This document records the complete methodology, the measured results, and the conclusions,
including the negative ones. Every number below comes from a file in [`docs/results/`](results/) or from the
decision log in [`01_PLAN_RELIABILITY_TUTOR.md`](01_PLAN_RELIABILITY_TUTOR.md).

---

## 1. Goal and constraints

- **Product:** an offline tutor that university students (India; English, Hindi, Hinglish) can talk to without
  limits. The student shares context **only** through their screen and voice: they click a floating circle, speak,
  and circle part of a slide or PDF. Nothing is uploaded.
- **Subject:** Reliability Engineering (semester-exam level).
- **Hardware target:** an ordinary laptop with a **4 GB GPU**. Measured on an RTX 3050 Ti Laptop, i7-11800H, 16 GB RAM.
  Phase 0 showed a ~4B model at 8k context is the largest that fits; anything bigger spills to system RAM and
  slows 7–12×.
- **Training hardware:** one shared server with an RTX 4090 (24 GB), 125 GB RAM, 32 cores.
- **The original hypothesis to test:** *a 4B model fine-tuned on reliability-engineering textbooks becomes a better
  tutor than the same model without fine-tuning*, with the subject knowledge living in the weights.

## 2. Models

| Role | Model | Why |
|---|---|---|
| Student (the SLM) | **Gemma-4-E4B-it** (8.6B total params, ~4B effective; Q4_K_M GGUF for the laptop) | Won a blind base-model A/B against Qwen3.5-4B, 11–4 (n=20). Qwen answered Hinglish in Devanagari. |
| Teacher (data writer, transcriber, judge) | **Qwen3.5-27B** Q4_K_M + mmproj (vision), via llama.cpp on the server | Strongest model that fits the 24 GB server GPU with room for parallel requests |

Both models are permissively licensed. The runtime is llama.cpp (llama-server) everywhere.

## 3. Training data pipeline (server, `train/`)

All steps are resumable, run detached in tmux, and write `status.json`.

### 3.1 Sources
- Of 11 candidate books, two were chosen: **K.K. Agarwal, *Reliability Engineering*** (Indian syllabus, problems with
  answers) and **M. Modarres, *Reliability Engineering and Risk Analysis*** (depth: FTA, Markov models, statistics).
- Rejected: Ebeling, which is fully scanned; the others were too specialised, or not about reliability.

### 3.2 Page transcription with a vision teacher (`clean_pages.py`)
- **Problem:** the PDFs' text layers are old OCR with broken formulas (`fIx) = b x exp[-(b x2/2)], 0 ~ x < 00`).
- **Method:** the teacher sees each **page image** plus the raw text layer and writes faithful Markdown + LaTeX,
  keeping equation numbers, tables and one-line figure descriptions, and adding nothing.
- **Result:** 838 pages (Agarwal 334, Modarres 504). 0 pages fell back to raw OCR.

### 3.3 Sections and hold-out (`sections.py`)
- Pages are split at numbered headings. Only the next number in sequence starts a section, because Agarwal prints
  subsection 3.5.1 as "3.51", which broke the first splitter.
- Long sections are cut into ≤ 12k-character parts.
- **~10 % of sections are held out** (chosen by hash) and never reach the teacher.
- Result: 207 section parts.

### 3.4 Synthetic training data (`gen.py`)
For each training section the teacher wrote:
- **Concept Q&A** in English, Hinglish (Roman script) and Hindi: definitions, intuition, comparisons, derivations,
  misconceptions, and exam-style "5-mark" answers.
- **New numericals** with step-by-step solutions **and a Python program that computes the answers**. An item is kept
  only if running the program reproduces the stated answers (relative tolerance 2e-3).
- **Synthetic lecture slides → "hotkey turns"** in the app's exact prompt format: `[SCREEN: marked region]`,
  `[SCREEN: full slide]`, `[STUDENT: voice]`. The screen text gets **OCR-style noise**: flattened sub- and superscripts, misreads such as l↔1 and λ→A., merged lines.
- **Honesty examples:** unreadable screen, things not on screen, table values that must not be invented, off-topic
  questions.

Quality filters:
- No answer may cite the book (author, chapter, section or equation numbers). 45 % of the first batch did, so the
  prompt was fixed and a regex filter drops any leftovers.
- Malformed items are skipped and counted.

### 3.5 Replay data (`replay.py`)
To protect general skills, the teacher wrote about 300 general prompts in English, Hinglish and Hindi. The
**untouched base model answered them** (self-distillation). Replies in the wrong language or script were dropped,
because the base model tends to answer Hinglish and Hindi in English.

### 3.6 Data volumes (pilot = Agarwal ch. 3, *Series-Parallel Systems*)
- Pilot v1: 582 examples. Pilot v2/v3: 889 examples. Plus 122–129 replay examples.
- Numerical verification pass rates: 90 % (v1, 126 / 140) and 98.6 % (v2, 208 / 211) in the pilot; 82.5 % for the
  full books (925 / 1,121).

## 4. Fine-tuning (`sft.py`)

### 4.1 Setup
| Setting | Value |
|---|---|
| Method | LoRA, r = 64, α = 64, dropout 0.05, on all language-model attention and MLP projections (139.5 M trainable params) |
| Precision | bf16 base. Audio and vision towers unloaded during text-only training. |
| Loss | Assistant reply tokens only (prompt masked) |
| Memory | Gemma's 262k vocabulary makes full logits 5+ GB, so the loss is computed in 1,024-token chunks with recomputation. 4,096-token length-bucketed batches, gradient checkpointing. Peak 22.1 GB on the 24 GB card, ~1,650 tok/s. |
| Schedule | AdamW, warm-up + cosine. v1: lr 1e-4, 3 epochs. v2/v3: lr 5e-5, 2 epochs. 5 % validation split; best checkpoint by validation loss. |
| Export | Merge → GGUF (bf16) → **Q4_K_M** with `--token-embedding-type q4_K --tensor-type per_layer_token_embd=q5_K` (matches the laptop-tested quantization: ~3.0–3.35 GB VRAM). Plain Q4_K_M costs +0.17 GB VRAM, and the laptop has only ~0.65 GB headroom. |

### 4.2 Three pilots
| Pilot | Data recipe | What changed and why |
|---|---|---|
| v1 | Teacher-written answers, "concise 120–350 words" | Baseline recipe |
| v2 | Teacher-written answers, **full step-by-step reasoning (250–600 words)**, lr halved, 2 epochs, 3 generation rounds | v1 learned brevity and lost reasoning, and val loss rose after epoch ~2 |
| v3 | **On-policy correction:** the base model drafts every answer, and the teacher edits the draft (fixes errors, converts language, keeps wording) | Keep targets close to the student's own style, to avoid style-drift damage |

## 5. Evaluation design (`eval_pilot.py`, `eval_problems.py`)

- **Held-out quiz:** questions on sections the model never trained on. Tests whether fine-tuning hurts
  generalisation.
- **Trained-section quiz:** *new* questions on trained sections. Tests whether knowledge was actually injected.
- **Scoring:**
  - Numericals: auto-scored, every reference value must appear in the reply within 1 %.
  - Concepts: scored 0–2 by the teacher against key points, blind to which model wrote the answer.
  - Language/script: checked by rule.
  - **Useful** = correct *and* in the student's language.
- **Author's own problems:** Agarwal's end-of-book problems, scored against the **printed answers**, in English and
  in a Hinglish translation. This removes the bias of the teacher writing both the notes and the quiz.
- **Regurgitation probes:** the model continues 30 book passages; we record the longest verbatim run.
- **Laptop runtime** measured on the target machine (VRAM, first-token latency, tok/s).

## 6. Results

### 6.1 Fine-tuning did not make a better tutor
73 fresh quiz items: 23 from a held-out section, 50 from trained sections. Source: [`pilot_eval_all_arms.md`](results/pilot_eval_all_arms.md).

| Arm | Content, held-out | Content, trained | Right language | Useful, trained |
|---|---|---|---|---|
| Base Gemma-4-E4B | 52 % | 48 % | 35–44 % | 25 % |
| Base + strict language prompt | 67 % | 50 % | 60 % | 32 % |
| Fine-tune v2 (teacher answers) | 30 % | 45 % | 96 % | 44 % |
| Fine-tune v3 (on-policy) | 37 % | 39 % | 96 % | 38 % |
| **Base + per-turn language hint + knowledge pack** | 63 % | **81 %** | **92 %** | **78 %** |

- Every fine-tune **fixed the answer language** (96 % in the student's language/script).
- **None added subject knowledge.** Even on *trained* sections, content did not rise above the base model (39–46 % vs 48 %).
- All of them **lost reasoning quality on unseen material**: 30–37 % vs 52 %.
- No memorisation: longest verbatim run 4–8 words in every arm, against a 15-word alarm threshold.

### 6.2 What worked instead: inference-time fixes
- **Language hint:** the app detects the question's language and appends a one-line instruction.
- **Knowledge pack:** BM25 search over 3,219 teacher-written notes, *not* book text. Notes sharing a 20+-word run with
  the books were removed.

On the author's own problems (34 problems × EN/Hinglish, printed answers; [`author_problems_eval.md`](results/author_problems_eval.md)):

| Arm | Correct EN | Correct Hinglish | Hinglish replies in Hinglish | Correct and right language |
|---|---|---|---|---|
| Base | 65 % | 65 % | 0 % | 32 % |
| + hint | 62 % | 68 % | 47 % | 46 % |
| + hint + pack (shipped) | 59–71 % | 62–68 % | 97–100 % | **63–65 %** |

The gain is almost entirely **language**. **Numerical correctness stayed at about 60–70 % in every arm.**

### 6.3 The shipped app (laptop, RTX 3050 Ti 4 GB)

| Component | Measured |
|---|---|
| Model | Gemma-4-E4B Q4_K_M, 8k context, q8_0 KV: ~3.0 GB VRAM, ready in 4–9 s, first token ~1.5 s, ~33 tok/s |
| OCR (RapidOCR, CPU) | ~0.3 s per screen |
| Speech-to-text (Whisper-small int8, CPU) | 1.8 s for 6.4 s of English speech |
| Voice (CPU) | Kokoro (English); Piper hi_IN priyamvada (Hinglish, 0.05× real time), reading a Devanagari "speech spelling" from a 4,892-word lexicon |

## 7. Observations from hands-on use (project owner)

> **The model did not perform well on mathematics, it hallucinated, and small language models are not good as
> tutors.** *(Kalash Baisoya, after using the system.)*

The measurements agree:
- **Mathematics:**
  - Even with the knowledge pack, **about one in three of the book author's own numerical problems is answered
    wrongly** (59–71 % correct).
  - Multi-step numericals are where the 4B model breaks: arithmetic slips, a wrong formula picked for the
    configuration, or a correct formula applied to the wrong quantity.
  - A tutor must be right on exactly these problems, because the student cannot tell a wrong derivation from a
    right one.
- **Hallucination:**
  - The fine-tuned pilots stated invented facts confidently. For example, "MTBF of a parallel system grows as
    exp(m)": the correct relationship is a slow, harmonic-sum (≈ logarithmic) growth.
  - A pilot also wrote a wrong parallel-system formula and then "used" the right one.
  - In the base-model selection test, Gemma mislabelled a concept ("β = 1 is pure wear-out") and Qwen3.5-4B invented
    the "four elements of reliability".
  - Fine-tuning made the style more confident without making the content more correct, which is the most harmful
    combination for a tutor.
- **SLMs as tutors:**
  - At the size that fits a 4 GB laptop GPU, the model is a fluent *explainer* and a good *language matcher*.
  - It is not a reliable *tutor*: it cannot be trusted to grade, derive or calculate without an external check.
  - The value we could add came from **outside the weights**: retrieval, language control and (planned) a
    calculator. Fine-tuning didn't add it.

This is consistent with the research the project started from:
- Fine-tuning injects facts poorly (Ovadia et al., EMNLP 2024; Soudani et al. 2024).
- Tutoring outcomes depend on pedagogy and guardrails more than on the model (Bastani et al. 2025; Liu et al. 2026).

## 8. Conclusions

1. **Do not expect a small model to learn a textbook through fine-tuning.** On 4B-class models, three different
   recipes changed style (language, length, confidence) but not knowledge, and each cost reasoning on unseen
   material.
2. **Put knowledge outside the weights:** a searchable pack of re-worded notes gave the biggest improvement measured
   (useful answers 25 % → 78 % on covered material). It also updates without retraining and survives model
   upgrades.
3. **Never let an SLM do the arithmetic unchecked.** A deterministic calculator or symbolic-math tool for every
   numeric step, and a visible "verify this" in the UI, are prerequisites before calling it a tutor.
4. **Use the SLM as an offline study *assistant*, not an authority:** explain what is on screen, answer in the
   student's language, point to the relevant concept, and be honest about uncertainty.

## 9. Limitations of this study
- Small evaluation sets (23 held-out items, 50 trained items, 34 author problems), so differences under ~8–10 points
  are within noise.
- Concept answers were judged by the 27B teacher, not by human experts.
- One subject, one book chapter for the fine-tuning pilots, one model family per role.
- Hindi speech-to-text (Whisper-small via sherpa-onnx) garbles Devanagari, so spoken Hindi questions are unreliable.
  English and Hinglish work.
- **Copyright:** the books are copyrighted. The repository contains code, prompts, statistics and evaluation
  outputs, but **no book text and no models or knowledge packs derived from the books.** Rebuild them yourself from
  books you are licensed to use.

## 10. Reproducing
1. **Server (CUDA GPU ≥ 24 GB):** run `bash train/server/setup.sh`. It builds the environment and llama.cpp and downloads the base and teacher models.
2. **Put your PDFs in place**, then run:
   - `train/run_full_pack.sh` for the knowledge pack;
   - `train/run_full_eval.sh` for the author-problem evaluation;
   - `train/run_pilot.sh`, `train/run_pilot_train.sh` and `train/run_pilot_v3.sh` for the fine-tuning pilots.
3. **Laptop (Windows):** run
   `powershell -ExecutionPolicy Bypass -File app\setup_windows.ps1 -ModelPath <gguf> -PackPath <pack.jsonl>`
   ([README](../README.md)).
