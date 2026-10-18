"""S8: base vs fine-tuned, on things the fine-tune never saw.

  quiz     the teacher writes a closed-book quiz from the HELD-OUT sections (EN / Hinglish / Hindi; numericals
           carry Python-verified answers and key points for concept questions)
  answer   each GGUF answers every quiz item and every regurgitation probe (one llama-server at a time)
  judge    numericals: auto-scored (a stated number within 1 % of each answer); concepts: the teacher scores
           key-point coverage 0-2, blind to which model wrote the answer; language/script checked by rule;
           regurgitation: longest verbatim word run shared with the true book continuation
  report   OUT/eval_report.md

Usage: python train/eval_pilot.py quiz SECTIONS.jsonl OUT
       python train/eval_pilot.py answer OUT NAME MODEL.gguf      (run for base and for the fine-tune)
       python train/eval_pilot.py judge OUT
       python train/eval_pilot.py report OUT SECTIONS.jsonl
"""

import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import PARALLEL, ROOT, SAMPLING, Status, parse_json, teacher
from gen import check_numerical
from replay import right_language
from tutor.context import SYSTEM, SYSTEM_STRICT, messages, with_lang_hint
from tutor.knowledge import KnowledgePack, notes_block
from tutor.llm import LlamaServer

QUIZ = """You are writing a closed-book exam quiz to test a small tutor model on the textbook SECTION below. The
model has NOT seen this section and students do not have the book, so never mention the book, chapter, section,
equation or figure numbers. Write {n} questions in {lang}: about half numerical (new numbers, exam style) and
half conceptual (definitions, derivation steps, comparisons, misconceptions). For numericals give the final answers
as plain numbers and a Python 3 program (math only) that computes them into a list named `answer`. For conceptual
questions give 2-4 key points that a correct answer must contain.
Return JSON: {{"items": [{{"type": "numerical|concept", "question": "...", "final": [0.0], "python": "...", "key_points": ["..."]}}]}}

SECTION {id}: {title}
<<<
{text}
>>>"""
LANGS = {"en": "English", "hinglish": "Hinglish (Hindi in Roman script mixed with English terms, never Devanagari)",
         "hi": "Hindi in Devanagari script with technical terms in English"}
JUDGE = """Grade a student-tutor answer against the key points. Score 2 = all key points correct and nothing
seriously wrong; 1 = partly correct or minor errors; 0 = mostly wrong, missing, or contains a serious error.
Judge the content only, not the language.
QUESTION: {q}
KEY POINTS: {k}
ANSWER:
<<<
{a}
>>>
Return JSON: {{"score": 0, "reason": "..."}}"""
GEMMA_SAMPLING = dict(temperature=1.0, top_p=0.95, top_k=64)


