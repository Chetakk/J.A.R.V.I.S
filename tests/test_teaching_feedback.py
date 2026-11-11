"""What turns out wrong is taken back: "that's not what I said" after a match by meaning, a "no" to "did you mean
X?", and a teaching pass's wordings that make a held-out sentence (core/heldout.py) go wrong.

Nothing here reaches Claude or the desktop: the harness answers for Claude and fakes the shell.
"""
import json
import time

import pytest
import yaml

from core import heldout, learned, outcomes, teaching

pytestmark = pytest.mark.usefixtures("strict_check_agrees")  # the strict check has its own tests

CFG = {"phrases": 1, "wordings": 4, "misses": 4}
EARS = "my ears are hurting from this"


@pytest.fixture
def cases(tmp_path, monkeypatch):
    """A small held-out file of its own: write({sentence: [phrases] or "none"})."""
    path = tmp_path / "heldout.yaml"
    monkeypatch.setattr(heldout, "CASES", str(path))

    def write(labels):
        path.write_text(yaml.safe_dump(labels))
        return str(path)
    return write


def taught(jv, wording=EARS, phrase="volume down"):
    assert teaching.teach(jv.j, [(wording, phrase)])[0]


# --- "that's not what I said"

def test_saying_its_wrong_takes_back_the_learned_wording(jv):
    taught(jv)
    jv.do(EARS)
    jv.do("that's not what i said")
    assert jv.says("my mistake")
    assert jv.j.intents.early(EARS) is None
    assert jv.j.intents.match(EARS)[0] != "volume down"
    data = learned.load()
    assert EARS not in data["wordings"] and data["wrong"][EARS] == ["volume down"]
    assert learned.history()[-1]["unlearned"]


def test_a_wording_found_wrong_is_never_learned_for_that_phrase_again(jv):
    taught(jv)
    jv.do(EARS)
    jv.do("no, that's wrong")
    kept, refused = teaching.teach(jv.j, [(EARS, "volume down")])
    assert not kept and "found wrong" in refused[0][2]


def test_it_stays_wrong_after_a_restart(jv):
    taught(jv)
    jv.do(EARS)
    jv.do("wrong one")
    from core.intent import IntentMatcher
    data = learned.load()
    m = IntentMatcher(teaching.CASES.replace("tests/phrases.yaml", "config/intents.yaml"),
                      learned=data["wordings"], wrong=data["wrong"])
    assert m.early(EARS) is None and m.match(EARS)[0] != "volume down"


def test_no_i_meant_does_that_and_learns_it(jv):
    taught(jv)
    jv.do(EARS)
    jv.do("no, i meant mute the sound")
    assert jv.j.intents.early(EARS) == "mute the sound"
    assert learned.load()["wordings"][EARS] == "mute the sound"


def test_a_correction_long_after_the_match_is_not_one(jv, monkeypatch):
    taught(jv)
    jv.do(EARS)
    jv.j._guess = (time.time() - 120,) + jv.j._guess[1:]
    jv.do("that's not what i said")
    assert jv.j.intents.early(EARS) == "volume down"


def test_a_correction_after_something_else_is_not_about_the_match(jv):
    taught(jv)
    jv.do(EARS)
    jv.do("what's on my clipboard")
    jv.do("that's not what i said")
    assert jv.j.intents.early(EARS) == "volume down"


def test_the_next_pass_asks_claude_what_it_did_mean(jv):
    taught(jv)
    jv.do(EARS)
    jv.do("that's not what i said")
    jv.claude.reply(json.dumps({EARS: "mute the sound"}))
    jv.claude.reply("{}")
    teaching.run(jv.j, CFG)
    assert EARS in jv.claude.asked("which single phrase")[0]
    assert jv.j.intents.early(EARS) == "mute the sound"
    jv.claude.reply("{}")
    teaching.run(jv.j, CFG)
    assert not any(EARS in q for q in jv.claude.asked("which single phrase")[1:])  # asked once


# --- "did you mean X?" and "undo that"

def test_no_to_did_you_mean_blocks_that_meaning(jv, monkeypatch):
    monkeypatch.setattr(jv.j.intents, "candidates", lambda text, n=2: [("volume down", 0.66), ("pin it", 0.3)])
    jv.do("the noise in here is a lot", "no")
    assert "volume down" in jv.j.intents.wrong["the noise in here is a lot"]
    assert learned.load()["wrong"]["the noise in here is a lot"] == ["volume down"]


