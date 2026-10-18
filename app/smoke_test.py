"""Headless check of every component on the laptop (no windows). Run from %LOCALAPPDATA%\\OfflineTutor\\src:
    ..\\venv\\Scripts\\python.exe -m tutor_app_smoke  (setup copies this file as src\\tutor_app_smoke.py)
Optional: a WAV path as the first argument to test speech-to-text.
"""

import subprocess
import sys
import threading
import time
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from tutor.knowledge import KnowledgePack
from tutor_app import config, ocr
from tutor_app.audio import Transcriber
from tutor_app.engine import Conversation, LlamaServer


def gpu_mem():
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return "?"


def main():
    print("GPU before:", gpu_mem())
    srv = LlamaServer()
    t0 = time.time()
    srv.start()
    srv.ready.wait(300)
    if not srv.ready.is_set():
        print("SERVER FAILED:", srv.error)
        return 1
    print(f"llama-server ready in {time.time() - t0:.1f}s; GPU: {gpu_mem()}")

    # OCR on a rendered "slide"
    img = Image.new("RGB", (1280, 720), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(["Parallel Systems", "R = 1 - (1 - R1)(1 - R2)...(1 - Rm)",
                              "Product law of unreliabilities", "MTBF increases slowly with m"]):
        d.text((80, 80 + 90 * i), line, fill="black", font=font)
    for label in ("incl. load", "warm"):
        t0 = time.time()
        text = ocr.read_text(img)
        print(f"OCR ({label}, {time.time() - t0:.1f}s)")
    print(text + "\n")

    # speech-to-text
    if len(sys.argv) > 1:
        with wave.open(sys.argv[1]) as w:
            rate, raw = w.getframerate(), w.readframes(w.getnframes())
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
        if rate != 16000:  # simple resample for the test only
            audio = np.interp(np.arange(0, len(audio), rate / 16000), np.arange(len(audio)), audio).astype(np.float32)
        tr = Transcriber()
        for label in ("incl. load", "warm"):
            t0 = time.time()
            said = tr.transcribe(audio)
            print(f"ASR ({label}, {time.time() - t0:.1f}s for {len(audio) / 16000:.1f}s audio): {said!r}")

    # a full screen turn, Hinglish question, with the knowledge pack
    pack = KnowledgePack.load(config.PACK) if config.PACK.exists() else None
    conv = Conversation(pack=pack)
    msgs, turn = conv.build("yeh wala formula samjhao, aur 3 components ho to R kitna hoga agar har ek 0.9 hai?",
                            region=text.splitlines()[1] if text else None, full=text)
    t0, first = time.time(), [None]

    def on_delta(_):
        if first[0] is None:
            first[0] = time.time() - t0

    reply = srv.stream(msgs, on_delta, threading.Event())
    dt = time.time() - t0
    print(f"--- reply: first token {first[0]:.1f}s, total {dt:.1f}s, ~{len(reply) / 4 / max(dt - first[0], 1e-6):.0f} tok/s")
    print(reply[:1500])
    print("GPU during:", gpu_mem())
    srv.stop_server()

    # voice: sentence splitting of the streamed reply, speakable text, synthesis time (no playback)
    from tutor_app.speech_text import SentenceSplitter, speakable
    from tutor_app.tts import Speaker
    sp = SentenceSplitter()
    sentences = [s for i in range(0, len(reply), 7) for s in sp.feed(reply[i:i + 7])] + sp.flush()
    print(f"\n--- {len(sentences)} spoken sentences; first three as speech:")
    for s in sentences[:3]:
        print("  >", speakable(s))
    spk = Speaker()
    t0 = time.time()
    spk.warm_up()
    print(f"TTS load {time.time() - t0:.1f}s")
    t0 = time.time()
    audio = spk._get().generate(speakable(sentences[0]), sid=3, speed=1.0)
    dur = len(audio.samples) / audio.sample_rate
    print(f"TTS first sentence: {time.time() - t0:.2f}s to synthesize {dur:.1f}s of speech")
    return 0


if __name__ == "__main__":
    sys.exit(main())
