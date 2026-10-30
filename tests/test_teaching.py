"""Teaching itself (core/teaching.py): Claude says what things mean and writes wordings, JARVIS checks them and
learns them without asking.

Nothing here reaches Claude or the desktop: the harness answers for Claude, and checking a wording only
ever matches patterns and compares sentences, never runs a handler.
"""
import json
import os
import re

from core import learned, misslog, outcomes, teaching

CFG = {"phrases": 2, "wordings": 4, "misses": 4}


def misses(*lines, outcome="claude"):
    """Write what JARVIS missed, as the miss log holds it."""
    os.makedirs(os.path.dirname(misslog.PATH), exist_ok=True)
    with open(misslog.PATH, "a", encoding="utf-8") as f:
        for text in lines:
            f.write(json.dumps({"time": "2026-10-30 10:00", "text": text, "outcome": outcome}) + "\n")


def said(how, text="", phrase="", skill="", score=0.0, times=1):
    for _ in range(times):
        outcomes.log(how, text, phrase, skill, score)


def wording_questions(jv):
    return jv.claude.asked("other ways someone would say")


def place_questions(jv):
    return jv.claude.asked("which single phrase")


# --- what's kept and what isn't

def test_a_wording_claude_wrote_is_kept_and_works_at_once(jv):
    kept, refused = teaching.teach(jv.j, [("my ears are hurting from this", "volume down")])
    assert kept == [("my ears are hurting from this", "volume down")] and not refused
    assert jv.j.intents.early("my ears are hurting from this") == "volume down"  # the running matcher
    assert learned.load()["wordings"]["my ears are hurting from this"] == "volume down"  # and kept for next time
    jv.do("my ears are hurting from this")
    assert not jv.claude.asked()  # it never has to ask Claude again


def test_a_whole_sentence_can_be_learned(jv):
    sentence = "i'm going to be on a call for a bit so turn the sound down"
    assert teaching.teach(jv.j, [(sentence, "volume down")])[0]


def test_a_skills_own_words_are_refused(jv):
    """"What's on my clipboard" is a skill's own phrase: teaching it to mean something else would break it."""
    assert teaching.claimed(jv.j, "what's on my clipboard")
    kept, refused = teaching.teach(jv.j, [("what's on my clipboard", "volume down")])
    assert not kept and "already takes it" in refused[0][2]


def test_a_pattern_with_a_word_or_two_in_a_group_still_owns_its_words(jv):
    """A group holding one word of the phrase isn't a value; a group holding most of the sentence is."""
    skill = jv.j.skills.all[0]
    skill.rules = [(re.compile(r"^(play|start) the whole thing right now$"), None),
                   (re.compile(r"^(.+) from (.+)$"), None)] + skill.rules
    assert teaching.claimed(jv.j, "play the whole thing right now")
    assert teaching.claimed(jv.j, "my ears are hurting from this") is None


def test_a_catch_all_pattern_does_not_count_as_owning_the_words(jv):
    """A pattern that's mostly a value matches all sorts of sentences and hands most of them back."""
    assert teaching.claimed(jv.j, "my ears are hurting from this") is None


def test_a_wording_that_clearly_means_something_else_is_refused(jv):
    kept, refused = teaching.teach(jv.j, [("put that on the clipboard", "volume down")])
    assert not kept and "already means" in refused[0][2]  # it is config/intents.yaml's own wording for it


def test_a_wording_the_matcher_only_nearly_agrees_with_is_still_kept(jv):
    """A small model scores by topic, not direction: "too loud" sits as close to volume up as to volume
    down. That isn't enough to overrule Claude, or JARVIS could never be taught either way round."""
    assert jv.j.intents.candidates("the sound is way too loud for me")[0][0] == "volume up"
    kept, _ = teaching.teach(jv.j, [("the sound is way too loud for me", "volume down")])
    assert kept and jv.j.intents.early("the sound is way too loud for me") == "volume down"


def test_a_wording_going_the_other_way_is_refused(jv):
    kept, refused = teaching.teach(jv.j, [("turn the volume up a lot", "volume down")])
    assert not kept and "other way" in refused[0][2]


