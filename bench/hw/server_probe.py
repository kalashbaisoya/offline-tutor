"""Phase 0 probe: real VRAM footprint and time-to-first-token through llama-server.

Starts llama-server for one model at a fixed context size, sends a RAG-sized prompt
(~3k tokens of textbook-like filler + a question), streams the answer, and records:
  - VRAM used after model load and after the request (nvidia-smi)
  - TTFT (client-side, first streamed token), prefill and decode tok/s (server timings)

Usage: python bench/hw/server_probe.py LLAMA_BIN MODEL.gguf [ctx] [ngl]
"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LLAMA_BIN, MODEL = Path(sys.argv[1]), Path(sys.argv[2])
CTX = int(sys.argv[3]) if len(sys.argv) > 3 else 8192
NGL = sys.argv[4] if len(sys.argv) > 4 else "99"
PORT = 8089
OUT = Path("bench/hw/results")

PASSAGE = (
    "Conditional probability. Let A and B be events with P(B) > 0. The conditional probability of A given B "
    "is defined as P(A|B) = P(A and B) / P(B). Two events are independent when P(A and B) = P(A)P(B), which "
    "is equivalent to P(A|B) = P(A). Mutually exclusive events with positive probability are never independent. "
)


def vram_mib():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


def post(path, body, stream=False):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=600)


def main():
    base = vram_mib()
    proc = subprocess.Popen(
        [str(LLAMA_BIN / "llama-server"), "-m", str(MODEL), "-c", str(CTX), "-ngl", NGL, "-fa", "on",
         "--port", str(PORT), "-t", "8", "--no-webui"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t0 = time.perf_counter()
        while True:
            try:
                if json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)).get("status") == "ok":
                    break
            except Exception:
                pass
            if proc.poll() is not None or time.perf_counter() - t0 > 300:
                raise SystemExit("server failed to start")
            time.sleep(0.5)
        load_s = time.perf_counter() - t0
        loaded = vram_mib()

        results = []
        for rep in range(3):  # rep 0 warm-up; vary prefix so the prompt cache cannot be reused
            prompt = f"[doc {rep}] " + PASSAGE * 48 + "\nQuestion: explain, in two sentences, why mutually exclusive events are not independent.\nAnswer:"
            t_send = time.perf_counter()
            ttft, timings = None, {}
            resp = post("/completion", dict(prompt=prompt, n_predict=96, temperature=0, stream=True, cache_prompt=False))
            for raw in resp:
                line = raw.decode().strip()
                if not line.startswith("data:"):
                    continue
                d = json.loads(line[5:])
                if ttft is None and d.get("content"):
                    ttft = time.perf_counter() - t_send
                if d.get("stop"):
                    timings = d.get("timings", {})
            results.append(dict(rep=rep, ttft_s=round(ttft, 3), prompt_n=timings.get("prompt_n"),
                                prefill_tps=round(timings.get("prompt_per_second", 0), 1),
                                decode_tps=round(timings.get("predicted_per_second", 0), 1)))
        after = vram_mib()
    finally:
        proc.terminate()
        proc.wait(timeout=30)

    summary = dict(model=MODEL.name, ctx=CTX, ngl=NGL, load_s=round(load_s, 1), vram_baseline_mib=base,
                   vram_loaded_mib=loaded, vram_after_request_mib=after, runs=results)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "server_probe.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
