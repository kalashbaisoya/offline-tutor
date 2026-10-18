"""S6: LoRA fine-tune of Gemma-4-E4B on the tutor data (loss on the tutor's replies only).

Usage: python train/sft.py CONFIG.json
Config keys (defaults below): base, data (list of {path, weight}), out, max_len, lora_r, lora_alpha, lr, epochs,
batch_tokens, val_fraction, eval_every, save_every, load_4bit, seed.

- Memory: the 262k-token vocabulary makes full logits huge, so logits are computed only at reply positions.
- Resumable: the adapter, optimizer and step are saved every `save_every` steps under OUT/last/.
- Progress: OUT/status.json (step, loss, val_loss, tokens/s, ETA); curves in OUT/log.jsonl.
- The best adapter by validation loss is kept in OUT/best/ (early stopping by inspection of the curve).
"""

import json
import math
import random
import sys
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = dict(base="models/gemma-4-E4B-it", data=[], out="run/sft", max_len=2048, lora_r=64, lora_alpha=64,
                lora_dropout=0.05, lr=1e-4, epochs=3, batch_tokens=4096, val_fraction=0.05, eval_every=50,
                save_every=100, load_4bit=False, seed=0)
LOGIT_CHUNK = 1024
TARGETS =r"model\.language_model\.layers\.\d+\.(self_attn\.(q|k|v|o)_proj|mlp\.(gate|up|down)_proj)"


def encode(tok, msgs, max_len):
    """Token ids + labels where only the final (assistant) turn is learned."""
    prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
    full = tok.apply_chat_template(msgs, tokenize=False)
    assert full.startswith(prompt), "chat template is not prefix-stable"
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    ids = tok(full, add_special_tokens=False)["input_ids"]
    if len(ids) > max_len:
        return None
    return ids, [-100] * len(p_ids) + ids[len(p_ids):]


def load_data(cfg, tok, rng):
    examples = []
    for src in cfg["data"]:
        rows = [json.loads(l) for l in open(ROOT / src["path"], encoding="utf-8") if l.strip()]
        w = src.get("weight", 1.0)  # weight > 1 repeats rows, < 1 subsamples
        rows = rows * int(w) + rng.sample(rows, round(len(rows) * (w - int(w))))
        for r in rows:
            enc = encode(tok, r["messages"], cfg["max_len"])
            if enc:
                examples.append(enc)
    rng.shuffle(examples)
    n_val = max(1, round(len(examples) * cfg["val_fraction"]))
    return examples[n_val:], examples[:n_val]


def batches(examples, batch_tokens, rng):
    """Length-bucketed batches of at most batch_tokens padded tokens."""
    order = sorted(range(len(examples)), key=lambda i: len(examples[i][0]))
    out, cur = [], []
    for i in order:
        longest = max([len(examples[j][0]) for j in cur] + [len(examples[i][0])])
        if cur and longest * (len(cur) + 1) > batch_tokens:
            out.append(cur)
            cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    rng.shuffle(out)
    return out


def collate(examples, idx, pad_id, device):
    L = max(len(examples[i][0]) for i in idx)
    ids = torch.full((len(idx), L), pad_id, dtype=torch.long)
    labels = torch.full((len(idx), L), -100, dtype=torch.long)
    mask = torch.zeros((len(idx), L), dtype=torch.long)
    for row, i in enumerate(idx):
        x, y = examples[i]
        ids[row, :len(x)], labels[row, :len(y)], mask[row, :len(x)] = torch.tensor(x), torch.tensor(y), 1
    return ids.to(device), labels.to(device), mask.to(device)


def loss_fn(model, ids, labels, mask, softcap):
    gemma = model.base_model.model  # Gemma4ForConditionalGeneration under the PEFT wrapper
    hidden = gemma.model(input_ids=ids, attention_mask=mask, token_type_ids=torch.zeros_like(ids)).last_hidden_state
    target = labels[:, 1:]
    keep = target != -100
    h, y = hidden[:, :-1][keep], target[keep]

    def chunk_loss(h_c, y_c):
        logits = gemma.lm_head(h_c).float()
        if softcap:
            logits = torch.tanh(logits / softcap) * softcap
        return F.cross_entropy(logits, y_c, reduction="sum")

    # 262k-entry vocabulary: never hold more than LOGIT_CHUNK rows of logits; recompute them in backward
    total = sum(checkpoint(chunk_loss, h[i:i + LOGIT_CHUNK], y[i:i + LOGIT_CHUNK], use_reentrant=False)
                for i in range(0, len(y), LOGIT_CHUNK))
    return total, keep.sum()


@torch.no_grad()
def evaluate(model, val, pad_id, softcap, batch_tokens, device):
    model.eval()
    tot, n = 0.0, 0
    for idx in batches(val, batch_tokens, random.Random(0)):
        loss, k = loss_fn(model, *collate(val, idx, pad_id, device), softcap)
        tot, n = tot + loss.item(), n + k.item()
    model.train()
    return tot / max(n, 1)


