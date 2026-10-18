"""Turn streamed Markdown + LaTeX answers into speakable sentences for TTS."""

import re

_MATH = re.compile(r"\$\$(.+?)\$\$|\$([^$\n]+?)\$|\\\[(.+?)\\\]|\\\((.+?)\\\)", re.S)
_SYMBOLS = [
    (r"\left", ""), (r"\right", ""), (r"\displaystyle", ""), (r"\,", " "), (r"\;", " "), (r"\quad", " "),
    (r"\cdots", " and so on "), (r"\ldots", " and so on "), (r"\dots", " and so on "),
    (r"\times", " times "), (r"\cdot", " times "), (r"\leq", " less than or equal to "),
    (r"\geq", " greater than or equal to "), (r"\le", " less than or equal to "), (r"\ge", " greater than or equal to "),
    (r"\neq", " not equal to "), (r"\approx", " approximately "), (r"\infty", " infinity "), (r"\to", " to "),
    (r"\rightarrow", " gives "), (r"\Rightarrow", " so "), (r"\implies", " so "), (r"\sum", " sum of "),
    (r"\prod", " product of "), (r"\int", " integral of "), (r"\exp", " e to the "), (r"\ln", " natural log of "),
    (r"\log", " log of "), (r"\lambda", " lambda "), (r"\mu", " mu "), (r"\beta", " beta "), (r"\eta", " eta "),
    (r"\theta", " theta "), (r"\alpha", " alpha "), (r"\sigma", " sigma "), (r"\gamma", " gamma "), (r"\Delta", " delta "),
    (r"\delta", " delta "), (r"\pi", " pi "), (r"\%", " percent "),
]


def _math_to_words(tex):
    t = tex
    for _ in range(3):  # nested fractions / roots
        t = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", r" \1 over \2 ", t)
        t = re.sub(r"\\sqrt\{([^{}]*)\}", r" square root of \1 ", t)
        t = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname|boldsymbol)\{([^{}]*)\}", r" \1 ", t)
    for a, b in _SYMBOLS:
        t = t.replace(a, b)
    t = re.sub(r"\^\{?2\}?", " squared ", t)
    t = re.sub(r"\^\{?3\}?", " cubed ", t)
    t = re.sub(r"\^\{([^{}]*)\}|\^(\S)", lambda m: f" to the power {m.group(1) or m.group(2)} ", t)
    t = re.sub(r"_\{([^{}]*)\}|_(\S)", lambda m: f" {m.group(1) or m.group(2)} ", t)
    t = re.sub(r"\\[a-zA-Z]+", " ", t)
    t = t.replace("=", " equals ").replace("+", " plus ").replace("/", " over ")
    t = re.sub(r"(?<=[\w)\s])-(?=[\s\w(])", " minus ", t)
    t = re.sub(r"[{}\\&]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def speakable(md):
    """Markdown + LaTeX -> plain words for TTS (formulas read aloud, markup dropped)."""
    t = _MATH.sub(lambda m: " " + _math_to_words(next(g for g in m.groups() if g)) + " ", md)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*\|?\s*:?-{3,}.*$", "", t, flags=re.M)  # table separator rows
    t = t.replace("|", ", ")
    t = re.sub(r"\*\*|__|\*|~~", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[→⇒]", " gives ", t)
    return re.sub(r"\s+", " ", t).strip()


class SentenceSplitter:
    """Feed streamed text; get back complete sentences to speak (never cutting inside a $...$ formula)."""

    END = re.compile(r"(?<=[.!?:])\s+(?=[A-Z0-9*#(\[-]|$)|\n\s*\n|\n(?=\s*(?:[-*•]|\d+\.)\s)")
    CLAUSE = re.compile(r"(?<=[,;])\s+")
    MAX_CHARS = 140  # longer sentences are cut at a comma, so speech starts sooner

    def __init__(self, min_chars=40):
        self.buf, self.min_chars = "", min_chars

    def feed(self, delta):
        self.buf += delta
        out, start = [], 0
        for m in self.END.finditer(self.buf):
            piece = self.buf[start:m.start()]
            if len(piece.strip()) < self.min_chars or self._open_math(self.buf[:m.start()]):
                continue
            out.append(piece.strip())
            start = m.end()
        self.buf = self.buf[start:]
        if len(self.buf) > self.MAX_CHARS:  # no sentence end yet: cut at the last clause boundary outside math
            cuts = [m for m in self.CLAUSE.finditer(self.buf)
                    if m.start() >= self.min_chars and not self._open_math(self.buf[:m.start()])]
            if cuts:
                out.append(self.buf[:cuts[-1].start()].strip())
                self.buf = self.buf[cuts[-1].end():]
        return out

    def flush(self):
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []

    @staticmethod
    def _open_math(text):
        return text.count("$") % 2 == 1 or text.count("\\[") > text.count("\\]")
