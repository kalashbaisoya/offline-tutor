"""Blind A/B comparison of two local models, judged by a person.

generate: runs every question through both models (one llama-server at a time), shuffles which model is
          shown as "A" per question, writes  <out>/sheet.md  (for the judge) and  <out>/key.json  (hidden).
score:    reads the judge's picks ("q01 A", "q02 B", "q03 both-bad", "q04 tie", one per line) and reveals results.

Usage:
  python eval/ab.py generate QUESTIONS.jsonl MODEL_X.gguf MODEL_Y.gguf OUT_DIR
  python eval/ab.py score OUT_DIR PICKS.txt
"""

import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tutor.llm import LlamaServer  # noqa: E402

SYSTEM = (
    "You are an expert Reliability Engineering tutor for university students preparing for semester exams. "
    "Reply in the same language and script the student uses (English, Hindi, or Hinglish). "
    "Explain clearly, show formulas and steps, and say so honestly if you are unsure."
)

# Each model's own recommended non-thinking sampling settings (from the model publishers' guidance).
SAMPLING = {
    "qwen": dict(temperature=0.7, top_p=0.8, top_k=20, presence_penalty=1.5),
    "gemma": dict(temperature=1.0, top_p=0.95, top_k=64),
}


def sampling_for(model):
    return SAMPLING["gemma" if "gemma" in Path(model).name.lower() else "qwen"]


def generate(questions_path, model_x, model_y, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    questions = [json.loads(l) for l in open(questions_path, encoding="utf-8") if l.strip()]
    answers = {}
    for model in (model_x, model_y):
        with LlamaServer(model) as srv:
            for q in questions:
                text, meta = srv.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": q["q"]}],
                                      seed=7, **sampling_for(model))
                answers[(model, q["id"])] = dict(text=text, **meta)
                print(f"{Path(model).name} {q['id']} {meta['seconds']}s", flush=True)

    rng = random.Random(20261016)
    key = {}
    for q in questions:
        pair = [model_x, model_y]
        rng.shuffle(pair)
        key[q["id"]] = {"A": Path(pair[0]).name, "B": Path(pair[1]).name}
    raw = {f"{Path(m).name}|{qid}": v for (m, qid), v in answers.items()}
    (out / "key.json").write_text(json.dumps(key, indent=2))
    (out / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    write_sheet(out, questions, key, raw)
    print("wrote", out / "sheet.md")


def write_sheet(out, questions, key, raw):
    lines = ["# Blind comparison", "",
             "For each question, read A and B and write one line in `picks.txt`: `q01 A`, `q01 B`, `q01 tie` "
             "or `q01 both-bad`. Judge correctness first, then clarity and whether it answers in the right "
             "language. An answer marked *cut off* hit the 1200-token limit (about 30 s of speech/reading).", ""]
    for q in questions:
        lines += [f"---\n\n## {q['id']} ({q['lang']}, {q['type']})", "", f"**Question:** {q['q']}", ""]
        for label in "AB":
            a = raw[f"{key[q['id']][label]}|{q['id']}"]
            lines += [f"### Answer {label}", "", a["text"].strip(), ""]
            if a.get("finish") != "stop":
                lines += ["*(cut off: answer exceeded the length limit)*", ""]
    (Path(out) / "sheet.md").write_text("\n".join(lines), encoding="utf-8")


def score(out_dir, picks_path):
    key = json.loads((Path(out_dir) / "key.json").read_text())
    tally, by_type = Counter(), Counter()
    questions = {}
    for l in open(Path(out_dir).parent.parent / "questions" / "base_pick_v1.jsonl", encoding="utf-8"):
        if l.strip():
            q = json.loads(l)
            questions[q["id"]] = q
    for line in open(picks_path):
        parts = line.split()
        if len(parts) != 2:
            continue
        qid, pick = parts[0], parts[1]
        winner = key[qid][pick] if pick in ("A", "B") else pick
        tally[winner] += 1
        by_type[(questions.get(qid, {}).get("lang", "?"), winner)] += 1
    print("overall:", dict(tally))
    print("by language:", {f"{k[0]}:{k[1]}": v for k, v in sorted(by_type.items())})


if __name__ == "__main__":
    if sys.argv[1] == "generate":
        generate(*sys.argv[2:6])
    elif sys.argv[1] == "score":
        score(*sys.argv[2:4])
    else:
        raise SystemExit(__doc__)
