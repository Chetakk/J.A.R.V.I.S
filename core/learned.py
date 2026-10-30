"""Wordings JARVIS learned from Claude: what you said -> the JARVIS phrase Claude turned it into.

Next time the same words (or close ones, via the meaning matcher) work at once, without Claude.
Kept in ~/.local/share/jarvis/learned.yaml (yours, not part of the code); edit or delete lines freely.
Each one learned is also noted in taught.jsonl, which is what "did you learn anything new?" reads.
"""
import json
import os
import re
import threading
import time

import yaml

from core.store import keep_damaged

PATH = os.path.expanduser("~/.local/share/jarvis/learned.yaml")
TAUGHT = os.path.expanduser("~/.local/share/jarvis/taught.jsonl")
_lock = threading.Lock()  # a request and the background teacher can both be saving


def load(path=None):
    try:
        with open(path or PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        data = {}
    except yaml.YAMLError:  # a slip while editing it by hand: keep it, or the next save would wipe your wordings
        keep_damaged(path or PATH)
        data = {}
    if not isinstance(data, dict):
        keep_damaged(path or PATH)
        data = {}
    return {"wordings": dict(data.get("wordings") or {}), "declined": list(data.get("declined") or [])}


def _write(data, path=None):
    path = path or PATH
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# Learned from Claude (core/learned.py): what you said -> what JARVIS does. Edit freely.\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=True)
    os.replace(tmp, path)


def remember(said, phrase, path=None, why=""):
    with _lock:
        data = load(path)
        data["wordings"][said.lower().strip()] = phrase
        _write(data, path)
    if not path:
        _note(said.lower().strip(), phrase, why)


def _note(said, phrase, why):
    try:
        with open(TAUGHT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M"), "said": said, "means": phrase,
                                "from": why}) + "\n")
    except OSError as e:
        print("[learned] can't write %s: %r" % (TAUGHT, e))


def history():
    """Everything learned, oldest first: [{"time", "said", "means", "from"}]."""
    rows = []
    try:
        with open(TAUGHT, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return rows


def decline(said, path=None):
    with _lock:
        data = load(path)
        if said.lower().strip() not in data["declined"]:
            data["declined"].append(said.lower().strip())
        _write(data, path)


def worth_learning(said, phrase, data, longest=10):
    """A short, value-free wording, new, and different from the phrase it maps to."""
    s = said.lower().strip(" .!?")
    return (s != phrase.lower().strip(" .!?") and not re.search(r"\d", s) and 1 < len(s.split()) <= longest
            and s not in data["wordings"] and s not in data["declined"])
