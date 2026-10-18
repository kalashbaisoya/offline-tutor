"""Compare voices for spoken Hinglish (dev tool, runs on the laptop).

For each candidate voice: synthesize real Hinglish sentences from the model's answers, transcribe the audio back
with Whisper (Hindi), and compare to the Devanagari reference (character error rate, lower = more intelligible as
Hindi). Also reports synthesis speed and writes sample WAVs for listening.

Usage (from %LOCALAPPDATA%\\OfflineTutor\\src):
  ..\\venv\\Scripts\\python.exe voice_eval.py ANSWERS.json OUT_DIR [N_SENTENCES]
"""

import json
import random
import re
import sys
import time
import unicodedata
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

from tutor.context import detect_lang
from tutor_app import config
from tutor_app.speech_text import SentenceSplitter, speakable
from tutor_app.translit import Transliterator

M = config.HOME / "models"


def kokoro(dirname, lang=None, multi=False):
    d = M / dirname
    cfg = sherpa_onnx.OfflineTtsKokoroModelConfig(
        model=str(d / "model.onnx"), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
        data_dir=str(d / "espeak-ng-data"),
        **(dict(lexicon=f"{d / 'lexicon-us-en.txt'},{d / 'lexicon-zh.txt'}", dict_dir=str(d / "dict")) if multi else {}),
        **(dict(lang=lang) if lang else {}))
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(kokoro=cfg,
                                                                                                    num_threads=4)))


def piper(name):
    d = M / f"vits-piper-{name}"
    cfg = sherpa_onnx.OfflineTtsVitsModelConfig(model=str(d / f"{name}.onnx"), tokens=str(d / "tokens.txt"),
                                                data_dir=str(d / "espeak-ng-data"))
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(vits=cfg,
                                                                                                    num_threads=4)))


def normalize(t):
    t = unicodedata.normalize("NFC", t)
    t = t.replace("\u093c", "").replace("ँ", "ं")  # nukta and chandrabindu vary freely
    return re.sub(r"[^\u0900-\u097F]", "", t.replace("।", ""))


def cer(ref, hyp):
    r, h = normalize(ref), normalize(hyp)
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i]
        for j, hc in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / max(len(r), 1)


def save_wav(path, samples, rate):
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def main(answers_path, out_dir, n=30):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    answers = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    sentences = []
    for v in answers.values():
        sp = SentenceSplitter()
        for s in sp.feed(v["text"]) + sp.flush():
            words = speakable(s)
            if detect_lang(words) == "hinglish" and 40 <= len(words) <= 180 and not re.search(r"\\|\$", s):
                sentences.append(words)
    random.Random(0).shuffle(sentences)
    sentences = sentences[:int(n)]
    tr = Transliterator()
    refs = [tr(s) for s in sentences]
    (out / "sentences.json").write_text(json.dumps([dict(roman=s, devanagari=r) for s, r in zip(sentences, refs)],
                                                   ensure_ascii=False, indent=1), encoding="utf-8")

    wd = config.WHISPER_DIR
    asr = sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=str(wd / "small-encoder.int8.onnx"), decoder=str(wd / "small-decoder.int8.onnx"),
        tokens=str(wd / "small-tokens.txt"), language="hi", task="transcribe", num_threads=4)

    loaders = {"kokoro-en": lambda: kokoro("kokoro-en-v0_19"),
               "kokoro-multi": lambda: kokoro("kokoro-multi-lang-v1_0", "hi", True),
               "priyamvada": lambda: piper("hi_IN-priyamvada-medium"),
               "rohan": lambda: piper("hi_IN-rohan-medium"),
               "pratham": lambda: piper("hi_IN-pratham-medium")}
    candidates = [  # name, model, sid, input script
        ("current: Kokoro-en af_sarah (Roman)", "kokoro-en", 3, "roman"),
        ("Kokoro-1.0 hf_alpha", "kokoro-multi", 31, "deva"),
        ("Kokoro-1.0 hf_beta", "kokoro-multi", 32, "deva"),
        ("Kokoro-1.0 hm_omega", "kokoro-multi", 33, "deva"),
        ("Kokoro-1.0 hm_psi", "kokoro-multi", 34, "deva"),
        ("Piper hi_IN priyamvada", "priyamvada", 0, "deva"),
        ("Piper hi_IN rohan", "rohan", 0, "deva"),
        ("Piper hi_IN pratham", "pratham", 0, "deva"),
    ]
    rows, cache = [], {}
    for name, model, sid, inp in candidates:
        if model not in cache:
            cache[model] = loaders[model]()
        tts = cache[model]
        errs, synth_s, audio_s = [], 0.0, 0.0
        for i, (rom, dev) in enumerate(zip(sentences, refs)):
            t0 = time.time()
            a = tts.generate(rom if inp == "roman" else dev, sid=sid, speed=1.0)
            synth_s += time.time() - t0
            samples = np.asarray(a.samples, dtype=np.float32)
            audio_s += len(samples) / a.sample_rate
            if i < 3:
                save_wav(out / f"{re.sub(r'[^A-Za-z0-9]+', '_', name)}_{i}.wav", samples, a.sample_rate)
            mono16 = np.interp(np.arange(0, len(samples), a.sample_rate / 16000), np.arange(len(samples)), samples)
            s = asr.create_stream()
            s.accept_waveform(16000, mono16.astype(np.float32))
            asr.decode_stream(s)
            errs.append(cer(dev, s.result.text))
        rows.append((name, sum(errs) / len(errs), synth_s / audio_s))
        print(f"{name:<38} CER {rows[-1][1]:.3f}   synthesis {rows[-1][2]:.2f}x real time", flush=True)
    lines = ["| Voice | Hindi-ASR character error rate (lower = clearer Hindi) | Synthesis time / audio time |",
             "|---|---|---|"] + [f"| {n} | {c:.3f} | {r:.2f} |" for n, c, r in rows]
    (out / "voice_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main(*sys.argv[1:])
