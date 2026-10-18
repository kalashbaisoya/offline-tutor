"""S5: general replay data, so fine-tuning does not erode general skills (self-distillation).

  prompts   the teacher writes varied general prompts (not reliability) in English, Hinglish and Hindi
  answers   the *untouched base model* (GGUF) answers them under the tutor's system prompt; replies in the wrong
            language/script are dropped, so the base model's Hinglish->English habit is not reinforced

Usage: python train/replay.py prompts OUT_DIR [N_PER_CALL] [CALLS]
       python train/replay.py answers OUT_DIR BASE.gguf
Output: OUT_DIR/prompts.jsonl, OUT_DIR/replay.jsonl (chat format, tutor/context.py)
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import PARALLEL, ROOT, SAMPLING, Status, parse_json, teacher
from tutor.context import messages
from tutor.llm import LlamaServer

TOPICS = ["school/college maths with numbers", "physics and chemistry basics", "programming help (Python, C)",
          "writing an email, application or summary", "general knowledge and current-affairs-style facts (no dates)",
          "everyday advice, study planning, motivation", "logic puzzles and reasoning", "other engineering subjects "
          "(thermodynamics, circuits, strength of materials, probability)", "explaining a concept to a beginner",
          "casual friendly chat"]
LANGS = {"en": "English", "hinglish": "Hinglish (Hindi in Roman script mixed with English, never Devanagari)",
         "hi": "Hindi in Devanagari script"}
PROMPT = """Write {n} different messages that Indian university students might send to a general AI assistant about:
{topic}. Language of every message: {lang}. Vary length and style (some one-liners, some detailed). Not about
reliability engineering. Return JSON: {{"items": ["...", "..."]}}"""

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
HINDI_ROMAN = re.compile(r"\b(hai|hain|kya|mein|ko|ka|ki|ke|aur|nahi|se|bhi|toh|kaise|kyun|yeh|woh|hota|karo|karna)\b", re.I)


def right_language(lang, text):
    letters = [c for c in text if c.isalpha()]  # share among letters: digits, spaces and markup don't dilute it
    deva = sum(bool(DEVANAGARI.match(c)) for c in letters) / max(len(letters), 1)
    if lang == "hi":
        return deva > 0.5  # Hindi answers keep English technical terms, so not ~1.0
    if lang == "hinglish":
        return deva < 0.01 and len(HINDI_ROMAN.findall(text)) >= max(5, len(text.split()) // 25)
    return deva < 0.01


def prompts(out_dir, n=10, calls=1):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(t, l, c) for t in TOPICS for l in LANGS for c in range(int(calls))]
    status, rows = Status(out, "replay_prompts", len(jobs)), []

    def work(job):
        topic, lang, c = job
        text, meta = srv.chat([{"role": "user", "content": PROMPT.format(n=n, topic=topic, lang=LANGS[lang])}],
                              max_tokens=4000, seed=c, response_format={"type": "json_object"}, **SAMPLING)
        try:
            rows.extend(dict(lang=lang, topic=topic, prompt=p) for p in parse_json(text)["items"] if isinstance(p, str))
        except Exception:
            pass
        status.tick(prompts=len(rows))

    with teacher() as srv, ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(work, jobs))
    with open(out / "prompts.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    status.write("done")


def answers(out_dir, base_gguf):
    out = Path(out_dir)
    rows = [json.loads(l) for l in open(out / "prompts.jsonl", encoding="utf-8")]
    status, kept, dropped = Status(out, "replay_answers", len(rows)), [], []
    # Gemma 4 recommended sampling
    sampling = dict(temperature=1.0, top_p=0.95, top_k=64)

    def work(r):
        text, meta = srv.chat(messages(r["prompt"]), max_tokens=1500, **sampling)
        ok = meta["finish"] == "stop" and text.strip() and right_language(r["lang"], text)
        (kept if ok else dropped).append(dict(r, answer=text))
        status.tick(kept=len(kept), dropped=len(dropped))

    with LlamaServer(ROOT / base_gguf, ctx=4096 * PARALLEL, port=8092, llama_bin=ROOT / "third_party/llama.cpp/build/bin",
                     extra_args=["-np", str(PARALLEL)]) as srv, ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(work, rows))
    with open(out / "dropped.jsonl", "w", encoding="utf-8") as f:  # kept for inspection
        for r in dropped:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / "replay.jsonl", "w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(dict(id=f"replay#{len(r['prompt'])}", task="replay", lang=r["lang"],
                                    messages=messages(r["prompt"], r["answer"])), ensure_ascii=False) + "\n")
    by_lang = {l: sum(r["lang"] == l for r in kept) for l in LANGS}
    status.extra.update(kept_by_lang=by_lang, dropped_by_lang={l: sum(r["lang"] == l for r in dropped) for l in LANGS})
    status.write("done")


if __name__ == "__main__":
    {"prompts": prompts, "answers": answers}[sys.argv[1]](*sys.argv[2:])
