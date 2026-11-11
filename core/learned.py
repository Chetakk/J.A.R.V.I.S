"""Wordings JARVIS learned from Claude: what you said -> the JARVIS phrase Claude turned it into.

Next time the same words (or close ones, via the meaning matcher) work at once, without Claude.
Kept in ~/.local/share/jarvis/learned.yaml (yours, not part of the code); edit or delete lines freely.
Each one learned is also noted in taught.jsonl, which is what "did you learn anything new?" reads.

`wrong` holds what turned out to be wrong: what you said -> the phrases it must never mean again ("that's not what I
said", or a wording a teaching pass made worse). A wording that's there is never learned for that phrase again.
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
    wrong = data.get("wrong") if isinstance(data.get("wrong"), dict) else {}
    return {"wordings": dict(data.get("wordings") or {}), "declined": list(data.get("declined") or []),
            "wrong": {str(k): [str(p) for p in (v if isinstance(v, list) else [v])] for k, v in wrong.items()}}


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


def history():
    """Everything learned, oldest first: [{"time", "said", "means", "from"}] ("unlearned": true when it was taken
    back)."""
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


def wrong(said, phrase, path=None, why=""):
    """`said` must never mean `phrase` again. If it was learned to mean that, it's unlearned: True when it was."""
    said = said.lower().strip()
    with _lock:
        data = load(path)
        if phrase not in data["wrong"].setdefault(said, []):
            data["wrong"][said].append(phrase)
        was = data["wordings"].get(said) == phrase
        if was:
            del data["wordings"][said]
        _write(data, path)
    if was and not path:
        _note(said, phrase, why, unlearned=True)
    return was


def _note(said, phrase, why, unlearned=False):
    try:
        with open(TAUGHT, "a", encoding="utf-8") as f:
            row = {"time": time.strftime("%Y-%m-%d %H:%M"), "said": said, "means": phrase, "from": why}
            f.write(json.dumps(dict(row, unlearned=True) if unlearned else row) + "\n")
    except OSError as e:
        print("[learned] can't write %s: %r" % (TAUGHT, e))


def worth_learning(said, phrase, data, longest=10):
    """A short, value-free wording, new, different from the phrase it maps to, and not found wrong for it."""
    s = said.lower().strip(" .!?")
    return (s != phrase.lower().strip(" .!?") and not re.search(r"\d", s) and 1 < len(s.split()) <= longest
            and s not in data["wordings"] and s not in data["declined"] and phrase not in data["wrong"].get(s, ()))


# A path, or a name with a file's extension (not a website's: "open youtube.com" is fine to learn).
FILE_NAME = r"[~/]\S+|\S+\.(?:txt|md|pdf|docx?|xlsx?|pptx?|odt|csv|json|ya?ml|py|sh|log|html?|png|jpe?g|mp[34]|zip)\b"


def points(said, phrase):
    """ "open claude in this" -> "open day-tracker project in vs code": "this" was the project on screen then.
    A wording that points at what's on screen only meant that phrase once."""
    here = r"\b(?:this|that|it|here|there|these|those|on (?:my|the) screen|(?:i'm|i am) (?:on|in|looking at))\b"
    if re.search(here, said.lower()) and not re.search(here, phrase.lower()):
        return True
    # "read the summarised version" -> "summarise AI Process Engineer.txt": a file or path Claude filled in
    # from what was going on is right only that once.
    return any(f not in said.lower() for f in re.findall(FILE_NAME, phrase.lower()))