def test_a_wording_that_would_pull_a_known_sentence_away_is_refused(jv):
    """"More detail" goes to Claude; a wording like this one took it away once, when it was written by hand."""
    assert teaching.pulls(jv.j, "go into more detail", "give me detailed answers")
    kept, refused = teaching.teach(jv.j, [("go into more detail", "give me detailed answers")])
    assert not kept and "pull" in refused[0][2]


def test_a_wording_that_would_only_nearly_catch_a_request_for_claude_is_refused_too(jv):
    """Not close enough to run it, but close enough for "did you mean...?" to stop it reaching Claude."""
    assert teaching.pulls(jv.j, "explain things in more detail", "give me detailed answers") == "more detail"


def test_a_number_or_a_secret_is_never_learned(jv):
    for wording in ("put it at 40 for me", "the wifi password is under the router"):
        kept, refused = teaching.teach(jv.j, [(wording, "volume down")])
        assert not kept and "not worth learning" in refused[0][2]


def test_a_vague_wording_is_dropped_before_it_is_even_checked(jv):
    assert teaching.tidy("volume down", ["turn it down a bit", "do it", "that's enough", "turn it down a bit"],
                         teaching.known(jv.j.intents)) == ["turn it down a bit"]


# --- what you got wrong: Claude says what you meant

def test_only_what_is_still_not_understood_is_taken_to_claude(jv):
    misses("the sound is way too loud for me", "turn the volume down", "the sound is way too loud for me")
    have = teaching.known(jv.j.intents)
    assert teaching.missed(have) == ["the sound is way too loud for me"]  # the known one is left out
    learned.remember("the sound is way too loud for me", "volume down")
    assert teaching.missed(have) == []  # and one already learned isn't asked about twice


def test_what_system_mode_kept_from_claude_stays_kept_from_it(jv):
    """A miss in system mode never went to Claude on purpose; noise and Claude's own "do" phrases aren't yours."""
    misses("the quiet thing i said in system mode", outcome="unknown")
    misses("just some noise here", outcome="unclear")
    misses("a phrase claude made up", outcome="claude-do")
    assert teaching.missed(teaching.known(jv.j.intents)) == []


def test_a_number_or_a_secret_in_a_miss_never_reaches_claude(jv):
    misses("the door code is one two three", "call the number 555 1234", "the sound is way too loud for me")
    jv.claude.reply(json.dumps({"the sound is way too loud for me": "volume down"}))
    teaching.run(jv.j, CFG)
    asked = place_questions(jv)[0]
    assert "too loud" in asked and "door code" not in asked and "555" not in asked


def test_claude_says_what_a_miss_meant_and_it_is_learned(jv):
    misses("the sound is way too loud for me")
    jv.claude.reply(json.dumps({"the sound is way too loud for me": "volume down"}))  # the misses question
    jv.claude.reply("{}")  # the wordings question: nothing worth adding
    assert "placed 1 of 1" in teaching.run(jv.j, CFG)
    assert jv.j.intents.early("the sound is way too loud for me") == "volume down"


def test_a_miss_claude_said_means_nothing_is_not_asked_about_again(jv):
    misses("book me a table for two at the thai place")
    jv.claude.reply("none of these mean anything JARVIS does, so: {}")
    jv.claude.reply("{}")
    assert teaching.run(jv.j, CFG) == ""
    assert learned.load()["wordings"] == {}
    n = len(place_questions(jv))
    jv.claude.reply("{}")
    teaching.run(jv.j, CFG)
    assert len(place_questions(jv)) == n  # "none" stays none: no question about it every three hours


def test_a_phrase_claude_invents_is_ignored(jv):
    """It may only map onto JARVIS's own phrases; anything else it answers with is dropped."""
    misses("the sound is way too loud for me")
    jv.claude.reply(json.dumps({"the sound is way too loud for me": "lower the speaker gain"}))
    jv.claude.reply("{}")
    assert teaching.run(jv.j, CFG) == ""
    assert learned.load()["wordings"] == {}