def test_no_answer_to_did_you_mean_blocks_nothing(jv, monkeypatch):
    monkeypatch.setattr(jv.j.intents, "candidates", lambda text, n=2: [("volume down", 0.66), ("pin it", 0.3)])
    jv.do("the noise in here is a lot")
    assert not learned.load()["wrong"]


def test_undo_right_after_a_match_by_meaning_goes_to_claude_to_check(jv):
    taught(jv)
    jv.do(EARS)
    jv.do("undo that")
    assert outcomes.doubtful(outcomes.read(), jv.j.intents.threshold) == {EARS: "volume down"}
    assert "volume down" not in outcomes.counted(outcomes.read(), "phrase") or \
        outcomes.counted(outcomes.read(), "phrase")["volume down"] == 1  # the undo isn't a use


def test_claude_placing_a_doubtful_match_elsewhere_blocks_it(jv):
    said = "the sound is a bit much"
    reached = jv.j.intents.match(said)[0]
    assert reached and reached != "mute the sound"
    outcomes.log("meaning", said, reached, score=jv.j.intents.threshold + 0.01)
    jv.claude.reply(json.dumps({said: "mute the sound"}))
    jv.claude.reply("{}")
    teaching.run(jv.j, CFG)
    assert reached in jv.j.intents.wrong[said] and learned.load()["wrong"][said] == [reached]
    assert jv.j.intents.match(said)[0] != reached


# --- the held-out score

def test_grades(jv):
    assert heldout.grade({"volume down"}, "volume down") == "right"
    assert heldout.grade({"volume down"}, None) == "left"
    assert heldout.grade({"none", "set a timer"}, None) == "right"
    assert heldout.grade(None, None) == "right"
    assert heldout.grade(None, "volume down") == "wrong"
    assert heldout.grade({"volume down"}, "volume up") == "wrong"


def test_the_halves_never_change(jv):
    assert heldout.half("turn the sound down") == heldout.half("Turn the sound down")
    halves = {heldout.half("sentence number %s" % w) for w in "one two three four five six seven eight".split()}
    assert halves == {"check", "score"}


def test_a_held_out_sentence_is_never_taught(jv, cases):
    cases({"the sound is far too much for my ears": ["volume down"]})
    kept, refused = teaching.teach(jv.j, [("the sound is far too much for my ears", "volume down")])
    assert not kept and "held-out" in refused[0][2]


def test_a_pass_takes_back_a_wording_that_makes_a_held_out_sentence_wrong(jv, cases):
    near = next(s for s in ("my ears are hurting from this one", "my ears are hurting from this now",
                            "my ears are hurting from this today", "my ears are hurting from all this")
                if heldout.half(s) == "check")
    cases({near: "none"})
    before = heldout.results(jv.j.intents)
    assert heldout.grade(None, before[near][0]) == "right"
    taught(jv)
    score = teaching.check(jv.j, teaching._state(), before, [(EARS, "volume down")])
    assert score["took back"] == [EARS]
    assert jv.j.intents.early(EARS) is None and EARS not in learned.load()["wordings"]
    assert score["after"]["wrong"] == 0


def test_a_pass_keeps_wordings_that_break_nothing_and_saves_its_score(jv, cases):
    cases({"what's the weather like": "none", "turn it up": ["volume up"]})
    phrase = teaching.wanted(jv.j, teaching._state(), [], CFG)[0]
    jv.claude.reply(json.dumps({phrase: {"wordings": ["a way of saying it nobody would mistake"], "enough": False}}))
    teaching.run(jv.j, CFG)
    scores = teaching._state()["scores"]
    if scores:  # (only when the wording was kept)
        assert scores[-1]["took back"] == [] and set(scores[-1]["after"]) == {"right", "wrong", "left", "out_of"}


def test_the_real_held_out_file_loads_and_scores(jv):
    labels, texts, vectors = heldout.load(jv.j.intents)
    assert len(texts) > 1500 and vectors.shape == (len(texts), jv.j.intents.matrix.shape[1])
    got = heldout.results(jv.j.intents)
    s = heldout.summary(got, jv.j.intents)
    assert s["out_of"] > 700 and s["right"] > 0.8 * s["out_of"]
