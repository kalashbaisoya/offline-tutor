"""Hinglish -> Devanagari pronunciation lexicon for the app's Hindi voice (app/tutor_app/translit.py).

Hindi TTS voices read Devanagari. Spoken Hinglish is Hindi words + English technical words said the Indian way, so
every Roman-script word gets the Devanagari spelling an Indian teacher would *say*:
  Hindi words   -> standard Hindi spelling      (hai -> है, mein -> में, hota -> होता)
  English words -> Indian-English pronunciation (reliability -> रिलायबिलिटी, equals -> इक्वल्स)
The word list comes from Hinglish text in our data plus the base model's own Hinglish replies (the style the app
speaks) and the words speakable() produces for formulas and numbers. Unknown words fall back to rules at runtime.

Usage: python train/build_lexicon.py OUT.json INPUT.jsonl|answers.json [...]
"""

import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import PARALLEL, ROOT, parse_json, teacher

sys.path.insert(0, str(ROOT / "app"))
from tutor_app.speech_text import speakable  # noqa: E402
from tutor.context import detect_lang  # noqa: E402

ALWAYS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
          "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand lakh "
          "million point percent equals plus minus times over squared cubed power root square natural log of to the "
          "sum product integral infinity approximately less greater than or equal not and so on gives lambda mu beta "
          "eta theta alpha sigma gamma delta pi e").split()

PROMPT = """You are building a pronunciation lexicon for a Hindi text-to-speech voice that must speak Hinglish (Hindi
written in Roman letters, mixed with English technical words) used by an Indian Reliability Engineering tutor.

For each word below give its Devanagari spelling exactly as an Indian teacher would SAY it in Hinglish:
- Hindi words -> standard Hindi spelling: hai->है, mein->में, hota->होता, nahi->नहीं, kyunki->क्योंकि, yaani->यानी
- English words -> Indian-English pronunciation written in Devanagari: reliability->रिलायबिलिटी, system->सिस्टम,
  failure->फेलियर, probability->प्रोबेबिलिटी, equals->इक्वल्स, parallel->पैरेलल, the->द, formula->फ़ॉर्मूला
- Ambiguous words: choose the reading most likely in Hinglish tutoring text (e.g. "main" -> मैं, "to" -> तो when
  Hindi-like, but "to the power" contexts are English: give तो only if it is more common as Hindi).
- Single letters used as variables (r, q, m, n, x): spell the English letter: r->आर, q->क्यू, m->एम.
Return JSON mapping every input word (lowercase, unchanged) to Devanagari: {{"word": "देवनागरी", ...}}

WORDS: {words}"""

DEVANAGARI = re.compile(r"^[ऀ-ॿ‌‍ ]+$")


def texts(path):
    p = Path(path)
    if p.suffix == ".jsonl":
        for l in open(p, encoding="utf-8"):
            r = json.loads(l)
            for m in r.get("messages", [])[1:]:
                yield m["content"]
            if "text" in r:
                yield r["text"]
    else:  # eval answers: {id: {"text": ...}}
        for v in json.loads(p.read_text(encoding="utf-8")).values():
            if isinstance(v, dict) and "text" in v:
                yield v["text"]


def main(out, *inputs):
    counts = Counter()
    for path in inputs:
        for t in texts(path):
            for para in t.split("\n"):
                if detect_lang(para) == "hinglish":
                    counts.update(w.lower() for w in re.findall(r"[A-Za-z]+", speakable(para)))
    words = sorted({w for w, c in counts.items() if c >= 2} | set(ALWAYS))
    print(f"{len(counts)} distinct Hinglish-context words, {len(words)} to transliterate")
    out = Path(out)
    lex = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    todo = [w for w in words if w not in lex]
    batches = [todo[i:i + 150] for i in range(0, len(todo), 150)]

    def work(batch):
        try:
            text, _ = srv.chat([{"role": "user", "content": PROMPT.format(words=", ".join(batch))}], max_tokens=8000,
                               temperature=0.1, response_format={"type": "json_object"})
            for w, d in parse_json(text).items():
                w, d = str(w).lower().strip(), str(d).strip()
                if w in batch and DEVANAGARI.match(d):
                    lex[w] = d
        except Exception as e:
            print("batch failed:", e)

    with teacher(ctx_per_slot=12288) as srv, ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(work, batches))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(sorted(lex.items())), ensure_ascii=False, indent=0), encoding="utf-8")
    missing = [w for w in words if w not in lex]
    print(f"lexicon: {len(lex)} words -> {out}; missing {len(missing)}: {missing[:30]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