def test_a_match_that_only_just_got_there_goes_to_claude_not_straight_in(jv):
    """It may have been the wrong match. Learning it as it stands would make that mistake permanent."""
    said("meaning", "bring the noise down a little", "volume down", score=jv.j.intents.threshold + 0.01)
    said("meaning", "turn the sound down for me", "volume down", score=0.95)  # sure of this one: nothing to check
    jv.claude.reply("{}")
    jv.claude.reply("{}")
    teaching.run(jv.j, CFG)
    asked = place_questions(jv)[0].split("JARVIS's own phrases")[0]
    assert "bring the noise down a little" in asked and "turn the sound down for me" not in asked
    assert learned.load()["wordings"] == {}  # Claude said nothing, so nothing was learned


# --- what you got right: Claude writes other ways of saying it

def test_a_phrase_you_use_is_asked_about_first_with_how_you_say_it(jv):
    said("wording", "make it louder for me", "volume up", times=3)
    jv.claude.reply(json.dumps({"volume up": {"wordings": ["i want it louder than this"], "enough": False}}))
    teaching.run(jv.j, CFG)
    asked = wording_questions(jv)[0]
    assert re.search(r"- volume up  \(knows: .*they said: make it louder for me", asked)
    assert jv.j.intents.early("i want it louder than this") == "volume up"


def test_what_a_skills_own_words_took_is_taught_too(jv):
    """It worked, but next time you might say it differently."""
    jv.do("where was i")
    jv.claude.reply(json.dumps({"where was i": {"wordings": ["what was i up to before this"], "enough": False}}))
    teaching.run(jv.j, CFG)
    assert "- where was i" in wording_questions(jv)[0]
    assert jv.j.intents.early("what was i up to before this") == "where was i"


def test_a_phrase_with_no_wordings_comes_before_one_with_plenty(jv):
    want = teaching.wanted(jv.j, teaching._state(), [], {"phrases": 1})
    plain = teaching.gaps(teaching.known(jv.j.intents))[0]
    assert plain and want[0] not in ("what's playing", "volume up")
    assert not [w for w, t in jv.j.intents.exact.items() if t == want[0]]


def test_a_phrase_claude_says_has_enough_is_left_alone(jv):
    phrase = teaching.wanted(jv.j, teaching._state(), [], {"phrases": 1})[0]
    jv.claude.reply(json.dumps({phrase: {"wordings": [], "enough": True}}))
    teaching.run(jv.j, dict(CFG, phrases=1))
    assert phrase not in teaching.wanted(jv.j, teaching._state(), [], {"phrases": 500})


def test_every_phrase_gets_its_turn(jv):
    """Claude not saying "enough" mustn't mean the same phrases are asked about for ever."""
    first = teaching.wanted(jv.j, teaching._state(), [], {"phrases": 2})
    jv.claude.reply("{}")
    teaching.run(jv.j, CFG)
    assert not set(first) & set(teaching.wanted(jv.j, teaching._state(), [], {"phrases": 2}))


def test_a_phrase_already_holding_wordings_is_not_taken_for_one_with_none(jv):
    """What made the same phrases come round every pass: the phrases themselves weren't counted as known."""
    have = teaching.known(jv.j.intents)
    assert not [p for _, p in teaching.gaps(have)[0] if p in ("read it to me", "volume up")]


def test_claude_answering_the_old_way_or_with_prose_is_still_read(jv):
    assert teaching.parse('Sure! Here you go:\n```json\n{"a": ["b"]}\n```\nHope that helps.', ["a"]) == {"a": ["b"]}
    assert teaching.parse("I couldn't do that.", ["a"]) == {}
    says, enough = teaching.ask_wordings(lambda p, s: '{"a": ["one"], "b": {"wordings": ["two"], "enough": true}}', ["a", "b"])
    assert says == {"a": ["one"], "b": ["two"]} and enough == {"b"}


