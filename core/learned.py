"""Wordings JARVIS learned from Claude: what you said -> the JARVIS phrase Claude turned it into.

Next time the same words (or close ones, via the meaning matcher) work at once, without Claude.
Kept in ~/.local/share/jarvis/learned.yaml (yours, not part of the code); edit or delete lines freely.
"""
import os
import re

import yaml

PATH = os.path.expanduser("~/.local/share/jarvis/learned.yaml")


def load(path=None):
    try:
        with open(path or PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    return {"wordings": dict(data.get("wordings") or {}), "declined": list(data.get("declined") or [])}


def _write(data, path=None):
    path = path or PATH
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# Learned from Claude (core/learned.py): what you said -> what JARVIS does. Edit freely.\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=True)
    os.replace(tmp, path)


def remember(said, phrase, path=None):
    data = load(path)
    data["wordings"][said.lower().strip()] = phrase
    _write(data, path)


def decline(said, path=None):
    data = load(path)
    if said.lower().strip() not in data["declined"]:
        data["declined"].append(said.lower().strip())
    _write(data, path)


def worth_learning(said, phrase, data):
    """A short, value-free wording, new, and different from the phrase it maps to."""
    s = said.lower().strip(" .!?")
    return (s != phrase.lower().strip(" .!?") and not re.search(r"\d", s) and 1 < len(s.split()) <= 10
            and s not in data["wordings"] and s not in data["declined"])
