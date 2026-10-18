"""Local LLM (llama-server child process) + conversation memory + knowledge pack + language rule."""

import http.client
import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field

from tutor.context import APP_SYSTEM, LANG_HINT, detect_lang, screen_turn
from tutor.knowledge import KnowledgePack, notes_block

from . import config

log = logging.getLogger(__name__)
CREATE_NO_WINDOW = 0x08000000


class LlamaServer:
    """Owns the llama-server.exe process: all layers on the GPU, 8k context, q8_0 KV cache, one request at a time."""

    def __init__(self):
        self.proc = None
        self.ready = threading.Event()
        self.error = None

    def start(self):
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [str(config.LLAMA_SERVER), "-m", str(config.MODEL), "-c", str(config.CTX), "-ngl", "99", "-fa", "on",
               "-ctk", "q8_0", "-ctv", "q8_0", "-np", "1", "--host", "127.0.0.1", "--port", str(config.PORT),
               "--jinja", "--no-webui"]
        logf = open(config.LOG_DIR / "llama-server.log", "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._wait, daemon=True).start()

    def _wait(self):
        t0 = time.time()
        while time.time() - t0 < 300:
            if self.proc.poll() is not None:
                self.error = f"llama-server exited (code {self.proc.returncode}); see {config.LOG_DIR / 'llama-server.log'}"
                log.error(self.error)
                return
            try:
                c = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=2)
                c.request("GET", "/health")
                if c.getresponse().status == 200:
                    log.info("llama-server ready in %.1fs", time.time() - t0)
                    self.ready.set()
                    return
            except OSError:
                pass
            time.sleep(0.5)
        self.error = "llama-server did not become ready within 5 minutes"

    def stream(self, messages, on_delta, stop):
        """POST a streaming chat request; on_delta(text) per chunk; returns the full reply."""
        body = json.dumps(dict(messages=messages, stream=True, max_tokens=config.MAX_REPLY_TOKENS,
                               chat_template_kwargs={"enable_thinking": False}, **config.SAMPLING))
        c = http.client.HTTPConnection("127.0.0.1", config.PORT, timeout=600)
        c.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
        r = c.getresponse()
        if r.status != 200:
            raise RuntimeError(f"llama-server HTTP {r.status}: {r.read()[:300]!r}")
        out = []
        for raw in r:
            if stop.is_set():
                c.close()  # the server cancels generation when the client disconnects
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
            if delta:
                out.append(delta)
                on_delta(delta)
        return "".join(out)

    def stop_server(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


@dataclass
class Turn:
    user_full: str      # as sent (screen blocks included)
    user_short: str     # compact form kept once the turn is older (screen text dropped)
    assistant: str = ""
    lang: str = "en"    # language the answer is requested in (decides the speaking voice)


@dataclass
class Conversation:
    """Rolling memory within the 8k context: recent turns in full, older ones compacted, oldest dropped."""

    pack: KnowledgePack | None = None
    turns: list = field(default_factory=list)
    last_lang: str = "en"

    def reply_language(self, question, setting):
        if setting == "english":
            return "en"
        if setting == "hinglish":
            return "hinglish"
        if question.strip():
            self.last_lang = detect_lang(question)
        return self.last_lang

    def build(self, question, region=None, full=None, setting="auto"):
        """Return (messages, turn) for a new student turn. region/full are OCR text from an activation."""
        region = (region or "").strip()[:config.REGION_CHARS] or None
        full = (full or "").strip()[:config.FULL_SCREEN_CHARS] or None
        if not question.strip():
            question = "Explain the part I circled." if region else "Explain what is on my screen."
        lang = self.reply_language(question, setting)
        user = screen_turn(question, region, full, full_label="full screen")
        short = (f"[shared screen: {(region or full or '')[:160]!r}]\n" if (region or full) else "") + question

        query = " ".join(x for x in (question, region or (full or "")[:400]) if x)
        notes = notes_block(self.pack.search(query, k=3)) if self.pack else ""
        current = (notes + "\n\n" if notes else "") + user + "\n\n" + LANG_HINT["hinglish" if lang == "hi" else lang]

        history, used = [], 0
        for i, t in enumerate(reversed(self.turns)):
            u = t.user_full if i == 0 else t.user_short  # only the latest turn keeps its screen text
            cost = len(u) + len(t.assistant)
            if used + cost > config.HISTORY_CHARS:
                break
            history = [{"role": "user", "content": u}, {"role": "assistant", "content": t.assistant}] + history
            used += cost
        msgs = [{"role": "system", "content": APP_SYSTEM}] + history + [{"role": "user", "content": current}]
        return msgs, Turn(user_full=user, user_short=short, lang="hinglish" if lang == "hi" else lang)

    def commit(self, turn, reply):
        turn.assistant = reply
        self.turns.append(turn)

    def clear(self):
        self.turns.clear()
