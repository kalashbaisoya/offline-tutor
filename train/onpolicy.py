"""S3b: on-policy correction. The student model's own answers, corrected by the teacher, become the targets.

Why: training on teacher-written answers (pilot v1/v2) taught the right language but shifted the small model toward
another writer's style and cost reasoning quality on unseen material. Here the targets stay close to what the base
model already writes; only errors, language/script and book references change.

  draft    the untouched base GGUF answers every training prompt (strict language prompt, so drafts start closer)
  fix      the teacher sees the book section, the prompt, its own reference answer and the draft, and returns the
           draft with every error fixed, in the student's language and script, keeping structure and wording
  build    keep a fixed answer only if it is in the right language/script, has no book reference and, for
           numericals, states every verified final answer -> OUT/sft.jsonl

Usage: python train/onpolicy.py draft SFT_IN.jsonl OUT BASE.gguf
       python train/onpolicy.py fix SFT_IN.jsonl SECTIONS.jsonl OUT
       python train/onpolicy.py build SFT_IN.jsonl OUT
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import PARALLEL, ROOT, Status, parse_json, teacher
from eval_pilot import numeric_ok
from gen import BOOK_REF
from replay import right_language
from tutor.context import SYSTEM_STRICT, messages
from tutor.llm import LlamaServer

LANG_NAME = {"en": "English", "hinglish": "Hinglish: Hindi in Roman (Latin) script mixed with English technical terms, "
             "never Devanagari", "hi": "Hindi in Devanagari script, technical terms in English"}

FIX = """You are improving training data for a small offline Reliability Engineering tutor model. Below are a
textbook SECTION (for fact-checking only), a STUDENT message, a REFERENCE answer written by an expert, and a DRAFT
answer written by the small model itself.

Return the final tutor answer by EDITING the DRAFT:
- Keep the draft's structure, length, formatting and wording wherever they are correct. Do not restyle it.
- Fix every factual, conceptual, formula or arithmetic error, using the SECTION and the REFERENCE as ground truth.
  If a step is missing or wrong, repair that step. If the draft is cut off, complete it in the same style.
- If the draft is mostly wrong or off-topic, write a new answer in the draft's style instead.
- Required language and script: {lang}. Convert the whole answer to it if the draft uses another one.
- Remove greetings, "As your tutor" preambles, and any mention of a book, author, chapter, section, equation or
  figure number. Only if the student message has a [SCREEN: ...] block should the answer start with one short line
  saying what it reads there; otherwise remove such a line.

SECTION:
<<<
{section}
>>>

STUDENT:
<<<
{user}
>>>

REFERENCE:
<<<
{reference}
>>>

DRAFT:
<<<
{draft}
>>>

Return JSON: {{"answer": "...", "changes": "none|minor|major"}}"""


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def lang_of(rec):
    l = str(rec.get("lang", "")).lower()
    return "hinglish" if "hinglish" in l else "hi" if l in ("hi", "hindi") else "en" if l in ("en", "english") else None


def draft(sft_in, out_dir, base_gguf):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "drafts.jsonl"
    done = {json.loads(l)["id"] for l in open(path, encoding="utf-8")} if path.exists() else set()
    rows = [r for r in load(sft_in) if r["id"] not in done]
    status = Status(out, "draft", len(rows))
    sampling = dict(temperature=0.7, top_p=0.95, top_k=64)

    def work(r):
        user = r["messages"][1]["content"]
        text, meta = srv.chat(messages(user, system=SYSTEM_STRICT), max_tokens=1800, **sampling)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(id=r["id"], draft=text, finish=meta["finish"]), ensure_ascii=False) + "\n")
        status.tick()

    with LlamaServer(ROOT / base_gguf, ctx=6144 * PARALLEL, port=8092, llama_bin=ROOT / "third_party/llama.cpp/build/bin",
                     extra_args=["-np", str(PARALLEL)]) as srv, ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(work, rows))
    status.write("done")


def fix(sft_in, sections_path, out_dir):
    out = Path(out_dir)
    sections = {s["id"]: s["text"] for s in load(sections_path)}
    drafts = {d["id"]: d for d in load(out / "drafts.jsonl")}
    path = out / "fixed.jsonl"
    done = {json.loads(l)["id"] for l in open(path, encoding="utf-8")} if path.exists() else set()
    rows = [r for r in load(sft_in) if r["id"] in drafts and r["id"] not in done and lang_of(r)]
    status, failed = Status(out, "fix", len(rows)), []

    def work(r):
        d = drafts[r["id"]]
        prompt = FIX.format(lang=LANG_NAME[lang_of(r)], section=sections.get(r["section"], "(general question)"),
                            user=r["messages"][1]["content"], reference=r["messages"][2]["content"],
                            draft=d["draft"] + ("\n[DRAFT WAS CUT OFF HERE]" if d["finish"] != "stop" else ""))
        try:
            text, meta = srv.chat([{"role": "user", "content": prompt}], max_tokens=6000, temperature=0.3, top_p=0.9,
                                  response_format={"type": "json_object"})
            res = parse_json(text)
            assert meta["finish"] == "stop" and isinstance(res.get("answer"), str)
        except Exception:
            failed.append(r["id"])
            status.tick(failed=len(failed))
            return
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(id=r["id"], answer=res["answer"], changes=res.get("changes")), ensure_ascii=False)
                    + "\n")
        status.tick(failed=len(failed))

    # ~8k tokens per job (section + reference + draft + answer). Teacher fits 6 x 12k slots on 24 GB (8 x 16k OOMs).
    with teacher(ctx_per_slot=12288) as srv, ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(work, rows))
    status.write("done")


def build(sft_in, out_dir):
    out = Path(out_dir)
    fixed = {f["id"]: f for f in load(out / "fixed.jsonl")}
    stats, n = {}, 0
    with open(out / "sft.jsonl", "w", encoding="utf-8") as sft:
        for r in load(sft_in):
            f = fixed.get(r["id"])
            if not f:
                continue
            ans, lang = f["answer"].strip(), lang_of(r)
            reason = ("wrong_language" if not right_language(lang, ans) else
                      "book_ref" if BOOK_REF.search(ans) else
                      "numeric_mismatch" if r.get("final") and not numeric_ok(ans, r["final"]) else None)
            stats[reason or "kept"] = stats.get(reason or "kept", 0) + 1
            stats[f"changes_{f.get('changes')}"] = stats.get(f"changes_{f.get('changes')}", 0) + 1
            if reason:
                continue
            sft.write(json.dumps(dict(r, messages=messages(r["messages"][1]["content"], ans)), ensure_ascii=False)
                      + "\n")
            n += 1
    (out / "build_stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats))


if __name__ == "__main__":
    {"draft": draft, "fix": fix, "build": build}[sys.argv[1]](*sys.argv[2:])
