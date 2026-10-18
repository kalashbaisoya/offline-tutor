"""Shared helpers for the server pipeline: teacher server, status.json progress, JSON parsing."""

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tutor.llm import LlamaServer  # noqa: E402

TEACHER = ROOT / "models/Qwen3.5-27B-GGUF/Qwen3.5-27B-Q4_K_M.gguf"
MMPROJ = ROOT / "models/Qwen3.5-27B-GGUF/mmproj-F16.gguf"
PARALLEL = int(os.environ.get("PARALLEL", 4))  # concurrent requests; llama-server splits the context between slots

# Qwen3.5 recommended non-thinking sampling
SAMPLING = dict(temperature=0.7, top_p=0.8, top_k=20, presence_penalty=1.5)


def teacher(vision=False, ctx_per_slot=16384):
    # q8_0 KV cache (user's request, shared GPU): all layers stay on the GPU, KV memory roughly halves
    # (when the GPU must be shared: PARALLEL=1 and add "-ub", "256" -> ~6.7 GB free with vision, ~7.5 GB without)
    extra = ["-np", str(PARALLEL), "-ctk", "q8_0", "-ctv", "q8_0"] + (["--mmproj", str(MMPROJ)] if vision else [])
    return LlamaServer(TEACHER, ctx=ctx_per_slot * PARALLEL, port=8091, llama_bin=ROOT / "third_party/llama.cpp/build/bin",
                       extra_args=extra)


class Status:
    """Writes OUT/status.json after every unit of work so progress can be read without streaming logs."""

    def __init__(self, out_dir, step, total):
        self.path, self.step, self.total = Path(out_dir) / "status.json", step, total
        self.done, self.t0, self.extra = 0, time.time(), {}
        self.write()

    def tick(self, **extra):
        self.done += 1
        self.extra.update(extra)
        self.write()

    def write(self, state="running", error=None):
        rate = self.done / max(time.time() - self.t0, 1e-9)
        eta = round((self.total - self.done) / rate) if self.done else None
        d = dict(step=self.step, state=state, done=self.done, total=self.total, eta_s=eta,
                 time=time.strftime("%Y-%m-%dT%H:%M:%S"), **self.extra)
        if error:
            d["error"] = error
        self.path.write_text(json.dumps(d))


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)
