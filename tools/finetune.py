#!/usr/bin/env python3
"""Trains the meaning matcher's model (all-MiniLM-L6-v2) on JARVIS's own wordings, on the Jetson's GPU, and
says whether it does better than the model as downloaded.

  tools/finetune.py              # train, save to models/minilm-tuned, score both on the held-out sentences
  tools/finetune.py --epochs 4   # (default 3)

A small model scores by topic, not action: "turn it up" sits as close to "volume down" as to "volume up". This
pulls each phrase's wordings together and pushes apart the ones it mixes up: the other way round ("volume up" /
"volume down"), what it's been told is wrong (learned.yaml `wrong:`), "leave it" rows, and chat that sits near a
phrase ("my day was great" by "how's my day going"). Besides JARVIS's own wordings it learns from tests/phrases.yaml
(what goes to Claude, and saved commands' other wordings) and from MASSIVE's train split (models/minilm-base/
massive-train.jsonl; tools/massive.py says what each intent is). The result is the same size and speed (int8 ONNX,
~23 MB).

It only ever writes ~/.local/share/jarvis/models/minilm-tuned. To use it, set intents.model: minilm-tuned in
config/settings.yaml (and the threshold this prints) and restart JARVIS. Held-out sentences (tests/heldout.yaml)
are never trained on.

Needs PyTorch (already on the Jetson) and the original weights in models/minilm-base (pytorch_model.bin from
sentence-transformers/all-MiniLM-L6-v2 on Hugging Face, loaded with weights_only, so it can't run code).
"""
import argparse
import math
import os
import random
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import importlib.util  # noqa: E402

import numpy as np  # noqa: E402
import torch  # noqa: E402
import yaml  # noqa: E402
from torch import nn  # noqa: E402

from core import heldout, learned  # noqa: E402
from core.intent import LEAVE, MODEL_DIR, MODELS, IntentMatcher, WordPiece, _norm  # noqa: E402
from core.registry import Registry  # noqa: E402

BASE = os.path.join(MODELS, "minilm-base")
OUT = os.path.join(MODELS, "minilm-tuned")
CONFIG = os.path.join(HERE, "config")
MASSIVE_TRAIN = os.path.join(BASE, "massive-train.jsonl")
HARD = 0.55  # an outside sentence this near one of JARVIS's wordings is worth training against
_spec = importlib.util.spec_from_file_location("massive", os.path.join(HERE, "tools", "massive.py"))
massive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(massive)


class Bert(nn.Module):
    """BERT as all-MiniLM-L6-v2 has it (6 layers, 384 wide, 12 heads), giving every token's vector."""

    def __init__(self, vocab=30522, width=384, layers=6, heads=12, inner=1536, positions=512, drop=0.1):
        super().__init__()
        self.heads = heads
        self.words, self.positions, self.types = nn.Embedding(vocab, width), nn.Embedding(positions, width), \
            nn.Embedding(2, width)
        self.norm = nn.LayerNorm(width, eps=1e-12)
        self.drop = nn.Dropout(drop)
        self.layers = nn.ModuleList(nn.ModuleDict({
            "q": nn.Linear(width, width), "k": nn.Linear(width, width), "v": nn.Linear(width, width),
            "o": nn.Linear(width, width), "norm1": nn.LayerNorm(width, eps=1e-12),
            "up": nn.Linear(width, inner), "down": nn.Linear(inner, width), "norm2": nn.LayerNorm(width, eps=1e-12),
        }) for _ in range(layers))

    def forward(self, input_ids, attention_mask, token_type_ids):
        n, length = input_ids.shape
        pos = torch.arange(length, device=input_ids.device).unsqueeze(0)
        x = self.drop(self.norm(self.words(input_ids) + self.positions(pos) + self.types(token_type_ids)))
        bias = (1.0 - attention_mask[:, None, None, :].to(x.dtype)) * -10000.0
        split = lambda t: t.view(n, length, self.heads, -1).transpose(1, 2)  # noqa: E731
        for layer in self.layers:
            q, k, v = split(layer["q"](x)), split(layer["k"](x)), split(layer["v"](x))
            att = self.drop(torch.softmax(q @ k.transpose(-1, -2) / math.sqrt(q.shape[-1]) + bias, dim=-1))
            ctx = (att @ v).transpose(1, 2).reshape(n, length, -1)
            x = layer["norm1"](x + self.drop(layer["o"](ctx)))
            x = layer["norm2"](x + self.drop(layer["down"](nn.functional.gelu(layer["up"](x)))))
        return x

    def load(self, path):
        """The Hugging Face BertModel weights, renamed onto this."""
        raw = torch.load(path, map_location="cpu", weights_only=True)
        raw = {k[5:] if k.startswith("bert.") else k: v for k, v in raw.items()}
        names = {"words.weight": "embeddings.word_embeddings.weight",
                 "positions.weight": "embeddings.position_embeddings.weight",
                 "types.weight": "embeddings.token_type_embeddings.weight",
                 "norm.weight": "embeddings.LayerNorm.weight", "norm.bias": "embeddings.LayerNorm.bias"}
        parts = {"q": "attention.self.query", "k": "attention.self.key", "v": "attention.self.value",
                 "o": "attention.output.dense", "norm1": "attention.output.LayerNorm", "up": "intermediate.dense",
                 "down": "output.dense", "norm2": "output.LayerNorm"}
        for i in range(len(self.layers)):
            for mine, theirs in parts.items():
                for w in ("weight", "bias"):
                    names["layers.%d.%s.%s" % (i, mine, w)] = "encoder.layer.%d.%s.%s" % (i, theirs, w)
        self.load_state_dict({mine: raw[theirs] for mine, theirs in names.items()})
        return self


