"""Held-out sentences (tests/heldout.yaml): how well the meaning matcher does on sentences nobody taught it.

Teaching (core/teaching.py) scores the matcher before and after each pass. A wording it learned that makes a
sentence here go wrong is taken back. Nothing here is ever sent to Claude or learned from, so the score says how
JARVIS does on new wordings, not on the ones it has seen.

The sentences are split in two, by a hash of their words, so the split never changes:
  check  what a pass is checked against: a wording that makes one of these worse is taken back
  score  only ever counted, never used to decide anything, so it stays an honest score

For each sentence the matcher gets it right (one of its phrases, or no match where that's fine), wrong (another
phrase, or any phrase for a sentence marked none), or leaves it (no match where a phrase was wanted: it would go on
to Claude, which is safe, just slower).
The vectors are worked out once (~10 s) and kept in ~/.local/share/jarvis/heldout.npz.
"""
import hashlib
import os

import numpy as np
import yaml

from core.intent import _norm

CASES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "heldout.yaml")
CACHE = os.path.expanduser("~/.local/share/jarvis/heldout.npz")
_memo = {}  # (file's hash) -> (labels, texts, vectors): worked out once per run


def half(text):
    return "check" if int(hashlib.sha1(_norm(text).encode()).hexdigest(), 16) % 2 == 0 else "score"


def load(matcher, path=None):
    """({sentence: set of right phrases, or None for none}, [sentences], their vectors), or ({}, [], None)."""
    try:
        with open(path or CASES, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return {}, [], None
    key = hashlib.sha1(raw.encode()).hexdigest()
    if key in _memo:
        return _memo[key]
    cases = yaml.safe_load(raw) or {}
    labels = {str(t): (None if v == "none" else set(v)) for t, v in cases.items() if v == "none" or isinstance(v, list)}
    texts = list(labels)
    vectors = None
    try:
        data = np.load(CACHE)
        if str(data["key"]) == key:
            vectors = data["vectors"]
    except (OSError, KeyError, ValueError):
        pass
    if vectors is None and texts:
        vectors = matcher.embed(texts)
        try:
            np.savez(CACHE, key=key, vectors=vectors)
        except OSError as e:
            print("[heldout] can't cache vectors: %r" % e)
    _memo[key] = labels, texts, vectors
    return _memo[key]


def results(matcher, path=None):
    """{sentence: (phrase it gets or None, the wording that won or None)} for every held-out sentence."""
    labels, texts, vectors = load(matcher, path)
    if not texts:
        return {}
    return dict(zip(texts, matcher.predict(texts, vectors)))


def grade(right, phrase):
    """"right", "left" (no match, where a phrase was wanted) or "wrong", for one sentence."""
    if phrase is None:
        return "right" if right is None or "none" in right else "left"
    return "right" if right is not None and phrase in right else "wrong"


def summary(got, matcher, which="score", path=None):
    """{"right", "wrong", "left", "out_of"} over one half; wrong is any phrase that shouldn't have been given."""
    labels = load(matcher, path)[0]
    out = {"right": 0, "wrong": 0, "left": 0, "out_of": 0}
    for text, (phrase, _) in got.items():
        if half(text) != which:
            continue
        out[grade(labels[text], phrase)] += 1
        out["out_of"] += 1
    return out


RANK = {"right": 2, "left": 1, "wrong": 0}


def worse(before, after, matcher, path=None):
    """[(sentence, the wording now winning it)] for check-half sentences that got worse: right to left or wrong, or
    left to wrong."""
    labels = load(matcher, path)[0]
    out = []
    for text, (phrase, winner) in after.items():
        if half(text) != "check" or text not in before:
            continue
        if RANK[grade(labels[text], phrase)] < RANK[grade(labels[text], before[text][0])]:
            out.append((text, winner))
    return out


def sentences(matcher, path=None):
    """Every held-out sentence, as compared word for word: none of them may be taught."""
    return {_norm(t) for t in load(matcher, path)[1]}