def main(config_path):
    cfg = {**DEFAULTS, **json.loads(Path(config_path).read_text())}
    out = ROOT / cfg["out"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(cfg, indent=1))
    rng = random.Random(cfg["seed"])
    torch.manual_seed(cfg["seed"])
    device = "cuda"

    tok = AutoTokenizer.from_pretrained(ROOT / cfg["base"])
    train, val = load_data(cfg, tok, rng)
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
                               bnb_4bit_use_double_quant=True) if cfg["load_4bit"] else None
    load = dict(dtype=torch.bfloat16, device_map={"": 0}, quantization_config=quant)
    try:  # SDPA for the language model; the audio tower (unused here) only supports eager attention
        base = AutoModelForCausalLM.from_pretrained(ROOT / cfg["base"], **load, attn_implementation={
            "text_config": "sdpa", "vision_config": "sdpa", "audio_config": "eager"})
    except (ValueError, TypeError) as e:
        print("per-tower attention not accepted, using eager everywhere:", e, flush=True)
        base = AutoModelForCausalLM.from_pretrained(ROOT / cfg["base"], **load, attn_implementation="eager")
    # Text-only training: free the audio/vision towers (~0.9 GB). export_gguf.sh merges the adapter into the full
    # base checkpoint, so the exported model keeps them.
    for tower in ("audio_tower", "vision_tower", "embed_audio", "embed_vision"):
        if hasattr(base.model, tower):
            setattr(base.model, tower, None)
    torch.cuda.empty_cache()
    base.config.use_cache = False
    base.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    base.enable_input_require_grads()
    softcap = base.config.get_text_config().final_logit_softcapping
    pad_id = tok.pad_token_id if tok.pad_token_id is not None else 0

    last = out / "last"
    if (last / "adapter_config.json").exists():
        model = PeftModel.from_pretrained(base, last, is_trainable=True)
        state = json.loads((last / "state.json").read_text())
    else:
        model = get_peft_model(base, LoraConfig(r=cfg["lora_r"], lora_alpha=cfg["lora_alpha"],
                                                lora_dropout=cfg["lora_dropout"], target_modules=TARGETS,
                                                task_type="CAUSAL_LM"))
        state = dict(step=0, best_val=None)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=cfg["lr"], weight_decay=0.0, betas=(0.9, 0.99))
    if (last / "optimizer.pt").exists():
        opt.load_state_dict(torch.load(last / "optimizer.pt"))

    plan = [b for _ in range(cfg["epochs"]) for b in batches(train, cfg["batch_tokens"], rng)]
    total, warmup = len(plan), max(1, len(plan) // 20)
    lr_at = lambda s: cfg["lr"] * (s / warmup if s < warmup else 0.5 * (1 + math.cos(math.pi * (s - warmup) / max(1, total - warmup))))
    n_trainable = sum(p.numel() for p in params)
    print(f"train {len(train)}  val {len(val)}  steps {total}  trainable {n_trainable / 1e6:.1f}M", flush=True)

    def save(dirname):
        d = out / dirname
        model.save_pretrained(d)
        (d / "state.json").write_text(json.dumps(state))
        if dirname == "last":
            torch.save(opt.state_dict(), d / "optimizer.pt")

    def status(**kw):
        (out / "status.json").write_text(json.dumps(dict(state="running", step=state["step"], total=total, **kw,
                                                         best_val=state["best_val"], time=time.strftime("%H:%M:%S"))))

    t0, toks, run_loss, start_step = time.time(), 0, None, state["step"]
    model.train()
    for step in range(state["step"], total):
        ids, labels, mask = collate(train, plan[step], pad_id, device)
        for g in opt.param_groups:
            g["lr"] = lr_at(step)
        loss, k = loss_fn(model, ids, labels, mask, softcap)
        (loss / k).backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        opt.zero_grad(set_to_none=True)
        state["step"] = step + 1
        toks += int(mask.sum())
        cur = (loss / k).item()
        run_loss = cur if run_loss is None else 0.95 * run_loss + 0.05 * cur
        rate = toks / (time.time() - t0)
        rec = dict(step=step + 1, loss=round(cur, 4), loss_ema=round(run_loss, 4), lr=lr_at(step))
        if (step + 1) % cfg["eval_every"] == 0 or step + 1 == total:
            rec["val_loss"] = round(evaluate(model, val, pad_id, softcap, cfg["batch_tokens"], device), 4)
            if state["best_val"] is None or rec["val_loss"] < state["best_val"]:
                state["best_val"] = rec["val_loss"]
                save("best")
        with open(out / "log.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        if (step + 1) % cfg["save_every"] == 0:
            save("last")
        status(loss_ema=rec["loss_ema"], val_loss=rec.get("val_loss"), tok_s=round(rate),
               eta_s=round((total - step - 1) * (time.time() - t0) / (step + 1 - start_step)),
               mem_gb=round(torch.cuda.max_memory_allocated() / 1e9, 1))
    save("last")
    (out / "status.json").write_text(json.dumps(dict(state="done", step=state["step"], total=total,
                                                     best_val=state["best_val"])))


if __name__ == "__main__":
    main(sys.argv[1])