def pooled(model, tok, sentences, device):
    """Mean of the token vectors, normalised: what core/intent.py does with the ONNX model's output."""
    ids = [tok.encode(s) for s in sentences]
    length = max(len(i) for i in ids)
    input_ids = torch.tensor([i + [0] * (length - len(i)) for i in ids], device=device)
    mask = torch.tensor([[1] * len(i) + [0] * (length - len(i)) for i in ids], device=device)
    out = model(input_ids, mask, torch.zeros_like(input_ids))
    vec = (out * mask[..., None]).sum(1) / mask.sum(1, keepdim=True)
    return nn.functional.normalize(vec, dim=-1)


def matcher(model=None, threshold=0.72):
    """The matcher as JARVIS builds it: intents.yaml, the commands' and apps' wordings, what it has learned."""
    import jarvis as J
    from system.apps import Apps
    data = learned.load()
    return IntentMatcher(os.path.join(CONFIG, "intents.yaml"), threshold,
                         extra=dict(Apps().intents(), **J.command_phrases(Registry(os.path.join(CONFIG, "commands.yaml")))),
                         learned=data["wordings"], wrong=data["wrong"], model=model)


def outside(m, held):
    """([(sentence, the phrase it means)], [sentences meaning none]) from tests/phrases.yaml and MASSIVE's train split,
    leaving out every held-out sentence."""
    registry = Registry(os.path.join(CONFIG, "commands.yaml"))
    say = {c["name"]: (c.get("say") or [""])[0] for c in registry.commands}
    with open(os.path.join(HERE, "tests", "phrases.yaml"), encoding="utf-8") as f:
        cases = yaml.safe_load(f) or {}
    positives = [(s, say[r.split(":", 1)[1]]) for s, r in cases.items()
                 if str(r).startswith("command:") and say.get(r.split(":", 1)[1])]
    negatives = [s for s, r in cases.items() if r == "claude"]
    if os.path.exists(MASSIVE_TRAIN):
        for s, right in massive.read(MASSIVE_TRAIN).items():
            if right == "none":
                negatives.append(s)
            elif len(right) == 1:  # (one right phrase only: "volume up", not "set an alarm, a reminder or a timer")
                positives.append((s, right[0]))
    return ([(s, p) for s, p in positives if _norm(s) not in held],
            [s for s in negatives if _norm(s) not in held])


def triplets(m, rng):
    """[(anchor, another wording of the same phrase, a sentence that must not be near it)] from the matcher."""
    held = heldout.sentences(m)
    groups = {}
    for target, example in zip(m.targets, m.examples):
        if _norm(example) not in held:
            groups.setdefault(target, []).append(example)
    leave = groups.pop(LEAVE, [])
    rows = [i for i, t in enumerate(m.targets) if t in groups and len(groups[t]) > 1]

    def near_other(vec, target):
        """The nearest wording of another phrase: the one it gets mixed up with. Even a near-synonym ("pause" for
        "pause the music") is worth pushing a little apart: the matcher wants a clear winner, not two close ones."""
        for i in np.argsort(-(m.matrix @ vec)):
            if m.targets[i] not in (target, LEAVE) and m.targets[i] in groups:
                return m.examples[i]
        return None

    out = []
    for i in rows:
        target, anchor = m.targets[i], m.examples[i]
        positive = rng.choice([e for e in groups[target] if e != anchor])
        out.append((target, anchor, positive, near_other(m.matrix[i], target)))
    more, others = outside(m, held)
    for said, target in more:
        if target in groups:
            vec = m.embed([said])[0]
            out.append((target, said, rng.choice(groups[target]), near_other(vec, target)))
    # Sentences that must not reach a phrase: each against the phrase it sits nearest.
    negatives = list(leave) + [said for said, phrases in m.wrong.items() for _ in phrases]
    with open(os.path.join(CONFIG, "chat.yaml"), encoding="utf-8") as f:
        chat = [s for t in (yaml.load(f, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}).values()
                for s in (t.get("says") or [])]
    chat = [s for s in chat if _norm(s) not in held] + others
    vectors = m.embed(chat)
    near_chat = [s for s, v in zip(chat, vectors) if float((m.matrix @ v).max()) >= HARD]
    for said in negatives + near_chat:
        scores = m.matrix @ m.embed([said])[0]
        best = next((j for j in np.argsort(-scores) if m.targets[j] in groups and len(groups[m.targets[j]]) > 1), None)
        if best is not None:
            target = m.targets[best]
            anchor, positive = rng.sample(groups[target], 2)
            out.append((target, anchor, positive, said))
    return out, len(near_chat)


