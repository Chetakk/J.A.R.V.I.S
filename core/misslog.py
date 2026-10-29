"""Requests JARVIS couldn't handle itself (sent to Claude, or not understood), one JSON line each.

Reviewed with tools/misses.py; common ones become new wordings in config/intents.yaml.
"""
import json
import os
import time

PATH = os.path.expanduser("~/.local/share/jarvis/misses.jsonl")


def log(text, outcome, guess=None, score=None):
    entry = {"time": time.strftime("%Y-%m-%d %H:%M"), "text": text, "outcome": outcome}
    if guess:
        entry.update(guess=guess, score=round(score, 2))
    try:
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print("[misses] can't write %s: %r" % (PATH, e))


def read(path=None):
    try:
        with open(path or PATH, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []
