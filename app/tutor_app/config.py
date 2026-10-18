"""Paths and settings. Everything lives under %LOCALAPPDATA%\\OfflineTutor (created by app/setup_windows.ps1)."""

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("OFFLINE_TUTOR_HOME") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OfflineTutor")
LLAMA_SERVER = HOME / "bin" / "llama" / "llama-server.exe"
MODEL = HOME / "models" / "gemma-base-Q4_K_M.gguf"
PACK = HOME / "models" / "pack.jsonl"
WHISPER_DIR = HOME / "models" / "sherpa-onnx-whisper-small"
KOKORO_DIR = HOME / "models" / "kokoro-en-v0_19"
HINGLISH_LEXICON = HOME / "models" / "hinglish_lexicon.json"   # Roman Hinglish -> Devanagari pronunciation
KOKORO_MULTI_DIR = HOME / "models" / "kokoro-multi-lang-v1_0"  # Hindi speakers 31-34
LOG_DIR = HOME / "logs"
SETTINGS_FILE = HOME / "settings.json"
ASSETS = Path(__file__).parent / "assets"

PORT = 8765
CTX = 8192                 # measured: Gemma-4-E4B Q4_K_M at 8k fits the 4 GB laptop GPU (~3.3 GB)
MAX_REPLY_TOKENS = 1500
SAMPLING = dict(temperature=0.7, top_p=0.95, top_k=64)

# Character budgets for one prompt (~3.5 chars/token): system ~1.4k, notes 4.5k, screen 4.5k, history 7k, reply 1500 tok
REGION_CHARS = 1500
FULL_SCREEN_CHARS = 3000
HISTORY_CHARS = 7000

DEFAULTS = {
    "hotkey": "<ctrl>+<shift>+<space>",   # pynput format; "" disables the hotkey (the circle always works)
    "answer_language": "auto",           # auto | english | hinglish
    "speak": True,                       # read answers aloud (the chat window then stays closed)
    "show_chat": False,                  # also open the chat window with every answer
    "voice": 3,                          # English answers: Kokoro v0.19 speaker id (see VOICES)
    # Hinglish answers: see HINDI_VOICES ("" = English voice). Piper priyamvada: best Hindi-ASR round-trip score
    # in app/voice_eval.py and ~9x faster than Kokoro (0.05x vs 0.34-0.46x real time on the laptop CPU)
    "hindi_voice": "piper:hi_IN-priyamvada-medium",
    "speed": 1.0,
    "bubble_pos": None,
}

# Kokoro en v0.19 speakers (sherpa-onnx sid)
VOICES = {0: "af (US, female)", 1: "af_bella (US, female)", 2: "af_nicole (US, female)", 3: "af_sarah (US, female)",
          4: "af_sky (US, female)", 5: "am_adam (US, male)", 6: "am_michael (US, male)", 7: "bf_emma (UK, female)",
          8: "bf_isabella (UK, female)", 9: "bm_george (UK, male)", 10: "bm_lewis (UK, male)"}

# Voices for Hinglish answers (engine#sid); text is transliterated to Devanagari first
HINDI_VOICES = {"kokoro#31": "Kokoro hf_alpha (female)", "kokoro#32": "Kokoro hf_beta (female)",
                "kokoro#33": "Kokoro hm_omega (male)", "kokoro#34": "Kokoro hm_psi (male)",
                "piper:hi_IN-priyamvada-medium": "Piper priyamvada (female, fast)",
                "piper:hi_IN-rohan-medium": "Piper rohan (male, fast)",
                "piper:hi_IN-pratham-medium": "Piper pratham (male, fast)",
                "": "English voice (old behaviour)"}


def load_settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(settings):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=1), encoding="utf-8")
    except OSError:
        pass