def batches(rows, size, rng):
    """Shuffled batches with each phrase once: every other row in a batch is a wrong answer for the rest."""
    rows = rows[:]
    rng.shuffle(rows)
    out, current, seen = [], [], set()
    for row in rows:
        if row[0] in seen:
            continue
        current.append(row)
        seen.add(row[0])
        if len(current) == size:
            out.append(current)
            current, seen = [], set()
    return out + ([current] if len(current) > 1 else [])


def train(rows, tok, epochs, device, rng, size=48, lr=3e-5, scale=20.0):
    model = Bert().load(os.path.join(BASE, "pytorch_model.bin")).to(device)
    model.train()
    steps = epochs * (len(rows) // size + 1)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / (0.1 * steps)) * max(0.0, 1 - s / steps))
    for epoch in range(epochs):
        total, n = 0.0, 0
        for batch in batches(rows, size, rng):
            a = pooled(model, tok, [r[1] for r in batch], device)
            p = pooled(model, tok, [r[2] for r in batch] + [r[3] for r in batch if r[3]], device)
            # Each anchor's own positive is the right answer among every positive and hard negative in the batch.
            loss = nn.functional.cross_entropy(a @ p.T * scale, torch.arange(len(batch), device=device))
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            total, n = total + loss.item(), n + 1
        print("  epoch %d: loss %.3f" % (epoch + 1, total / max(n, 1)))
    return model.eval()


def export(model, folder):
    """int8 ONNX with the same inputs and output as the downloaded one, so core/intent.py runs it unchanged."""
    from onnxruntime.quantization import QuantType, quantize_dynamic
    os.makedirs(folder, exist_ok=True)
    full = os.path.join(folder, "model.onnx")
    ids = torch.tensor([[101, 7592, 2088, 102]])
    model = model.cpu()
    torch.onnx.export(model, (ids, torch.ones_like(ids), torch.zeros_like(ids)), full,
                      input_names=["input_ids", "attention_mask", "token_type_ids"], output_names=["last_hidden_state"],
                      dynamic_axes={k: {0: "batch", 1: "tokens"} for k in
                                    ("input_ids", "attention_mask", "token_type_ids", "last_hidden_state")},
                      opset_version=14)
    quantize_dynamic(full, os.path.join(folder, "model_quantized.onnx"), weight_type=QuantType.QInt8)
    os.remove(full)  # (the full-size one isn't used: ~90 MB)
    for name in ("phrases.npz",):  # vectors made with an earlier one
        if os.path.exists(os.path.join(folder, name)):
            os.remove(os.path.join(folder, name))
    shutil.copy(os.path.join(MODEL_DIR, "vocab.txt"), os.path.join(folder, "vocab.txt"))


def scored(m, which):
    return heldout.summary(heldout.results(m), m, which)


def best_threshold(m):
    """The threshold doing best on the check half: most right, a wrong one counting as two."""
    best = None
    for t in np.arange(0.60, 0.92, 0.02):
        m.threshold = float(t)
        s = scored(m, "check")
        if best is None or s["right"] - 2 * s["wrong"] > best[1]:
            best = (round(float(t), 2), s["right"] - 2 * s["wrong"])
    m.threshold = best[0]
    return best[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    if not os.path.exists(os.path.join(BASE, "pytorch_model.bin")):
        sys.exit("Put all-MiniLM-L6-v2's pytorch_model.bin in %s first." % BASE)
    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = WordPiece(os.path.join(MODEL_DIR, "vocab.txt"))

    base = matcher()
    rows, chat = triplets(base, rng)
    print("training on %d wordings of %d phrases (%d with a near miss, %d near-phrase chat and other lines), on %s" % (
        len(rows), len({r[0] for r in rows}), sum(1 for r in rows if r[3]), chat, device))
    started = time.time()
    model = train(rows, tok, args.epochs, device, rng)
    print("  trained in %.0f s" % (time.time() - started))
    export(model, OUT)

    tuned = matcher("minilm-tuned")
    threshold = best_threshold(tuned)
    started = time.time()
    tuned.embed(["turn the sound down a bit"] * 50)
    each = (time.time() - started) / 50 * 1000
    print("\nheld-out sentences never trained on (the score half):")
    print("  as downloaded, threshold 0.72: %s" % scored(base, "score"))
    print("  tuned, threshold %.2f:         %s   (%.1f ms a sentence)" % (threshold, scored(tuned, "score"), each))
    print("\nTo use it: config/settings.yaml intents: model: minilm-tuned, threshold: %.2f; restart JARVIS." % threshold)


if __name__ == "__main__":
    main()
