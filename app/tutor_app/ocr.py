"""CPU OCR (RapidOCR, ONNX) with reading-order reconstruction."""

import threading

import numpy as np

_engine = None
_init_lock = threading.Lock()  # creating the engine
_run_lock = threading.Lock()   # one OCR call at a time (separate lock: read_text holds it while calling _get)


def _get():
    global _engine
    with _init_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        return _engine


def warm_up():
    _get()


def read_text(img, min_score=0.5):
    """PIL image -> text in reading order (lines top-to-bottom, words left-to-right)."""
    if img.width < 400:  # small circled regions read better enlarged
        img = img.resize((img.width * 2, img.height * 2))
    engine = _get()
    with _run_lock:
        result, _ = engine(np.asarray(img.convert("RGB")))
    if not result:
        return ""
    boxes = []
    for box, text, score in result:
        if float(score) < min_score or not text.strip():
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append((min(ys), max(ys), min(xs), text.strip()))
    boxes.sort()
    lines, cur, cur_bottom = [], [], 0.0
    for top, bottom, left, text in boxes:
        if cur and top > cur_bottom - 0.5 * (bottom - top):  # starts below the current line's middle: new line
            lines.append(cur)
            cur, cur_bottom = [], 0.0
        cur.append((left, text))
        cur_bottom = max(cur_bottom, bottom)
    if cur:
        lines.append(cur)
    return "\n".join("  ".join(t for _, t in sorted(line)) for line in lines)
