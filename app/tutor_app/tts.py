"""Speaking the answers, sentence by sentence while the model is still writing.

English answers: Kokoro en v0.19. Hinglish answers: a Hindi voice reading Devanagari (translit.py), so Hindi words
sound Hindi and English terms get an Indian-English accent. All on the CPU via sherpa-onnx.

Two threads: synthesis (text -> audio) and playback, so the next sentence is synthesized while the current one
plays. stop() cancels everything at once (new question, or the student clicks the circle).
"""

import logging
import queue
import threading

import numpy as np
import sounddevice as sd

from . import config
from .speech_text import speakable
from .translit import Transliterator

log = logging.getLogger(__name__)


def _kokoro_en():
    import sherpa_onnx
    d = config.KOKORO_DIR
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
        kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(next(d.glob("model*.onnx"))), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
            data_dir=str(d / "espeak-ng-data")),
        num_threads=4)))


def _hindi(engine):
    """engine: 'kokoro' (Kokoro v1.0 multi-lang, Hindi speakers 31-34) or 'piper:<voice>' (Piper hi_IN)."""
    import sherpa_onnx
    if engine == "kokoro":
        d = config.KOKORO_MULTI_DIR
        model = sherpa_onnx.OfflineTtsModelConfig(kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(d / "model.onnx"), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
            data_dir=str(d / "espeak-ng-data"), dict_dir=str(d / "dict"),
            lexicon=f"{d / 'lexicon-us-en.txt'},{d / 'lexicon-zh.txt'}", lang="hi"), num_threads=4)
    else:
        name = engine.split(":", 1)[1]
        d = config.HOME / "models" / f"vits-piper-{name}"
        model = sherpa_onnx.OfflineTtsModelConfig(vits=sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(d / f"{name}.onnx"), tokens=str(d / "tokens.txt"), data_dir=str(d / "espeak-ng-data")),
            num_threads=4)
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model))


class Speaker:
    def __init__(self, on_playing=None):
        self.on_playing = on_playing or (lambda playing: None)  # called from worker threads
        self.voice, self.speed = 0, 1.0              # English voice (Kokoro en sid)
        self.hindi_voice = config.DEFAULTS["hindi_voice"]
        self._engines, self._lock = {}, threading.Lock()
        self._translit = None
        self._text_q, self._audio_q = queue.Queue(), queue.Queue()
        self._gen = 0              # bumped by stop(): queued items from older answers are dropped
        self._pending = 0          # sentences queued but not yet played
        self._count_lock = threading.Lock()
        self.playing = False
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def _engine(self, key):
        with self._lock:
            if key not in self._engines:
                self._engines[key] = _kokoro_en() if key == "en" else _hindi(key)
            return self._engines[key]

    def _voice_for(self, lang):
        """(engine key, sid, text transform) for an answer language."""
        if lang in ("hinglish", "hi") and self.hindi_voice:
            engine, _, sid = self.hindi_voice.partition("#")
            if self._translit is None:
                self._translit = Transliterator()
            return engine, int(sid or 0), self._translit
        return "en", self.voice, None

    def warm_up(self):
        for lang in ("en", "hinglish"):
            key, sid, tr = self._voice_for(lang)
            self._engine(key).generate(tr("theek hai") if tr else "Ready.", sid=sid, speed=self.speed)

    @property
    def busy(self):
        return self._pending > 0

    def say(self, text, lang="en"):
        with self._count_lock:
            self._pending += 1
        self._text_q.put((self._gen, text, lang))

    def stop(self):
        with self._count_lock:
            self._gen += 1
            self._pending = 0
        for q in (self._text_q, self._audio_q):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        sd.stop()
        self._set_playing(False)

    def _done_one(self, gen):
        with self._count_lock:
            if gen == self._gen and self._pending > 0:
                self._pending -= 1
            idle = self._pending == 0
        if idle:
            self._set_playing(False)

    def _set_playing(self, playing):
        if playing != self.playing:
            self.playing = playing
            self.on_playing(playing)

    def _synth_loop(self):
        while True:
            gen, text, lang = self._text_q.get()
            if gen != self._gen:
                continue
            words = speakable(text)
            try:
                key, sid, tr = self._voice_for(lang)
                words = tr(words) if tr else words
                if not words.strip():
                    self._done_one(gen)
                    continue
                audio = self._engine(key).generate(words, sid=sid, speed=self.speed)
                self._audio_q.put((gen, np.asarray(audio.samples, dtype=np.float32), audio.sample_rate))
            except Exception:
                log.exception("TTS failed for %r", words[:80])
                self._done_one(gen)

    def _play_loop(self):
        while True:
            gen, samples, rate = self._audio_q.get()
            if gen != self._gen:
                continue
            self._set_playing(True)
            try:
                sd.play(samples, rate)
                sd.wait()  # returns early when stop() calls sd.stop()
            except Exception:
                log.exception("audio playback failed")
            self._done_one(gen)
