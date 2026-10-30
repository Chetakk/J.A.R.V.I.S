"""Teaching itself (core/teaching.py): Claude writes other wordings, JARVIS keeps only the safe ones.

Nothing here reaches Claude or the desktop: the harness answers for Claude, and checking a wording only
ever matches patterns, never runs a handler.
"""
import json
import os
import re

from core import learned, teaching

CFG = {"phrases": 2, "wordings": 4, "misses": 4}


def misses(*lines):
    """Write what JARVIS missed, as the miss log holds it."""
    from core import misslog
    os.makedirs(os.path.dirname(misslog.PATH), exist_ok=True)
    with open(misslog.PATH, "w", encoding="utf-8") as f:
        for text in lines:
            f.write(json.dumps({"time": "2026-10-30 10:00", "text": text, "outcome": "claude"}) + "\n")


def test_a_wording_claude_wrote_is_kept_and_works_at_once(jv):
    kept, refused = teaching.teach(jv.j, [("my ears are hurting from this", "volume down")])
    assert kept == [("my ears are hurting from this", "volume down")] and not refused
    assert jv.j.intents.early("my ears are hurting from this") == "volume down"  # the running matcher
    assert learned.load()["wordings"]["my ears are hurting from this"] == "volume down"  # and kept for next time
    jv.do("my ears are hurting from this")
    assert not jv.claude.asked()  # it never has to ask Claude again


def test_a_wording_a_skill_already_takes_is_refused(jv):
    """"What's on my clipboard" is a skill's own phrase: teaching it to mean something else would break it."""
    assert teaching.claimed(jv.j, "what's on my clipboard")
    kept, refused = teaching.teach(jv.j, [("what's on my clipboard", "volume down")])
    assert not kept and "already takes it" in refused[0][2]


def test_a_wording_that_already_means_something_else_is_refused(jv):
    kept, refused = teaching.teach(jv.j, [("put that on the clipboard", "volume down")])
    assert not kept and "already means" in refused[0][2]  # it is config/intents.yaml's own wording for it


def test_a_wording_the_matcher_only_nearly_agrees_with_is_still_kept(jv):
    """A small model scores by topic, not direction: "too loud" sits as close to volume up as to volume
    down. That isn't enough to overrule Claude, or JARVIS could never be taught either way round."""
    assert jv.j.intents.candidates("the sound is way too loud for me")[0][0] == "volume up"
    kept, _ = teaching.teach(jv.j, [("the sound is way too loud for me", "volume down")])
    assert kept and jv.j.intents.early("the sound is way too loud for me") == "volume down"


def test_a_catch_all_pattern_does_not_count_as_owning_the_words(jv):
    """A skill pattern with a value in it matches all sorts of sentences and hands most of them back, so
    it owns nothing; only a pattern made of the words themselves does."""
    assert teaching.claimed(jv.j, "my ears are hurting from this") is None


def test_a_wording_with_a_value_in_it_is_refused(jv):
    kept, refused = teaching.teach(jv.j, [("put it at 40 for me", "volume down")])
    assert not kept and "not worth learning" in refused[0][2]


def test_a_vague_wording_is_dropped_before_it_is_even_checked(jv):
    assert teaching.tidy("volume down", ["turn it down a bit", "do it", "that's enough", "turn it down a bit"],
                         teaching.known(jv.j.intents)) == ["turn it down a bit"]


def test_only_the_misses_still_not_understood_are_taken_to_claude(jv):
    misses("the sound is way too loud for me", "turn the volume down", "the sound is way too loud for me")
    have = teaching.known(jv.j.intents)
    assert teaching.missed(have) == ["the sound is way too loud for me"]  # the known one is left out
    learned.remember("the sound is way too loud for me", "volume down")
    assert teaching.missed(have) == []  # and one already learned isn't asked about twice


def test_claude_says_what_a_miss_meant_and_it_is_learned(jv):
    misses("the sound is way too loud for me")
    jv.claude.reply(json.dumps({"the sound is way too loud for me": "volume down"}))  # the misses question
    jv.claude.reply("{}")  # the wordings question: nothing worth adding
    assert "learned 1 of 1" in teaching.run(jv.j, CFG)
    assert jv.j.intents.early("the sound is way too loud for me") == "volume down"


def test_claude_saying_it_meant_none_of_them_teaches_nothing(jv):
    misses("book me a table for two at the thai place")
    jv.claude.reply("none of these mean anything JARVIS does, so: {}")
    jv.claude.reply("{}")
    assert teaching.run(jv.j, CFG) == ""
    assert learned.load()["wordings"] == {}


def test_a_phrase_claude_invents_is_ignored(jv):
    """It may only map onto JARVIS's own phrases; anything else it answers with is dropped."""
    misses("the sound is way too loud for me")
    jv.claude.reply(json.dumps({"the sound is way too loud for me": "lower the speaker gain"}))
    jv.claude.reply("{}")
    assert teaching.run(jv.j, CFG) == ""
    assert learned.load()["wordings"] == {}


def test_wordings_for_a_phrase_with_none_are_asked_for_and_kept(jv):
    phrase = teaching.gaps(teaching.known(jv.j.intents))[0][0][1]  # the first phrase with no wordings
    jv.claude.reply(json.dumps({phrase: ["the way i would really say that out loud"]}))  # nothing missed,
    # so the misses question isn't asked at all and this answers the wordings one
    line = teaching.run(jv.j, CFG)
    assert "1 new wordings for 1 phrases" in line
    assert jv.j.intents.early("the way i would really say that out loud") == phrase
    assert jv.claude.asked(re.escape(phrase))  # it was asked about that phrase, not another


def test_what_it_taught_itself_is_written_down(jv):
    teaching.teach(jv.j, [("my ears are hurting from this", "volume down")], "a phrase with no wordings")
    with open(teaching.TAUGHT, encoding="utf-8") as f:
        row = json.loads(f.read().strip())
    assert row["said"] == "my ears are hurting from this" and row["means"] == "volume down"
    assert row["from"] == "a phrase with no wordings"


def test_claude_answering_with_prose_around_the_json_is_still_read():
    assert teaching.parse('Sure! Here you go:\n```json\n{"a": ["b"]}\n```\nHope that helps.', ["a"]) == {"a": ["b"]}
    assert teaching.parse("I couldn't do that.", ["a"]) == {}


def test_a_phrase_needing_a_slot_template_is_not_asked_about(jv):
    plain, templated = teaching.gaps(teaching.known(jv.j.intents))
    assert all("<" not in p and not any(c.isdigit() for c in p) for _, p in plain)
    assert ("focus_mode", "focus for 45 minutes") in templated
