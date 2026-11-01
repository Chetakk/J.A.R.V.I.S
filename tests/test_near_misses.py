"""Near but different (core/teaching.py near_misses): sentences that sound like one of JARVIS's phrases but mean
another, or none of them. A "none" one becomes a "leave it" row (core/intent.py LEAVE): nearest, it leaves the
request alone for Claude.

Nothing here reaches Claude or the desktop.
"""
import json

import pytest
import yaml

from core import heldout, learned, teaching
from core.intent import LEAVE

SPHERE = "what's the volume of a sphere with a radius of a metre"
SPHERE_NEAR = "what is the volume of a sphere with a radius of one metre"


@pytest.fixture
def one_phrase(monkeypatch):
    monkeypatch.setattr(teaching, "wanted", lambda *a, **k: ["volume up"])


def test_a_leave_it_row_leaves_what_is_near_it_alone(jv):
    assert teaching.teach(jv.j, [(SPHERE, LEAVE)])[0]
    assert jv.j.intents.match(SPHERE_NEAR)[0] is None
    assert jv.j.intents.early(SPHERE) is None  # never a phrase word for word
    assert LEAVE not in [p for p, _ in jv.j.intents.candidates(SPHERE_NEAR, n=5)]
    assert jv.j.intents.left(SPHERE_NEAR)
    assert learned.load()["wordings"][SPHERE] == LEAVE


def test_it_is_not_asked_about_either(jv, monkeypatch):
    teaching.teach(jv.j, [(SPHERE, LEAVE)])
    monkeypatch.setattr(jv.j.intents, "candidates", lambda text, n=2: [("volume up", 0.66), ("pin it", 0.3)])
    jv.do(SPHERE_NEAR)
    assert not any("did you mean" in q.lower() for q in jv.questions)


def test_a_leave_it_row_that_would_stop_a_known_sentence_is_refused(jv):
    # (one it reaches by meaning, not one of its own wordings: those score 1.0, and nothing near beats them)
    case = next(t for t, want in yaml.safe_load(open(teaching.CASES)).items()
                if want.startswith("command:") and jv.j.intents.early(t) is None
                and jv.j.intents.threshold <= jv.j.intents.match(t)[1] < 0.9 and jv.j.intents.match(t)[0])
    kept, refused = teaching.teach(jv.j, [(case + " right now", LEAVE)])
    assert not kept and "would stop" in refused[0][2]


def test_a_pass_asks_for_near_misses_and_teaches_them(jv, one_phrase):
    jv.claude.reply(json.dumps({"volume up": {"wordings": [], "enough": False, "not": {
        SPHERE: "none", "it's far too loud in here bring it down": "volume down", "make it rain": "a made-up phrase"}}}))
    line = teaching.run(jv.j, {"phrases": 1, "wordings": 4, "misses": 0})
    asked = jv.claude.asked("must NOT do it")
    assert asked and "volume up" in asked[0]
    words = learned.load()["wordings"]
    assert words.get(SPHERE) == LEAVE
    assert words.get("it's far too loud in here bring it down") == "volume down"
    assert "make it rain" not in words  # a phrase JARVIS doesn't have: dropped, never guessed at
    assert "near but different" in line


def test_a_leave_it_row_that_makes_a_held_out_sentence_worse_is_taken_back(jv, tmp_path, monkeypatch):
    sentence = next(s for s in ("turn the volume up a bit more", "turn the volume up some more",
                                "turn the volume up a little more", "turn the volume up a little bit")
                    if heldout.half(s) == "check" and jv.j.intents.match(s)[0] == "volume up")
    path = tmp_path / "heldout.yaml"
    path.write_text(yaml.safe_dump({sentence: ["volume up"]}))
    monkeypatch.setattr(heldout, "CASES", str(path))
    before = heldout.results(jv.j.intents)
    row = "turn the volume up a bit more now"
    jv.j.intents.learn(row, LEAVE)
    learned.remember(row, LEAVE)
    score = teaching.check(jv.j, teaching._state(), before, [(row, LEAVE)])
    assert score["took back"] == [row] and jv.j.intents.match(sentence)[0] == "volume up"
    assert row not in learned.load()["wordings"]
