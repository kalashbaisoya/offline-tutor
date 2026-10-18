"""Hinglish (Roman script) -> Devanagari *for speech*, so a Hindi voice can say it with a natural accent.

Hindi words get Hindi spelling (hai -> है); English technical words get their Indian-English pronunciation
(reliability -> रिलायबिलिटी). Lookup order: the pronunciation lexicon built on the server
(train/build_lexicon.py), then rules for anything unknown. Numbers are read in English, as in Indian classrooms.
"""

import json
import re

from . import config

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
       "seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def int_words(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + int_words(n % 100))
    if n < 1_000_000:
        return int_words(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + int_words(n % 1000))
    return " ".join(ONES[int(d)] for d in str(n))  # long numbers digit by digit


def number_words(tok):
    """'0.729' -> 'zero point seven two nine', '1000' -> 'one thousand', '10e-4' kept simple."""
    whole, _, frac = tok.partition(".")
    out = int_words(int(whole)) if whole else "zero"
    if frac:
        out += " point " + " ".join(ONES[int(d)] for d in frac)
    return out


# --- rule-based fallback (common Hinglish spelling conventions)
CONS = [("chh", "छ"), ("kh", "ख"), ("gh", "घ"), ("ch", "च"), ("jh", "झ"), ("th", "थ"), ("dh", "ध"), ("ph", "फ"),
        ("bh", "भ"), ("sh", "श"), ("k", "क"), ("g", "ग"), ("c", "क"), ("j", "ज"), ("t", "त"), ("d", "द"), ("n", "न"),
        ("p", "प"), ("b", "ब"), ("m", "म"), ("y", "य"), ("r", "र"), ("l", "ल"), ("v", "व"), ("w", "व"), ("s", "स"),
        ("h", "ह"), ("f", "फ़"), ("z", "ज़"), ("q", "क़"), ("x", "क्स")]
VOWELS = [("aa", "आ", "ा"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"), ("ee", "ई", "ी"), ("ei", "ए", "े"), ("oo", "ऊ", "ू"),
          ("a", "अ", ""), ("i", "इ", "ि"), ("u", "उ", "ु"), ("e", "ए", "े"), ("o", "ओ", "ो")]
VIRAMA = "्"


def rule_translit(word):
    w, i, out, prev_cons = word.lower(), 0, [], False
    while i < len(w):
        for rom, dev in CONS:
            if w.startswith(rom, i):
                at_end = i + len(rom) == len(w)
                if rom == "n" and at_end and not prev_cons and out:  # "hain", "mein" -> nasal
                    out.append("ं")
                else:
                    if prev_cons:
                        out.append(VIRAMA)
                    out.append(dev)
                    prev_cons = True
                i += len(rom)
                break
        else:
            for rom, ind, matra in VOWELS:
                if w.startswith(rom, i):
                    at_end = i + len(rom) == len(w)
                    if prev_cons:
                        out.append("ा" if rom == "a" and at_end else matra)  # final -a is long in Hinglish
                    else:
                        out.append(ind)
                    prev_cons = False
                    i += len(rom)
                    break
            else:
                i += 1  # other characters are dropped
    return "".join(out)


class Transliterator:
    def __init__(self, path=None):
        try:
            self.lex = json.loads((path or config.HINGLISH_LEXICON).read_text(encoding="utf-8"))
        except OSError:
            self.lex = {}

    def word(self, w):
        return self.lex.get(w.lower()) or rule_translit(w)

    def __call__(self, text):
        """Speakable Hinglish (plain words, numbers, punctuation) -> Devanagari for the Hindi voice."""
        text = re.sub(r"\d+(?:\.\d+)?", lambda m: number_words(m.group(0)), text)
        out = []
        for tok in re.findall(r"[A-Za-z]+|[ऀ-ॿ]+|[.,!?;:]|\S", text):
            if tok[0].isascii() and tok[0].isalpha():
                out.append(self.word(tok))
            elif tok in ".!?":
                out.append("।" if tok == "." else tok)
            elif tok in ",;:":
                out.append(",")
            elif "ऀ" <= tok[0] <= "ॿ":
                out.append(tok)
        s = " ".join(out)
        return re.sub(r"\s+([,।!?])", r"\1", s).strip()
