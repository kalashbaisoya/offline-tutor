"""Minimal client for a local llama-server process (OpenAI-compatible chat endpoint)."""

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

DEFAULT_LLAMA_BIN = Path(os.environ.get("LLAMA_BIN", "third_party/llama.cpp/build/bin"))


class LlamaServer:
    """Starts llama-server for one GGUF model; use as a context manager."""

    def __init__(self, model, ctx=8192, ngl=99, port=8090, llama_bin=DEFAULT_LLAMA_BIN, extra_args=()):
        self.model, self.ctx, self.ngl, self.port = Path(model), ctx, ngl, port
        self.llama_bin, self.extra_args = Path(llama_bin), list(extra_args)
        self.proc = None

    def __enter__(self):
        cmd = [str(self.llama_bin / "llama-server"), "-m", str(self.model), "-c", str(self.ctx), "-ngl", str(self.ngl),
               "-fa", "on", "--port", str(self.port), "--no-webui", "--jinja", *self.extra_args]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        return self
            except Exception:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited while loading {self.model.name}")
            if time.time() - t0 > 300:
                raise TimeoutError(f"llama-server did not become ready for {self.model.name}")
            time.sleep(0.5)

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=30)

    def chat(self, messages, max_tokens=1200, thinking=False, **sampling):
        # Qwen3.5 / Gemma 4 templates can enable a hidden reasoning channel; the tutor answers directly by default.
        body = dict(messages=messages, max_tokens=max_tokens, stream=False,
                    chat_template_kwargs={"enable_thinking": thinking}, **sampling)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.load(r)
        msg = d["choices"][0]["message"]
        return msg.get("content") or "", dict(seconds=round(time.time() - t0, 2), usage=d.get("usage"),
                                              reasoning_chars=len(msg.get("reasoning_content") or ""),
                                              finish=d["choices"][0].get("finish_reason"))
