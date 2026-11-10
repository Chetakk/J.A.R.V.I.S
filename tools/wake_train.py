#!/usr/bin/env python3
"""Trains JARVIS's tiny wake word on your own recordings (tools/speechtest.py), on the GPU.

A small classifier on top of openWakeWord's speech embedding, so 20 clips of the name are enough. Every number it
prints comes from clips the model didn't train on (5 folds): a score from what it trained on would mean nothing.

Needs, in ~/.local/share/jarvis/models/wake: melspectrogram.onnx and embedding_model.onnx (openWakeWord v0.5.1 release)
and validation_set_features.npy (huggingface.co/datasets/davidscripka/openwakeword_features).
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import wave

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
import torch  # noqa: E402
import yaml  # noqa: E402

from voice.ears import Embedder  # noqa: E402

JARVIS = os.path.expanduser("~/.local/share/jarvis")
WAKE = os.path.join(JARVIS, "models", "wake")
CLIPS = os.path.join(JARVIS, "speech-test")
MINE = os.path.join(JARVIS, "wake-data", "mine")  # tools/speechtest.py record-wake
MEDIA = os.path.join(JARVIS, "wake-data", "media")  # voice/ears.py MediaRecorder
MINE_HITS = os.path.join(JARVIS, "wake-data", "hits")
FALSE = os.path.join(JARVIS, "wake-data", "false")  # wake-ups you called false ("Jarvis, false wake": voice/ears.py)
PLACES = ("close", "arm", "side", "across", "away", "far", "seat")  # tests/wake_session.yaml parts with the name alone
OUT = "jarvis_wake.next"
FILES = {"mel": "melspectrogram.onnx", "embed": "embedding_model.onnx", "noise": "validation_set_features.npy"}
RATE, LENGTH = 16000, 33600  # 2.1 s: just over what 16 embeddings need
FOLDS = 5
THRESHOLDS = (0.5, 0.7, 0.9, 0.95, 0.98, 0.99, 0.995)
NEAR = """Travis. Jervis. Davis. Harvest. Service. Nervous. Darius. Marvis. Garlic. Jar. Jars. Charles. Carvings. Harris.
Hey Travis. Hey Davis. Hey there. Hey, what's up. Hey buddy. Is this. Thanks a lot. Okay then. Are you there.
Can you pass me the charger. I'll be there in ten minutes. What are you having. Let me think. Good night.
We covered the packing room. Why not. All right.""".replace("\n", " ").split(". ")
rng = np.random.default_rng(7)

embeddings = Embedder()


def read(path):
    with wave.open(path) as f:
        rate, x = f.getframerate(), np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32)
    return x if rate == RATE else resample(x, RATE / rate)


def resample(x, ratio):
    return np.interp(np.linspace(0, len(x) - 1, int(len(x) * ratio)), np.arange(len(x)), x).astype(np.float32)


def speak(lines, scales):
    """Piper's six voices saying each line, at each speed."""
    out, voices = [], os.path.join(JARVIS, "voices")
    for voice in sorted(v for v in os.listdir(voices) if v.endswith(".onnx")):
        for scale in scales:
            with tempfile.TemporaryDirectory() as d:
                done = subprocess.run([os.path.join(JARVIS, "piper", "piper"), "--model", os.path.join(voices, voice), "--output_dir", d,
                                       "--length_scale", str(scale), "--sentence_silence", "0"], input="\n".join(lines).encode(),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if done.returncode:  # a voice this Piper can't run (en_US-mike needs a newer one): train without it
                    break
                out += [read(os.path.join(d, w)) for w in sorted(os.listdir(d))]
    return out


def background(room):
    """Quiet room sound under each clip, so the model never learns "digital silence around it" means the name."""
    x = rng.normal(0, rng.uniform(5, 40), LENGTH).astype(np.float32)
    if rng.random() < 0.3:
        y = room[rng.integers(len(room))]
        y = np.resize(y, LENGTH) * rng.uniform(0.1, 0.6)
        x = x + y
    return x


def placed(clip, room, positive):
    """A 2.1 s window with this clip in it: the name just before the end, anything else anywhere."""
    if rng.random() < 0.7:
        clip = resample(clip, rng.uniform(0.92, 1.08))
    clip = clip * np.exp(rng.uniform(np.log(0.5), np.log(2)))
    x = background(room)
    end = LENGTH - int(rng.uniform(0.05, 0.35) * RATE) if positive else rng.integers(int(0.5 * RATE), LENGTH)
    if positive or len(clip) <= end:
        clip = clip[-end:]
    else:  # a long "not the name" clip (a false wake-up): what set it off can be anywhere in it
        start = rng.integers(0, len(clip) - end + 1)
        clip = clip[start:start + end]
    x[end - len(clip):end] += clip
    return np.clip(x, -32768, 32767)


def views(clip, room, positive, n):
    return np.stack([embeddings(placed(clip, room, positive))[-16:] for _ in range(n)])


def sliding(clip, room):
    """Embedding windows for a clip as a live stream would see it, with room sound around it."""
    x = np.concatenate([np.resize(background(room), RATE), clip, np.resize(background(room), RATE // 2)])
    e = embeddings(np.clip(x, -32768, 32767))
    return np.stack([e[i:i + 16] for i in range(len(e) - 15)])


def fit(X, y, device, epochs=40):
    net = torch.nn.Sequential(torch.nn.Flatten(), torch.nn.Linear(1536, 48), torch.nn.ReLU(), torch.nn.Dropout(0.3),
                              torch.nn.Linear(48, 48), torch.nn.ReLU(), torch.nn.Linear(48, 1)).to(device)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-2)
    weight = torch.tensor([float((y == 0).sum()) / max(float((y == 1).sum()), 1)], device=device)
    for _ in range(epochs):
        for batch in torch.randperm(len(X), device=device).split(1024):
            opt.zero_grad()
            loss = torch.nn.functional.binary_cross_entropy_with_logits(net(X[batch]).squeeze(1), y[batch], pos_weight=weight)
            loss.backward()
            opt.step()
    return net.eval()


def score(net, windows, device):
    with torch.no_grad():
        return float(torch.sigmoid(net(torch.tensor(windows, device=device))).max())


def stacked(embedded, step):
    """Windows of 16 embeddings from a continuous run of them."""
    return np.lib.stride_tricks.sliding_window_view(embedded, 16, axis=0)[::step].transpose(0, 2, 1).astype(np.float32)


def alarms(scores, cutoff, step=2):
    """False alarms in a run of window scores (`step` windows of 80 ms apart); hits within 2 s are one alarm."""
    hits = np.where(scores >= cutoff)[0]
    return 1 + int((np.diff(hits) > 25 // step).sum()) if len(hits) else 0


def started(path):
    """When a recording began, from its name (20261109-152249-539.wav)."""
    return time.mktime(time.strptime(os.path.basename(path)[:15], "%Y%m%d-%H%M%S"))


def show_audio():
    """The room while a show or song played (voice/ears.py MediaRecorder): (windows to train on, held-out runs, hours
    held out). The last fifth of each recording is held out; 3 s around any wake-up then is left out of both."""
    woke = []
    if os.path.exists(os.path.join(MINE_HITS, "hits.jsonl")):
        with open(os.path.join(MINE_HITS, "hits.jsonl")) as f:
            woke = [json.loads(l)["time"] for l in f if l.strip()]
    train, held, seconds = [], [], 0.0
    for path in sorted(glob.glob(os.path.join(MEDIA, "*.wav"))):
        x = read(path)
        if len(x) < 3 * RATE:
            continue
        e = embeddings(x)
        clear = np.ones(len(e), dtype=bool)
        for t in woke:
            i = int((t - started(path)) / 0.08)
            clear[max(0, i - 40):max(0, i + 40)] = False
        w = stacked(e, 1)
        ok = np.array([clear[i:i + 16].all() for i in range(len(w))])
        cut = int(len(w) * 0.8)
        train.append(w[:cut][ok[:cut]][::4])
        held.append(w[cut:][ok[cut:]])
        seconds += len(held[-1]) * 0.08
    return (np.concatenate(train) if train else np.zeros((0, 16, 96), np.float32)), held, seconds / 3600


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    with open(os.path.join(HERE, "tests", "speech_commands.yaml"), encoding="utf-8") as f:
        lines = yaml.safe_load(f)
    with open(os.path.join(HERE, "tests", "wake_session.yaml"), encoding="utf-8") as f:
        session = yaml.safe_load(f)
    clip = lambda part, i: read(os.path.join(CLIPS, "%s-%02d.wav" % (part, i)))  # noqa: E731
    mine = lambda part, i: os.path.join(MINE, "%s-%02d.wav" % (part, i))  # noqa: E731
    named = {i: t for i, t in enumerate(lines["commands"], 1) if "jarvis" in t.lower().split(",")[0]}
    bare = [i for i in range(1, len(lines["commands"]) + 1) if i not in named]
    wake = {("wake", i): clip("wake", i) for i in range(1, len(lines["wake"]) + 1)}
    # Your "Jarvis" from around the room (tools/speechtest.py record-wake), and what must not wake it
    wake.update({(part, i): read(mine(part, i)) for part in PLACES for i in range(1, len(session.get(part) or []) + 1)
                 if os.path.exists(mine(part, i))})
    other = dict([(("commands", i), clip("commands", i)) for i in bare] + [(("noise", i), clip("noise", i)) for i in range(1, len(lines["noise"]) + 1)])
    other.update({(part, i): read(mine(part, i)) for part in ("near", "talk") for i in range(1, len(session.get(part) or []) + 1)
                  if os.path.exists(mine(part, i))})
    other.update({("false", i): read(p) for i, p in enumerate(sorted(glob.glob(os.path.join(FALSE, "*.wav"))), 1)})
    breath = {("named", i): clip("commands", i) for i in named}
    breath.update({("breath", i): read(mine("breath", i)) for i in range(1, len(session.get("breath") or []) + 1)
                   if os.path.exists(mine("breath", i))})
    room = [c for k, c in other.items() if k[0] == "noise"]
    print("clips: %d name, %d other (%d false wakes), %d name-in-one-breath" % (
        len(wake), len(other), sum(k[0] == "false" for k in other), len(breath)), flush=True)

    print("voices...", flush=True)
    tts_pos = speak(["Jarvis.", "Hey Jarvis.", "Jarvis!", "Hey, Jarvis."], (0.8, 1.0, 1.25))
    tts_neg = speak(NEAR + [t for t in lines["commands"] if "jarvis" not in t.lower()], (1.0,))
    print("voices: %d name, %d other" % (len(tts_pos), len(tts_neg)), flush=True)

    print("features...", flush=True)
    X, y, source = [], [], []
    def add(views_, label, src):  # noqa: E306
        X.append(views_)
        y.extend([label] * len(views_))
        source.extend([src] * len(views_))
    for k, c in wake.items():
        add(views(c, room, True, 120 if k[0] == "wake" else 60), 1, k)
    for k, c in other.items():
        add(views(c, room, False, 40), 0, k)
    for c in tts_pos:
        add(views(c, room, True, 12), 1, ("tts", 0))
    for c in tts_neg:
        add(views(c, room, False, 3), 0, ("tts", 0))
    X, y = np.concatenate(X).astype(np.float32), np.array(y, dtype=np.float32)
    print("training set: %d windows, %d of the name" % (len(X), int(y.sum())), flush=True)

    outside = np.load(os.path.join(WAKE, FILES["noise"]))  # ~10.7 h of speech, noise and music, one embedding per 80 ms
    cut = int(len(outside) * 0.8)  # split by time, so the held-out audio really is unheard
    used, held_out = stacked(outside[:cut], 8), stacked(outside[cut:], 2)
    hours = len(held_out) * 2 * 0.08 / 3600
    show, show_held, show_hours = show_audio()
    used = np.concatenate([used, show])
    print("outside audio: %.1f hours held out; your room with a show on: %d windows to train on, %.2f hours held out" %
          (hours, len(show), show_hours), flush=True)

    test = {k: sliding(c, room) for k, c in list(wake.items()) + list(other.items()) + list(breath.items())}

    fold_of = lambda s: (s[1] % FOLDS) if s[0] != "tts" else -1  # noqa: E731
    scores, outside_scores, show_scores = {}, [], []
    for fold in range(FOLDS):
        mask = np.array([fold_of(s) != fold for s in source])
        trainX = torch.tensor(np.concatenate([X[mask], used]), device=device)
        trainY = torch.tensor(np.concatenate([y[mask], np.zeros(len(used), dtype=np.float32)]), device=device)
        net = fit(trainX, trainY, device)
        for k, w in test.items():
            if k[0] in ("named", "breath") or fold_of(k) == fold:  # (name-in-one-breath clips are never trained on)
                scores.setdefault(k, []).append(score(net, w, device))
        with torch.no_grad():
            run = lambda w: torch.sigmoid(net(torch.tensor(w, device=device))).squeeze(1).cpu().numpy()  # noqa: E731
            outside_scores.append(run(held_out))
            show_scores.append([run(w) for w in show_held if len(w)])
        print("fold %d done" % fold, flush=True)

    hit = lambda kinds, t: (sum(max(v) >= t for k, v in scores.items() if k[0] in kinds),  # noqa: E731
                            sum(k[0] in kinds for k in scores))
    print("\nOn clips each model never trained on. Name: should wake. Others: should not.")
    print("%6s  %-9s %-11s %-10s %-11s %-9s %-7s %-11s %-10s %s" % ("cutoff", "old name", "room name", "one-breath",
          "sound-alike", "commands", "noise", "false wake", "outside/h", "show/h"))
    table = []
    for t in THRESHOLDS:
        row = {"cutoff": t, "old": hit(("wake",), t), "room": hit(PLACES, t), "breath": hit(("named", "breath"), t),
               "near": hit(("near", "talk"), t), "commands": hit(("commands",), t), "noise": hit(("noise",), t), "false": hit(("false",), t),
               "per_hour": float(np.mean([alarms(s, t) for s in outside_scores])) / hours,
               "show_per_hour": (float(np.mean([sum(alarms(r, t, 1) for r in runs) for runs in show_scores])) / show_hours
                                 if show_hours else None)}
        table.append(row)
        f = lambda p: "%d/%d" % p  # noqa: E731
        print("%6.3f  %-9s %-11s %-10s %-11s %-9s %-7s %-11s %-10.2f %s" % (t, f(row["old"]), f(row["room"]), f(row["breath"]),
              f(row["near"]), f(row["commands"]), f(row["noise"]), f(row["false"]), row["per_hour"],
              "%.1f" % row["show_per_hour"] if show_hours else "-"))
    print("\nby place, at 0.98:", ", ".join("%s %d/%d" % ((p,) + hit((p,), 0.98)) for p in ("wake",) + PLACES))
    missed = sorted("%s-%02d %.2f" % (k[0], k[1], max(v)) for k, v in scores.items() if k[0] in PLACES + ("wake",) and max(v) < 0.98)
    print("name clips under 0.98:", ", ".join(missed) or "none")

    final = fit(torch.tensor(np.concatenate([X, used]), device=device),
                torch.tensor(np.concatenate([y, np.zeros(len(used), dtype=np.float32)]), device=device), device)
    os.makedirs(WAKE, exist_ok=True)
    out = os.path.join(WAKE, OUT)  # voice/ears.py keeps using jarvis_wake.npz until this one is swapped in
    torch.save(final.state_dict(), out + ".pt")
    layers = [final[i] for i in (1, 4, 6)]  # voice/ears.py runs them with numpy, so JARVIS doesn't load PyTorch
    np.savez(out + ".npz", **{"w%d" % n: l.weight.detach().cpu().numpy() for n, l in enumerate(layers)},
             **{"b%d" % n: l.bias.detach().cpu().numpy() for n, l in enumerate(layers)})
    with open(out + "-results.json", "w") as f:
        json.dump({"table": table, "scores": {"%s-%02d" % k: v for k, v in scores.items()}}, f, indent=1, default=float)
    print("\nsaved %s.npz: to use it, copy it over jarvis_wake.npz and restart JARVIS" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
