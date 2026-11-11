"""Teaching's strict check (core/teaching.py review): nothing is learned on one answer from Claude, and what was
learned before, what you saved with "remember that command" first, is rechecked and put right.

Nothing here reaches Claude or the desktop: the harness answers for Claude.
"""
import json
import os

from core import learned, misslog, outcomes, teaching

CFG = {"phrases": 1, "wordings": 4, "misses": 4}
LOUD = "the sound is way too loud for me"


def miss(text):
    os.makedirs(os.path.dirname(misslog.PATH), exist_ok=True)
    with open(misslog.PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": "2026-10-30 10:00", "text": text, "outcome": "claude"}) + "\n")


def checks(jv):
    return jv.claude.asked("strict checker")


def test_what_claude_placed_is_learned_only_when_the_strict_check_agrees(jv):
    miss(LOUD)
    jv.claude.reply(json.dumps({LOUD: "volume down"}))  # what it meant
    jv.claude.reply("{}")  # wordings: none
    jv.claude.reply(json.dumps({LOUD: "none"}))  # the strict check
    assert teaching.run(jv.j, CFG) == ""
    assert '"%s" -> volume down' % LOUD in checks(jv)[0]
    assert learned.load()["wordings"] == {}


def test_a_yes_from_the_strict_check_is_learned(jv):
    miss(LOUD)
    jv.claude.reply(json.dumps({LOUD: "volume down"}))
    jv.claude.reply("{}")
    jv.claude.reply(json.dumps({LOUD: "yes"}))
    assert "placed 1 of 1" in teaching.run(jv.j, CFG)
    assert jv.j.intents.early(LOUD) == "volume down"


def test_no_answer_from_the_strict_check_means_nothing_is_learned(jv):
    miss(LOUD)
    jv.claude.reply(json.dumps({LOUD: "volume down"}))
    jv.claude.reply("{}")
    jv.claude.reply("I couldn't decide.")
    teaching.run(jv.j, CFG)
    assert learned.load()["wordings"] == {}


def test_a_command_you_saved_that_was_wrong_is_taken_back_and_put_right(jv):
    """You said "remember that command" after the wrong thing happened: the check sees what you said around then."""
    learned.remember(LOUD, "volume up", why="you asked me to remember it")
    jv.j.intents.learn(LOUD, "volume up")
    outcomes.log("pattern", "no that's the wrong way", "volume down", "Audio")
    jv.claude.reply("{}")  # wordings: none
    jv.claude.reply(json.dumps({LOUD: "volume down"}))  # the strict check
    assert "took back 1" in teaching.run(jv.j, CFG)
    asked = checks(jv)[0]
    assert '"%s" -> volume up' % LOUD in asked and "no that's the wrong way" in asked
    assert learned.load()["wordings"] == {LOUD: "volume down"}
    assert jv.j.intents.early(LOUD) == "volume down"


def test_a_saved_command_claude_says_means_nothing_is_just_taken_back(jv):
    learned.remember("ask claude why it didn't work", "force quit firefox", why="you asked me to remember it")
    jv.j.intents.learn("ask claude why it didn't work", "force quit firefox")
    jv.claude.reply("{}")
    jv.claude.reply(json.dumps({"ask claude why it didn't work": "none"}))
    teaching.run(jv.j, CFG)
    assert learned.load()["wordings"] == {}
    assert jv.j.intents.early("ask claude why it didn't work") != "force quit firefox"


def test_what_you_saved_is_rechecked_first_and_a_yes_is_not_asked_again(jv):
    learned.remember("turn the sound down a bit", "volume down", why="a new way of saying it")
    learned.remember(LOUD, "volume down", why="you asked me to remember it")
    old, _ = teaching.to_recheck(teaching._state(), {"review": 1})
    assert old == [(LOUD, "volume down")]
    jv.claude.reply("{}")
    jv.claude.reply(json.dumps({LOUD: "yes", "turn the sound down a bit": "yes"}))
    teaching.run(jv.j, CFG)
    assert learned.load()["wordings"][LOUD] == "volume down"
    assert teaching.to_recheck(teaching._state(), {})[0] == []