def quiz(sections_path, out_dir):
    """Held-out sections test generalisation; fresh questions on TRAINED sections test knowledge injection."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    secs = [json.loads(l) for l in open(sections_path, encoding="utf-8") if l.strip()]
    jobs = [(s, l, n) for s in secs if s["split"] == "heldout" for l, n in (("en", 14), ("hinglish", 14), ("hi", 8))]
    jobs += [(s, l, n) for s in secs if s["split"] == "train" for l, n in (("en", 3), ("hinglish", 3), ("hi", 2))]
    items = []

    def work(job):
        s, lang, n = job
        text, _ = srv.chat([{"role": "user", "content": QUIZ.format(n=n, lang=LANGS[lang], **s)}], max_tokens=10000,
                           response_format={"type": "json_object"}, **SAMPLING)
        for it in parse_json(text)["items"]:
            if not isinstance(it, dict) or "question" not in it:
                continue
            it["type"] = "numerical" if str(it.get("type", "")).startswith("num") else "concept"
            if it["type"] == "numerical" and not check_numerical(it):
                continue  # unverifiable reference answer
            if it["type"] == "concept" and not it.get("key_points"):
                continue
            items.append(dict(it, lang=lang, section=s["id"], split=s["split"]))

    with teacher(ctx_per_slot=24576) as srv, ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(work, jobs))
    # regurgitation probes come from TRAINING sections: 30 spans of 40 + 40 words
    rng = random.Random(0)
    train = [json.loads(l) for l in open(sections_path, encoding="utf-8") if '"train"' in l]
    probes = []
    for _ in range(30):
        words = rng.choice(train)["text"].split()
        if len(words) > 120:
            i = rng.randrange(0, len(words) - 80)
            probes.append(dict(prefix=" ".join(words[i:i + 40]), truth=" ".join(words[i + 40:i + 80])))
    for i, it in enumerate(items):
        it["id"] = f"q{i:03d}"
    (out / "quiz.json").write_text(json.dumps(dict(items=items, probes=probes), ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    print(len(items), "quiz items,", len(probes), "probes")


def answer(out_dir, name, gguf, system="default", hint="no", pack=""):
    """system: default|strict; hint: yes = per-turn language hint (tutor/context.py); pack: knowledge-pack JSONL."""
    out = Path(out_dir)
    sys_prompt = {"default": SYSTEM, "strict": SYSTEM_STRICT}[system]
    kp = KnowledgePack.load(ROOT / pack) if pack else None

    def user_turn(question):
        text = with_lang_hint(question) if hint == "yes" else question
        if kp:
            block = notes_block(kp.search(question, k=3))
            text = (block + "\n\n" + text) if block else text
        return text

    q = json.loads((out / "quiz.json").read_text(encoding="utf-8"))
    res = {}

    def ask(key, msgs, sampling, max_tokens):
        text, meta = srv.chat(msgs, max_tokens=max_tokens, seed=7, **sampling)
        res[key] = dict(text=text, finish=meta["finish"])

    with LlamaServer(ROOT / gguf, ctx=8192 * PARALLEL, port=8093, llama_bin=ROOT / "third_party/llama.cpp/build/bin",
                     extra_args=["-np", str(PARALLEL)]) as srv, ThreadPoolExecutor(PARALLEL) as ex:
        jobs = [(it["id"], messages(user_turn(it["question"]), system=sys_prompt), GEMMA_SAMPLING, 1500)
                for it in q["items"]]
        jobs += [(f"probe{i}", [{"role": "user", "content": "Continue this textbook passage exactly, word for word:\n\n"
                                 + p["prefix"]}], dict(temperature=0.0), 120) for i, p in enumerate(q["probes"])]
        list(ex.map(lambda j: ask(*j), jobs))
    (out / f"answers_{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


NUM = re.compile(r"-?\d+(?:[.,]\d+)?(?:\s*[x×]\s*10\^?\{?-?\d+\}?|e-?\d+)?")


def numbers_in(text):
    vals = []
    for m in NUM.findall(text.replace(",", "")):
        m2 = re.sub(r"\s*[x×]\s*10\^?\{?(-?\d+)\}?", r"e\1", m)
        try:
            vals.append(float(m2))
        except ValueError:
            pass
    return vals


def numeric_ok(text, final):
    vals = numbers_in(text)
    return all(any(abs(v - f) <= 0.01 * abs(f) + 1e-9 or abs(v - 100 * f) <= 0.01 * abs(100 * f) for v in vals)
               for f in final)


def longest_shared_run(a, b):
    a, b = a.lower().split(), b.lower().split()
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def judge(out_dir):
    out = Path(out_dir)
    q = json.loads((out / "quiz.json").read_text(encoding="utf-8"))
    names = sorted(p.stem[len("answers_"):] for p in out.glob("answers_*.json"))
    ans = {n: json.loads((out / f"answers_{n}.json").read_text(encoding="utf-8")) for n in names}
    scores = {n: {} for n in names}
    concept_jobs = []
    for it in q["items"]:
        for n in names:
            a = ans[n][it["id"]]
            s = dict(lang_ok=right_language(it["lang"], a["text"]), cut=a["finish"] != "stop")
            if it["type"] == "numerical":
                s["score"] = 2 * numeric_ok(a["text"], it["final"])
            else:
                concept_jobs.append((n, it, a["text"]))
            scores[n][it["id"]] = s
    rng = random.Random(1)
    rng.shuffle(concept_jobs)  # the judge never sees model names; order is mixed

    def work(job):
        n, it, a = job
        text, _ = srv.chat([{"role": "user", "content": JUDGE.format(q=it["question"], k="; ".join(it["key_points"]),
                                                                       a=a)}],
                           max_tokens=600, temperature=0.0, response_format={"type": "json_object"})
        try:
            scores[n][it["id"]]["score"] = int(parse_json(text)["score"])
        except Exception:
            scores[n][it["id"]]["score"] = None

    with teacher() as srv, ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(work, concept_jobs))
    for n in names:
        scores[n]["_regurgitation"] = [longest_shared_run(ans[n][f"probe{i}"]["text"], p["truth"])
                                       for i, p in enumerate(q["probes"])]
    (out / "scores.json").write_text(json.dumps(scores, indent=1))


def report(out_dir, sections_path):
    out = Path(out_dir)
    q = json.loads((out / "quiz.json").read_text(encoding="utf-8"))
    scores = json.loads((out / "scores.json").read_text())
    lines = ["# Pilot evaluation", "",
             "Fresh quiz questions written by the teacher; none appear in the training data. Score 0-2 per item.",
             "- **held-out**: sections never trained on (does fine-tuning hurt generalisation?)",
             "- **trained**: new questions on trained sections (did the model learn the material?)",
             "- **Useful** = the content score, counted only if the reply is in the student's language/script.", "",
             "| Model | Split | n | Content | Useful | Numerical | Concept | EN | Hinglish | HI | Right language | Cut off |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for split in ("heldout", "train"):
        items = [it for it in q["items"] if it.get("split", "heldout") == split]
        if not items:
            continue
        for n, s in scores.items():
            def mean(pred, useful=False):
                v = [s[it["id"]]["score"] * (s[it["id"]]["lang_ok"] or not useful) for it in items
                     if pred(it) and s[it["id"]]["score"] is not None]
                return f"{sum(v) / (2 * len(v)):.0%}" if v else "-"
            lang_ok = sum(s[it["id"]]["lang_ok"] for it in items) / len(items)
            cut = sum(s[it["id"]]["cut"] for it in items)
            lines.append(f"| {n} | {split} | {len(items)} | {mean(lambda i: True)} | **{mean(lambda i: True, True)}** | "
                         f"{mean(lambda i: i['type'] == 'numerical')} | {mean(lambda i: i['type'] == 'concept')} | "
                         f"{mean(lambda i: i['lang'] == 'en')} | {mean(lambda i: i['lang'] == 'hinglish')} | "
                         f"{mean(lambda i: i['lang'] == 'hi')} | {lang_ok:.0%} | {cut} |")
    lines += ["", "| Model | Regurgitation max / mean run (words) |", "|---|---|"]
    for n, s in scores.items():
        reg = s["_regurgitation"]
        lines.append(f"| {n} | {max(reg)} / {sum(reg) / len(reg):.1f} |")
    lines += ["", "Regurgitation: longest verbatim word run shared with the true 40-word continuation of a training "
              "passage (30 probes, greedy). A run of 15+ words would indicate memorised book text."]
    (out / "eval_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    {"quiz": quiz, "answer": answer, "judge": judge, "report": report}[sys.argv[1]](*sys.argv[2:])
