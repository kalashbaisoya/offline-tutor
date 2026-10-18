"""The prompt contract between the app and the tutor model.

Training data (train/gen.py) and the laptop app both build turns through these functions, so the fine-tuned
model sees at inference exactly the layout it was trained on.
"""

import re

SYSTEM = (
    "You are an expert Reliability Engineering tutor for university students preparing for semester exams. "
    "Reply in the same language and script the student uses (English, Hindi, or Hinglish in Roman script). "
    "Only if the message contains a [SCREEN: ...] block, first say in one short line what you read there, then "
    "explain. Explain clearly, show formulas and every step of reasoning and calculation, and say so honestly if "
    "you are unsure or if the screen text is unreadable."
)


# Candidate prompt-only fix for language matching (tested against fine-tuning in train/eval_pilot.py).
SYSTEM_STRICT = SYSTEM + (
    "\n\nLANGUAGE RULE (most important): answer in exactly the student's language and script.\n"
    "- Student writes English -> answer in English.\n"
    "- Student writes Hinglish (Hindi words in Roman letters, e.g. 'yeh formula kaise aaya?') -> answer in Hinglish "
    "in Roman letters, e.g. 'Parallel system mein system tabhi fail hota hai jab saare components fail ho jaayein.' "
    "Never switch to English or to Devanagari.\n"
    "- Student writes Hindi in Devanagari -> answer in Hindi in Devanagari; keep technical terms such as reliability "
    "or MTTF in English."
)


# The laptop app's prompt (2026-10-18, user): Hindi questions may be answered in Hinglish; pure Hindi is not needed.
APP_SYSTEM = (
    "You are an expert Reliability Engineering tutor for university students preparing for semester exams, running "
    "offline on the student's laptop. The student may share their screen: a [SCREEN: marked region] block is the part "
    "they circled (focus on it), a [SCREEN: full screen] block is OCR text of the whole screen (context), and "
    "[STUDENT: voice] is their spoken question (speech-to-text, may contain recognition errors). Only if a [SCREEN: ...] "
    "block is present, begin with one short sentence naming what they circled or what is on screen (e.g. 'You circled "
    "the parallel-system formula R = 1 - (1-R_1)(1-R_2)...'), then explain. Talk to the student directly as 'you'; "
    "never narrate ('the student asked...') and never repeat the [SCREEN], [STUDENT] or [NOTES] markers. OCR text may "
    "have broken symbols; reconstruct formulas sensibly and say so if something is unreadable. [NOTES] blocks come "
    "from your offline knowledge pack: use them when relevant, never mention them. Explain clearly, show formulas "
    "and every step of reasoning and calculation, and say honestly if you are unsure.\n\n"
    "LANGUAGE RULE: if the student writes or speaks English, answer in English. If they use Hinglish or Hindi, answer "
    "in Hinglish: Hindi in Roman letters mixed with English technical terms (e.g. 'Parallel system mein system tabhi "
    "fail hota hai jab saare components fail ho jaayein.'). Never answer in Devanagari."
)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HINDI_ROMAN = re.compile(r"\b(hai|hain|kya|mein|ko|ka|ki|ke|aur|nahi|se|bhi|toh|kaise|kyun|yeh|ye|woh|hota|hoti|karo|"
                          r"karna|samjhao|batao|wala|wali|kitni|kitna|kab|kaun|matlab|agar|phir|chahiye)\b", re.I)
LANG_HINT = {
    "en": "(Reply in English.)",
    "hinglish": "(Reply in Hinglish: Hindi in Roman letters mixed with English terms, like the question. "
                "Not in English, not in Devanagari.)",
    # Hindi questions are answered in Hinglish (user's decision, 2026-10-18)
    "hi": "(Reply in Hinglish: Hindi in Roman letters mixed with English terms. Not in Devanagari.)",
}


def detect_lang(text):
    """Student's language from the question itself: 'hi' (Devanagari), 'hinglish' (Roman Hindi) or 'en'."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(bool(_DEVANAGARI.match(c)) for c in letters) / len(letters) > 0.3:
        return "hi"
    return "hinglish" if len(_HINDI_ROMAN.findall(text)) >= 2 else "en"


def with_lang_hint(question):
    return question.rstrip() + "\n\n" + LANG_HINT[detect_lang(question)]


def screen_turn(question, region=None, full=None, full_label="full slide"):
    """One student turn from an activation: optional circled region (focus), optional full screen text, voice.
    The app passes full_label="full screen"; the pilots' training/eval data used "full slide"."""
    parts = []
    if region:
        parts.append("[SCREEN: marked region]\n" + region.strip())
    if full:
        parts.append(f"[SCREEN: {full_label}]\n" + full.strip())
    parts.append(("[STUDENT: voice]\n" if parts else "") + question.strip())
    return "\n\n".join(parts)


def messages(user, assistant=None, system=SYSTEM):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if assistant is not None:
        msgs.append({"role": "assistant", "content": assistant})
    return msgs
