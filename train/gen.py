"""S3 + S4: teacher-generated training data from the cleaned book sections.

Two phases, so verification and noise can change without paying for teacher calls again:

  generate  one teacher call per (section, task, round), saved raw to OUT/raw/*.json. Resumable.
              qa_<lang>    concept questions + answers (definition, intuition, comparison, derivation,
                           misconception, exam-style)
              num_<lang>   new numericals with worked solutions + a Python program that computes the answers
              slides       synthetic lecture slides -> hotkey turns (marked region + full slide + spoken question)
              honest       once per run: unreadable screen, not-on-screen, unknown table values, off-topic
  build     raw -> OUT/sft.jsonl in chat format (tutor/context.py). Numericals are re-computed by running the
            teacher's Python; items whose numbers disagree are dropped. Screen text gets OCR-style noise.

Only sections with split == "train" are used; held-out sections never reach the teacher.

Usage: python train/gen.py generate SECTIONS.jsonl OUT_DIR [ROUNDS] [pilot|pack]
       python train/gen.py build SECTIONS.jsonl OUT_DIR
"""

import json
import math
import random
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import PARALLEL, ROOT, SAMPLING, Status, parse_json, teacher
from tutor.context import messages, screen_turn

LANG = {
    "en": "Language: English.",
    "hinglish": ("Language: Hinglish, i.e. Hindi written in Roman (Latin) script mixed with English technical terms, the "
                 "way Indian students actually talk and type (e.g. 'Parallel system mein agar ek component fail ho jaye "
                 "to bhi system chalta rehta hai'). Both the student question and the tutor answer must be Hinglish. "
                 "Never use Devanagari."),
    "hi": ("Language: Hindi in Devanagari script, keeping technical terms such as reliability, failure rate and MTTF "
           "in English (Latin script), as Indian teachers do. Both the question and the answer in this style."),
    "mixed": ("Languages: on every slide write interaction 1 in English, interactions 2 and 3 in Hinglish (Hindi in "
              "Roman script mixed with English terms, never Devanagari) and interaction 4 in Hindi (Devanagari script, "
              "technical terms in English)."),
}

BASE = """You are writing training data for a small offline tutor model that helps Indian university students prepare
for Reliability Engineering semester exams. Ground everything in the SECTION below (textbook "{book}", chapter
{chapter}). You may use standard textbook knowledge that is consistent with it; never contradict it, and never copy
long passages: always re-explain in your own words. The students do NOT have this book: never mention the book,
its author, chapter or section numbers, equation numbers, figure numbers, or "the text/passage"; write questions and
answers as general subject knowledge. Write formulas in LaTeX ($...$). Tutor answers are friendly, exam-focused
and above all correct: state the relevant formula, explain why it applies, and show every step of a derivation or
calculation (never skip steps to be brief; a careful 250-600 word answer is better than a short confident one).
Use headings or numbered steps where they help. No greeting or "As your tutor" preamble. {lang_rule}{variation}

SECTION {id}: {title}
<<<
{text}
>>>

"""

TASKS = {
    "qa": """Write {n} different student questions about this section, each with the tutor's answer. Cover a mix of:
definition, intuition (why/how), comparison, derivation walkthrough (step by step), misconception (the student states
something wrong and the tutor corrects it kindly) and exam-style ("write a short note on ...", "5-mark answer").
Make the questions sound like real students: some short and casual, some precise.
Return JSON: {{"items": [{{"kind": "...", "question": "...", "answer": "..."}}]}}""",
    "num": """Write {n} NEW numerical problems that test this section. Use different numbers and situations than the
book's own examples, at realistic exam difficulty (some multi-step). For each give: the problem as a student would
ask it; a step-by-step tutor solution that ends with the final answer(s); the final answers as plain numbers; and a
short Python 3 program (standard library and math only) that computes those answers from the givens and stores them,
in the same order, in a list named `answer`.
If the section has no quantitative content, return {{"items": []}}.
Return JSON: {{"items": [{{"question": "...", "solution": "...", "final": [0.0], "python": "..."}}]}}""",
    "slides": """Turn this section into {n} lecture slides, the way an Indian professor makes a PPT: a title, 3-6 terse
bullets, key formulas, and figure placeholders like [Figure: ...]. Then, for each slide, write 4 interactions with
the tutor app. The student presses a hotkey, may mark part of the slide with the mouse ("region": copy the marked
lines exactly from the slide, or "" if nothing is marked) and asks by voice ("question": short, spoken, often
pointing, like "yeh wala formula kahan se aaya?" or "what does this term mean?"). Some questions should ask to solve,
apply or extend, not only to explain. The tutor "answer" starts with one short line saying what it reads in the
marked region (or on the slide if nothing is marked), then explains using its own knowledge.
Return JSON: {{"slides": [{{"slide": "full slide text", "turns": [{{"lang": "en|hinglish|hi", "region": "...", "question": "...", "answer": "..."}}]}}]}}""",
}

