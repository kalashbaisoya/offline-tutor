"""S2: split cleaned pages into numbered sections and mark ~10 % as held out (evaluation only).

Usage: python train/sections.py PAGES_DIR CHAPTER|auto BOOK_ID OUT.jsonl [HOLDOUT_FRACTION] [MAX_CHARS]
Reads PAGES_DIR/pNNNN.md (from clean_pages.py) in page order. A section starts at a heading like
'## 3.4 PARALLEL SYSTEMS'. Only the next number in sequence counts (3.4 -> 3.5, or 3.x -> 4.1 with CHAPTER=auto), so
equation numbers, cross-references and subsections printed as "3.51" never split a section. Sections longer than
MAX_CHARS (default 12000) are cut at paragraph boundaries into parts (ids ...-3.4a, -3.4b), so each fits a teacher
prompt. Held-out sections are chosen whole, by hash.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^#{1,3}\s*(\d+)\.(\d+)\s+(.+?)\s*$")
RUNNING_HEADER = re.compile(r"^(\d{1,3}|Reliability Engineering|Reliability Analysis of .*|\d{1,3}\s+Reliability Engineering"
                            r"|Chapter \d+|\d{1,3}\s+Chapter \d+)\s*$")


def split_long(text, max_chars):
    if len(text) <= max_chars:
        return [text]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > max_chars:
            parts.append(cur.strip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts


def main(pages_dir, chapter, book_id, out_path, holdout=0.1, max_chars=12000):
    auto = chapter == "auto"
    chapter, holdout, max_chars = (0 if auto else int(chapter)), float(holdout), int(max_chars)
    sections, cur, last_minor = [], None, 0
    for f in sorted(Path(pages_dir).glob("p*.md")):
        page = int(f.stem[1:])
        for line in f.read_text(encoding="utf-8").splitlines():
            m = HEADING.match(line)
            if m:
                ch, minor = int(m.group(1)), int(m.group(2))
                next_section = ch == chapter and minor == last_minor + 1
                next_chapter = auto and minor == 1 and (chapter == 0 or ch == chapter + 1)
                if next_section or next_chapter:
                    chapter, last_minor = ch, minor
                    cur = dict(id=f"{book_id}-{ch}.{minor}", book=book_id, chapter=ch, title=m.group(3).strip().title(),
                               pages=[], lines=[])
                    sections.append(cur)
            if cur is None or RUNNING_HEADER.match(line.strip()):
                continue
            if page not in cur["pages"]:
                cur["pages"].append(page)
            cur["lines"].append(line)

    # deterministic hold-out: the sections with the smallest hashes, at least one
    ranked = sorted(sections, key=lambda s: hashlib.sha1(s["id"].encode()).hexdigest())
    held = {s["id"] for s in ranked[:max(1, round(holdout * len(sections)))]}
    n_out = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for s in sections:
            text = re.sub(r"\n{3,}", "\n\n", "\n".join(s.pop("lines"))).strip()
            parts = split_long(text, max_chars)
            for i, part in enumerate(parts):
                sid = s["id"] + ("abcdefghijklmnopqrstuvwxyz"[i] if len(parts) > 1 else "")
                out.write(json.dumps(dict(s, id=sid, split="heldout" if s["id"] in held else "train", chars=len(part),
                                          text=part), ensure_ascii=False) + "\n")
                n_out += 1
            print(f"{s['id']:<16} {'HELD' if s['id'] in held else 'train':<5} {len(text):>6} chars "
                  f"{len(parts)} part(s)  {s['title'][:50]}")
    print(f"{len(sections)} sections -> {n_out} records")


if __name__ == "__main__":
    main(*sys.argv[1:7])
