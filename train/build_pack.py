"""Build an offline knowledge pack (tutor/knowledge.py) from generated tutor data: one note per Q&A.

Notes are the teacher's re-worded explanations and worked examples, never book text. Honest/off-topic examples
are skipped. With --book GLOB, any note that shares a run of MAX_RUN or more words with the book pages is dropped
(copyright: the pack may be distributed; the books may not).

Usage: python train/build_pack.py SFT.jsonl [SFT2.jsonl ...] OUT_PACK.jsonl [--book 'data/full/*/clean/pages/*.md']
"""

import glob
import json
import re
import sys

MAX_RUN = 20  # words
SEED = 12  # n-gram size used to find shared runs


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def book_ngrams(pattern):
    grams = set()
    for f in glob.glob(pattern):
        w = words(open(f, encoding="utf-8").read())
        grams.update(tuple(w[i:i + SEED]) for i in range(len(w) - SEED + 1))
    return grams


def longest_shared_run(text, grams):
    w, run, best = words(text), 0, 0
    for i in range(len(w) - SEED + 1):
        run = run + 1 if tuple(w[i:i + SEED]) in grams else 0
        best = max(best, run)
    return best + SEED - 1 if best else 0


def main(argv):
    book = None
    if "--book" in argv:
        i = argv.index("--book")
        book = book_ngrams(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    *inputs, out = argv
    n = dropped = 0
    with open(out, "w", encoding="utf-8") as f:
        for p in inputs:
            for l in open(p, encoding="utf-8"):
                r = json.loads(l)
                if r.get("task") == "honest":
                    continue
                user, answer = r["messages"][1]["content"], r["messages"][2]["content"]
                text = f"Q: {user}\nA: {answer}"
                if book is not None and longest_shared_run(text, book) >= MAX_RUN:
                    dropped += 1
                    continue
                f.write(json.dumps(dict(id=r["id"], section=r.get("section"), lang=r.get("lang"), text=text),
                                   ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} notes -> {out}" + (f" ({dropped} dropped: >= {MAX_RUN}-word run shared with the books)"
                                    if book is not None else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
