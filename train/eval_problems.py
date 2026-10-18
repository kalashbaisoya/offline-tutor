"""S8b: evaluation on the book author's own end-of-chapter problems, scored against the author's printed answers.

Unlike eval_pilot.py, no teacher-written answer is used as ground truth: the teacher only *extracts* problem
statements and the printed answers (odd-numbered problems) from the cleaned pages, and translates each question to
Hinglish (numbers unchanged). Scoring is automatic: every printed answer must appear in the reply (1 % tolerance).

  extract  OUT/problems.json from the cleaned PROBLEMS + ANSWERS pages
  answer   one arm (same options as eval_pilot.py answer)
  report   OUT/problems_report.md

Usage: python train/eval_problems.py extract PAGES_DIR OUT
       python train/eval_problems.py answer OUT NAME MODEL.gguf [default|strict] [no|yes] [PACK.jsonl]
       python train/eval_problems.py report OUT
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import PARALLEL, ROOT, parse_json, teacher
from eval_pilot import GEMMA_SAMPLING, numeric_ok
from replay import right_language
from tutor.context import SYSTEM, SYSTEM_STRICT, messages, with_lang_hint
from tutor.knowledge import KnowledgePack, notes_block
from tutor.llm import LlamaServer

EXTRACT_ANSWERS = """Below are the printed "ANSWERS TO ODD NUMBERED PROBLEMS" pages of a textbook. Extract every answer
as numbers. Some problems have several parts; give all numeric values in order. Skip answers that are not numeric
(formulas, words). Return JSON: {{"answers": [{{"problem": 1, "values": [0.9409, 0.97]}}]}}

PAGES:
<<<
{text}
>>>"""

EXTRACT_PROBLEMS = """Below are pages of a textbook's PROBLEMS section. Extract the problems whose numbers are in this
list: {wanted}. For each, give the full problem statement exactly as printed (all data needed to solve it, LaTeX for
formulas; include figure descriptions if present), and a faithful Hinglish version of the same question (Hindi in
Roman script mixed with English technical terms; keep every number and unit unchanged). Skip a problem if its
statement is not on these pages.
Return JSON: {{"problems": [{{"problem": 1, "question": "...", "question_hinglish": "..."}}]}}

PAGES:
<<<
{text}
>>>"""


def extract(pages_dir, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pages = [(int(f.stem[1:]), f.read_text(encoding="utf-8")) for f in sorted(Path(pages_dir).glob("p*.md"))]
    start = next(i for i, (_, t) in enumerate(pages) if "ANSWERS TO ODD" in t.upper())
    problem_pages, answer_pages = pages[:start], pages[start:]
    with teacher(ctx_per_slot=24576) as srv:
        text, _ = srv.chat([{"role": "user", "content": EXTRACT_ANSWERS.format(
            text="\n\n".join(t for _, t in answer_pages))}], max_tokens=8000, temperature=0.0,
            response_format={"type": "json_object"})
        answers = {int(a["problem"]): [float(v) for v in a["values"]] for a in parse_json(text)["answers"]
                   if a.get("values")}
        wanted = sorted(answers)
        found = {}

        def work(chunk):
            text, _ = srv.chat([{"role": "user", "content": EXTRACT_PROBLEMS.format(
                wanted=wanted, text="\n\n".join(t for _, t in chunk))}], max_tokens=16000, temperature=0.0,
                response_format={"type": "json_object"})
            for p in parse_json(text).get("problems", []):
                if int(p["problem"]) in answers:
                    found.setdefault(int(p["problem"]), p)

        chunks = [problem_pages[i:i + 4] for i in range(0, len(problem_pages), 4)]
        with ThreadPoolExecutor(PARALLEL) as ex:
            list(ex.map(work, chunks))
    # NB: problem numbers restart per chapter in some books; Agarwal numbers them continuously (1..N)
    items = []
    for num in sorted(found):
        p = found[num]
        for lang, q in (("en", p["question"]), ("hinglish", p.get("question_hinglish"))):
            if q:
                items.append(dict(id=f"p{num:03d}_{lang}", problem=num, lang=lang, question=q, final=answers[num]))
    (out / "problems.json").write_text(json.dumps(dict(items=items), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(answers)} printed answers, {len(found)} problems matched, {len(items)} items")


def answer(out_dir, name, gguf, system="default", hint="no", pack=""):
    out = Path(out_dir)
    items = json.loads((out / "problems.json").read_text(encoding="utf-8"))["items"]
    sys_prompt = {"default": SYSTEM, "strict": SYSTEM_STRICT}[system]
    kp = KnowledgePack.load(ROOT / pack) if pack else None
    res = {}

    def work(it):
        text = with_lang_hint(it["question"]) if hint == "yes" else it["question"]
        if kp:
            block = notes_block(kp.search(it["question"], k=3))
            text = (block + "\n\n" + text) if block else text
        reply, meta = srv.chat(messages(text, system=sys_prompt), max_tokens=2000, seed=7, **GEMMA_SAMPLING)
        res[it["id"]] = dict(text=reply, finish=meta["finish"], correct=numeric_ok(reply, it["final"]),
                             lang_ok=right_language(it["lang"], reply))

    with LlamaServer(ROOT / gguf, ctx=8192 * PARALLEL, port=8093, llama_bin=ROOT / "third_party/llama.cpp/build/bin",
                     extra_args=["-np", str(PARALLEL)]) as srv, ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(work, items))
    (out / f"answers_{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


def report(out_dir):
    out = Path(out_dir)
    items = json.loads((out / "problems.json").read_text(encoding="utf-8"))["items"]
    lines = ["# Author's problems (Agarwal, end of book), scored against the printed answers", "",
             f"{len({i['problem'] for i in items})} problems x (English + Hinglish) = {len(items)} items. "
             "Correct = every printed value appears in the reply (1 % tolerance).", "",
             "| Arm | Correct EN | Correct Hinglish | Right language (Hinglish) | Correct and right language |",
             "|---|---|---|---|---|"]
    for f in sorted(out.glob("answers_*.json")):
        a = json.loads(f.read_text(encoding="utf-8"))
        en = [a[i["id"]] for i in items if i["lang"] == "en" and i["id"] in a]
        hg = [a[i["id"]] for i in items if i["lang"] == "hinglish" and i["id"] in a]
        allr = [a[i["id"]] for i in items if i["id"] in a]
        pct = lambda xs, k: f"{sum(x[k] for x in xs) / len(xs):.0%}" if xs else "-"
        both = sum(x["correct"] and x["lang_ok"] for x in allr) / max(len(allr), 1)
        lines.append(f"| {f.stem[len('answers_'):]} | {pct(en, 'correct')} | {pct(hg, 'correct')} | "
                     f"{pct(hg, 'lang_ok')} | **{both:.0%}** |")
    (out / "problems_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    {"extract": extract, "answer": answer, "report": report}[sys.argv[1]](*sys.argv[2:])
