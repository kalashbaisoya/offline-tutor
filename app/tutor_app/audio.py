"""Microphone recording (only while an activation is open) and offline speech-to-text (Whisper-small, CPU)."""

import logging
import threading

import numpy as np
import sounddevice as sd

from . import config

log = logging.getLogger(__name__)
RATE = 16000


class Recorder:
    """Opens the mic on start() and closes it on stop(); nothing is recorded outside an activation."""

    def __init__(self):
        self.stream, self.chunks, self.level = None, [], 0.0

    def start(self):
        self.chunks, self.level = [], 0.0

        def cb(indata, frames, t, status):
            self.chunks.append(indata[:, 0].copy())
            self.level = float(np.sqrt(np.mean(indata[:, 0] ** 2)))

        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=cb)
        self.stream.start()

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)


class Transcriber:
    """Multilingual Whisper-small via sherpa-onnx; language auto-detected (English, Hindi, Hinglish speech)."""

    def __init__(self):
        self._rec = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._rec is None:
                import sherpa_onnx
                d = config.WHISPER_DIR
                self._rec = sherpa_onnx.OfflineRecognizer.from_whisper(
                    encoder=str(d / "small-encoder.int8.onnx"), decoder=str(d / "small-decoder.int8.onnx"),
                    tokens=str(d / "small-tokens.txt"), language="", task="transcribe", num_threads=4)
            return self._rec

    def warm_up(self):
        self._get()

    def transcribe(self, audio):
        """float32 16 kHz mono -> text. Whisper sees at most 30 s at a time, so longer audio is cut into windows."""
        if audio.size < RATE * 0.4 or float(np.sqrt(np.mean(audio ** 2))) < 0.003:  # < 0.4 s or near silence
            return ""
        rec, texts = self._get(), []
        for start in range(0, audio.size, RATE * 28):
            s = rec.create_stream()
            s.accept_waveform(RATE, audio[start:start + RATE * 28])
            rec.decode_stream(s)
            texts.append(s.result.text.strip())
        text = " ".join(t for t in texts if t)
        log.info("transcript (%.1fs audio): %s", audio.size / RATE, text)
        return text