def test_a_phrase_needing_a_slot_template_is_not_asked_about(jv):
    plain, templated = teaching.gaps(teaching.known(jv.j.intents))
    assert all("<" not in p and not any(c.isdigit() for c in p) for _, p in plain)
    assert ("focus_mode", "focus for 45 minutes") in templated
    assert "focus for 45 minutes" not in teaching.wanted(jv.j, teaching._state(), [], {"phrases": 500})


# --- it learns without asking

def test_learning_is_silent(jv):
    misses("the sound is way too loud for me")
    jv.claude.reply(json.dumps({"the sound is way too loud for me": "volume down"}))
    jv.claude.reply("{}")
    jv.clear()
    assert teaching.run(jv.j, CFG)
    assert not jv.spoken and not jv.questions


def test_what_claude_turned_a_request_into_is_learned_without_asking(jv):
    assert jv.j.settings["learn"] == "quiet"
    jv.j._learn("the sound is hurting my head", "volume down")
    assert not jv.questions and not jv.spoken
    assert jv.j.intents.early("the sound is hurting my head") == "volume down"


def test_restarting_does_not_mean_asking_again(jv):
    assert teaching.due_in({"every": 3}) == 0  # never run
    jv.claude.reply("{}")
    teaching.run(jv.j, CFG)
    assert teaching.due_in({"every": 3}) > 3 * 3600 - 60  # read back from the file, as after a restart


# --- what's kept about what you said

def rows():
    return outcomes.read()


def test_a_request_handled_by_its_own_words_is_recorded(jv):
    jv.do("where was i")
    assert [(r["how"], r["text"], r["phrase"]) for r in rows()] == [("pattern", "where was i", "where was i")]


def test_a_request_with_a_value_keeps_no_words(jv):
    jv.do("remember that i parked on the third floor")
    written = open(outcomes.PATH, encoding="utf-8").read()
    assert "parked" not in written and "Notes" in written  # only that the skill was used


def test_a_secret_or_a_private_note_keeps_nothing(jv):
    jv.do("remember privately that i owe sam money")
    jv.do("my locker code is 4512")
    written = open(outcomes.PATH, encoding="utf-8").read() if os.path.exists(outcomes.PATH) else ""
    assert "4512" not in written and "locker" not in written and "sam" not in written


def test_a_rewritten_request_counts_once(jv):
    """Said once, however many times JARVIS handles it on the way."""
    jv.do("make it louder")
    assert [r["how"] for r in rows()] == ["wording"] and rows()[0]["phrase"] == "volume up"


def test_the_log_can_be_turned_off(jv):
    jv.j.settings["teaching"] = {"log": False}
    jv.do("where was i")
    assert rows() == []


def test_the_log_is_kept_short(jv, monkeypatch):
    monkeypatch.setattr(outcomes, "MAX", 3)
    said("pattern", skill="Notes", times=5)
    outcomes.trim()
    assert len(rows()) == 3


# --- "did you learn anything new?"

def test_asked_what_it_learned_it_says_and_shows_it(jv):
    learned.remember("my ears are hurting from this", "volume down", why="a new way of saying it")
    learned.remember("the sound is hurting my head", "volume down", why="something you said")
    jv.do("did you learn anything new")
    assert jv.says("2 new ways of saying 1 thing")
    card = jv.ui.cards[-1]
    assert "my ears are hurting from this" in card["text"] and "volume down" in card["text"]


def test_any_way_of_asking_is_understood(jv):
    learned.remember("my ears are hurting from this", "volume down")
    for question in ("did you learn any new phrase", "have you learned any new sentences", "what have you learned today",
                     "what new phrases do you know", "any new phrases", "tell me what you've learned"):
        jv.clear()
        jv.j.memory.set_pref("learned_asked", None)
        jv.do(question)
        assert jv.says("1 new way of saying 1 thing"), question
        assert not jv.claude.asked()


def test_only_whats_new_since_you_last_asked(jv):
    learned.remember("my ears are hurting from this", "volume down")
    jv.do("did you learn anything new")
    jv.clear()
    jv.do("did you learn anything new")
    assert jv.says("Nothing new since you last asked")


def test_nothing_learned_yet(jv):
    jv.do("did you learn anything new")
    assert jv.says("haven't learned anything new yet")