PLANS = {
    # SFT pilots: all languages + screen-turn practice
    "pilot": [("qa", "en", 8), ("qa", "hinglish", 8), ("qa", "hi", 4),
              ("num", "en", 4), ("num", "hinglish", 4), ("num", "hi", 2), ("slides", "mixed", 3)],
    # knowledge pack (tutor/knowledge.py): notes are retrieved, and the tutor answers in the student's language
    # itself, so mostly English notes plus some Hinglish/Hindi phrasings for lexical matching
    "pack": [("qa", "en", 10), ("qa", "hinglish", 3), ("qa", "hi", 2), ("num", "en", 5), ("num", "hinglish", 2)],
}

HONEST = """You are writing training data for a small offline Reliability Engineering tutor used by Indian university
students. The student presses a hotkey, may mark part of the screen, and asks by voice; the app passes OCR text of
the marked region and of the full screen. Write {n} interactions where the tutor must be honest instead of guessing:
(a) the marked region is OCR garbage or cut off mid-formula: the tutor says what it can read and asks the student to
    re-mark or zoom in;
(b) the student asks about something not on screen or unknowable ("sir ne kal class mein kya bola tha?",
    "what's on slide 12?");
(c) the student asks for a specific value from a standard or table that is not shown (e.g. a MIL-HDBK-217 base
    failure rate): the tutor explains where and how to look it up and does not invent numbers;
(d) the question is outside reliability engineering: answer briefly if it is simple general knowledge, otherwise say
    it is outside what the tutor can reliably help with.
Mix languages: 8 English, 12 Hinglish (Roman script, never Devanagari), 4 Hindi (Devanagari, technical terms in
English). Keep answers short and kind. Use "" for region/full when nothing was shared.{variation}
Return JSON: {{"items": [{{"lang": "...", "region": "...", "full": "...", "question": "...", "answer": "..."}}]}}"""


