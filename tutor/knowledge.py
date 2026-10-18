"""Offline knowledge pack: BM25 search over tutor notes (teacher-written explanations, not book text).

A pack is a JSONL file of {"id", "text"} notes. Pure Python, no dependencies, fast enough for a few
thousand notes on a laptop CPU.
"""

import json
import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+|[ऀ-ॿ]+")
_STOP = set("the a an of to in is are and or for on with by as be it this that what how why which mein ka ki ke ko "
            "hai hain kya se aur bhi toh yeh ye".split())


def tokens(text):
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class KnowledgePack:
    def __init__(self, notes, k1=1.5, b=0.75):
        self.notes, self.k1, self.b = notes, k1, b
        self.docs = [Counter(tokens(n["text"])) for n in notes]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @classmethod
    def load(cls, path):
        return cls([json.loads(l) for l in open(path, encoding="utf-8") if l.strip()])

    def search(self, query, k=3):
        q = set(tokens(query))
        scored = []
        for i, (d, L) in enumerate(zip(self.docs, self.lens)):
            s = sum(self.idf[t] * d[t] * (self.k1 + 1) / (d[t] + self.k1 * (1 - self.b + self.b * L / self.avg))
                    for t in q if t in d)
            if s > 0:
                scored.append((s, i))
        return [self.notes[i] for _, i in sorted(scored, reverse=True)[:k]]


def notes_block(notes, max_chars=4500):
    """Context block placed before the student's question."""
    out, used = [], 0
    for n in notes:
        t = n["text"].strip()[:max_chars - used]
        if not t:
            break
        out.append(t)
        used += len(t)
    return "[NOTES: tutor's offline knowledge pack; use if relevant]\n" + "\n---\n".join(out) if out else ""
