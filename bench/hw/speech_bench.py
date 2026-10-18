"""Phase 0 hardware probe: CPU latency of the local speech stack (sherpa-onnx).

TTS:  Kokoro int8 (en v0.19)          -> time to synthesize one tutor sentence, RTF
ASR:  Parakeet-TDT 0.6B v2 int8       -> time to transcribe one utterance after end-of-speech, RTF
Loop: TTS output is fed back into ASR, giving a rough round-trip WER sanity check
      (synthetic speech is far easier than real microphone audio; this is NOT an accuracy benchmark).

Usage: python bench/hw/speech_bench.py [speech_models_dir] [threads] [kokoro-int8|kokoro|piper]
"""

import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import sherpa_onnx

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "models/speech")
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
TTS_ENGINE = sys.argv[3] if len(sys.argv) > 3 else "kokoro-int8"
OUT = Path("bench/hw/results")

SENTENCES = [
    "Wait, I didn't understand the previous step.",
    "The probability of A given B equals the probability of A and B divided by the probability of B.",
    "Let's check your answer: if the events were independent, the joint probability would be zero point one two.",
    "Before I show the solution, what do you think the expected value of a fair six sided die is?",
]


def words(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=int)
    d[:, 0] = range(len(r) + 1)
    d[0, :] = range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r), len(h)] / max(1, len(r))


def tts_model_config():
    if TTS_ENGINE == "piper":
        d = ROOT / "vits-piper-en_US-lessac-medium"
        return sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(d / "en_US-lessac-medium.onnx"), tokens=str(d / "tokens.txt"),
                data_dir=str(d / "espeak-ng-data")),
            num_threads=THREADS)
    d = ROOT / ("kokoro-int8-en-v0_19" if TTS_ENGINE == "kokoro-int8" else "kokoro-en-v0_19")
    return sherpa_onnx.OfflineTtsModelConfig(
        kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(next(d.glob("model*.onnx"))), voices=str(d / "voices.bin"),
            tokens=str(d / "tokens.txt"), data_dir=str(d / "espeak-ng-data")),
        num_threads=THREADS)


def main():
    t0 = time.perf_counter()
    tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=tts_model_config()))
    tts_load = time.perf_counter() - t0

    pd = ROOT / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
    t0 = time.perf_counter()
    asr = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(pd / "encoder.int8.onnx"),
        decoder=str(pd / "decoder.int8.onnx"),
        joiner=str(pd / "joiner.int8.onnx"),
        tokens=str(pd / "tokens.txt"),
        model_type="nemo_transducer",
        num_threads=THREADS,
    )
    asr_load = time.perf_counter() - t0

    rows = []
    for rep in range(2):  # rep 0 = warm-up, reported separately
        for s in SENTENCES:
            t0 = time.perf_counter()
            audio = tts.generate(s, sid=0, speed=1.0)
            t_tts = time.perf_counter() - t0
            samples = np.asarray(audio.samples, dtype=np.float32)
            dur = len(samples) / audio.sample_rate

            # Parakeet expects 16 kHz; sherpa-onnx resamples internally when given another rate.
            t0 = time.perf_counter()
            st = asr.create_stream()
            st.accept_waveform(audio.sample_rate, samples)
            asr.decode_stream(st)
            hyp = st.result.text
            t_asr = time.perf_counter() - t0
            rows.append(
                dict(rep=rep, text=s, audio_s=round(dur, 2), tts_s=round(t_tts, 3), tts_rtf=round(t_tts / dur, 3),
                     asr_s=round(t_asr, 3), asr_rtf=round(t_asr / dur, 3), hyp=hyp, wer=round(wer(s, hyp), 3))
            )

    warm = [r for r in rows if r["rep"] == 1]
    summary = dict(
        tts_engine=TTS_ENGINE,
        threads=THREADS,
        tts_load_s=round(tts_load, 2),
        asr_load_s=round(asr_load, 2),
        tts_rtf_mean=round(float(np.mean([r["tts_rtf"] for r in warm])), 3),
        tts_first_sentence_s=warm[0]["tts_s"],
        tts_long_sentence_s=max(r["tts_s"] for r in warm),
        asr_rtf_mean=round(float(np.mean([r["asr_rtf"] for r in warm])), 3),
        asr_latency_max_s=max(r["asr_s"] for r in warm),
        roundtrip_wer_mean=round(float(np.mean([r["wer"] for r in warm])), 3),
    )
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"speech_{time.strftime('%Y%m%dT%H%M%S')}_{TTS_ENGINE}_t{THREADS}.json"
    out.write_text(json.dumps(dict(summary=summary, rows=rows), indent=2))
    print(json.dumps(summary, indent=2))
    for r in warm:
        print(f"{r['audio_s']:5.2f}s audio | tts {r['tts_s']:.2f}s | asr {r['asr_s']:.2f}s | wer {r['wer']:.2f} | {r['hyp']}")
    print("wrote", out)


if __name__ == "__main__":
    main()