def load_sections(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def variation(r):
    return "" if r == 0 else (f"\nThis is variation round {r + 1}: choose different sub-topics, numbers, situations and "
                              "phrasings than an obvious first pass would.")


def generate(sections_path, out_dir, rounds=1, plan="pilot"):
    out, rounds = Path(out_dir), int(rounds)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    jobs = []
    for r in range(rounds):
        for s in load_sections(sections_path):
            if s["split"] != "train":
                continue
            for task, lang, n in PLANS[plan]:
                prompt = BASE.format(book=s["book"], chapter=s["chapter"], lang_rule=LANG[lang], variation=variation(r),
                                     id=s["id"], title=s["title"], text=s["text"]) + TASKS[task].format(n=n)
                jobs.append((f"{s['id']}__{task}_{lang}__r{r}", prompt))
        if plan == "pilot":
            jobs.append((f"all__honest__r{r}", HONEST.format(n=24, variation=variation(r))))
    jobs = [j for j in jobs if not (out / "raw" / f"{j[0]}.json").exists()]
    status = Status(out, "generate", len(jobs))
    failed = []

    def work(job):
        name, prompt = job
        data, meta = None, {}
        for attempt in range(2):
            try:
                text, meta = srv.chat([{"role": "user", "content": prompt}], max_tokens=18000, seed=1000 * attempt + 7,
                                      response_format={"type": "json_object"}, **SAMPLING)
                data = parse_json(text)
                break
            except Exception:  # bad JSON or a timed-out request: retry once, then count the job as failed
                data = None
        if data is None or meta.get("finish") != "stop":
            failed.append(name)
            status.tick(failed=len(failed))
            return
        (out / "raw" / f"{name}.json").write_text(json.dumps(dict(data=data, meta=meta), ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
        status.tick(failed=len(failed))

    try:
        with teacher(ctx_per_slot=24576) as srv, ThreadPoolExecutor(PARALLEL) as ex:  # longest prompt ~4k + 18k out
            list(ex.map(work, jobs))
    except Exception as e:
        status.write("failed", error=f"{type(e).__name__}: {e}")
        raise
    status.extra["failed_jobs"] = failed
    status.write("done")


# ---------------------------------------------------------------- build

BOOK_REF = re.compile(r"agarwal|modarres|\b(section|chapter|eq\.?|equation|fig\.?|figure)\s*\(?\d+\.\d+|"
                      r"\bthe (text|passage|book)\b|according to the (section|chapter)|"
                      r"(chapter|section) (mein|me|ke hisaab)", re.I)


def check_numerical(item):
    """Run the teacher's program; True if it reproduces every stated final answer."""
    if not isinstance(item, dict) or not isinstance(item.get("final"), list):
        return False
    code = str(item.get("python", "")) + "\nimport json as _j\nprint(_j.dumps([float(x) for x in answer]))\n"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=10, cwd=tmp)
            got = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            return False
    try:
        want = [float(x) for x in item["final"]]
    except (TypeError, ValueError):
        return False
    return bool(want) and len(got) == len(want) and all(
        math.isclose(g, w, rel_tol=2e-3, abs_tol=1e-6) for g, w in zip(got, want))


GREEK = [(r"\left", ""), (r"\right", ""), (r"\lambda", "λ"), (r"\mu", "μ"), (r"\beta", "β"), (r"\eta", "η"),
         (r"\theta", "θ"), (r"\alpha", "α"), (r"\sum", "Σ"), (r"\prod", "Π"), (r"\int", "∫"), (r"\infty", "∞"),
         (r"\leq", "≤"), (r"\geq", "≥"), (r"\le", "≤"), (r"\ge", "≥"), (r"\cdot", "·"), (r"\times", "×"),
         (r"\exp", "exp"), (r"\ln", "ln"), (r"\approx", "≈")]
MISREADS = [("l", "1"), ("1", "l"), ("O", "0"), ("0", "O"), ("λ", "A."), ("μ", "u"), ("β", "B"), ("≤", "~"),
            ("rn", "m"), ("i", "j"), ("e", "c"), ("Σ", "E"), ("=", "-")]


def ocrize(text, rng):
    """What screen OCR returns for a rendered slide: plain symbols, flattened sub/superscripts, some misreads."""
    t = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", text)
    for a, b in GREEK:
        t = t.replace(a, b)
    t = re.sub(r"\\[a-zA-Z]+|[$\\{}^_]", "", t)
    strength = rng.choice([0.0, 0.0, 0.02, 0.05])  # 0: text read directly from the .pptx/.pdf file
    if strength:
        for a, b in MISREADS:
            t = re.sub(re.escape(a), lambda m: b if rng.random() < strength * 3 else m.group(0), t)
        lines = t.splitlines()
        t = "\n".join(l + (" " if rng.random() < strength * 4 else "\n") for l in lines).replace(" \n", " ")
    return t.strip()


def build(sections_path, out_dir):
    out = Path(out_dir)
    split = {s["id"]: s["split"] for s in load_sections(sections_path)}
    stats, n = {}, 0
    with open(out / "sft.jsonl", "w", encoding="utf-8") as sft:
        for f in sorted((out / "raw").glob("*.json")):
            section, task, _ = f.stem.split("__")
            assert section == "all" or split.get(section) == "train", f"held-out section leaked: {f.name}"
            kind, _, lang = task.partition("_")
            data = json.loads(f.read_text(encoding="utf-8"))["data"]
            rng = random.Random(f.stem)
            examples = []

            def add(make):  # one malformed teacher item must not stop the build
                try:
                    examples.append(make())
                except (KeyError, TypeError, AttributeError):
                    stats["malformed"] = stats.get("malformed", 0) + 1

            if kind == "qa":
                for it in data.get("items", []):
                    add(lambda: (lang, it["question"], it["answer"]))
            elif kind == "num":
                for it in data.get("items", []):
                    ok = check_numerical(it)
                    stats["num_ok" if ok else "num_rejected"] = stats.get("num_ok" if ok else "num_rejected", 0) + 1
                    if ok:
                        add(lambda: (lang, it["question"], it["solution"], [float(x) for x in it["final"]]))
            elif kind == "slides":
                for sl in data.get("slides", []):
                    for t in (sl.get("turns", []) if isinstance(sl, dict) else []):
                        def turn():
                            region = ocrize(t["region"], rng) if t.get("region") else None
                            full = ocrize(sl["slide"], rng) if (not region or rng.random() < 0.75) else None
                            return t.get("lang", "?"), screen_turn(t["question"], region, full), t["answer"]
                        add(turn)
            elif kind == "honest":
                for it in data.get("items", []):
                    add(lambda: (it.get("lang", "?"),
                                 screen_turn(it["question"], it.get("region") or None, it.get("full") or None),
                                 it["answer"]))
            for lang_, user, answer, *final in examples:
                if not str(user).strip() or not str(answer).strip():
                    continue
                if BOOK_REF.search(user) or BOOK_REF.search(answer):
                    stats["dropped_book_ref"] = stats.get("dropped_book_ref", 0) + 1
                    continue
                rec = dict(id=f"{f.stem}#{n}", section=section, task=kind, lang=lang_, messages=messages(user, answer))
                if final:
                    rec["final"] = final[0]  # verified numeric answers, used to check later rewrites
                sft.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
                stats[kind] = stats.get(kind, 0) + 1
    stats["total"] = n
    (out / "build_stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats))


if __name__ == "__main__":
    {"generate": generate, "build": build}[sys.argv[1]](*sys.argv[2:])
