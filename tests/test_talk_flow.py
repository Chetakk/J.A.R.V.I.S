"""How a conversation with Claude through JARVIS sounds: one voice per answer, no false claims, nothing left silent."""
from claude.bridge import _early_say


# --- what is spoken while Claude's answer is still streaming in ---

def test_early_say_waits_for_the_whole_line():
    assert _early_say('{"say": "Here are your') is None
    assert _early_say('{"say": "Here are your to-dos.", "det') == "Here are your to-dos."


def test_early_say_skips_a_nested_say():
    # A "suggest" (or a card, or a task step) before the answer's own line has a "say" too: not the answer.
    block = '{"suggest": {"say": "Want a shortcut?"}, "say": "It is 5 pm."'
    assert _early_say(block) == "It is 5 pm."
    assert _early_say('{"suggest": {"say": "Want a shortcut?"}') is None


def test_early_say_waits_when_a_do_phrase_comes_first():
    assert _early_say('{"do": ["what\'s on my todo list"], "say": "Here are your four to-dos."') is None
    assert _early_say('{"do": [], "say": "Hi."') == "Hi."


def test_brackets_inside_strings_dont_count():
    assert _early_say('{"details": "a {b} [c", "say": "Fine."') == "Fine."


# --- Claude's line and the "do" phrase's own reply ---

def test_one_voice_when_claude_uses_a_phrase(jv):
    # Claude guessed "four"; the phrase's own reply is the real answer, so only that is said.
    jv.claude.reply({"do": ["what time is it"], "say": "Here are your four to-dos."})
    jv.do("ask claude to show me the time")
    assert not jv.says("four to-dos")
    assert jv.said, "the phrase's own reply"


def test_a_mishearing_note_is_still_said(jv):
    jv.claude.reply({"do": ["what time is it"], "say": "I heard dime, I think you meant time.",
                     "misheard": {"heard": "dime", "meant": "time"}})
    jv.do("ask claude what dime is it")
    assert jv.says("I think you meant time")


def test_a_phrase_that_matches_nothing_is_owned_up_to(jv):
    jv.claude.reply({"say": "Moving it into Projects.", "do": ["frobnicate the zorblax into projects"]})
    jv.do("ask claude to move my process engineer folder to projects")
    assert jv.says("couldn't actually do that")
    assert any("couldn't do" in line for line in jv.j.recent)  # Claude hears about it next time


# --- "Sorry about that." after you talked over JARVIS ---

def _speaker():
    import collections
    import time
    from voice.speech import Speaker
    s = Speaker.__new__(Speaker)
    s.enabled, s.muted, s.brief, s.on_say, s.last = False, False, False, None, ""
    s.quiet = lambda: False
    s.history = collections.deque(maxlen=12)
    s._interrupted = time.time()  # you just talked over it
    return s


def test_the_apology_skips_thinking_and_goes_on_the_answer():
    s = _speaker()
    s.say("Thinking.")
    assert s.last == "Thinking."
    s.say("Bios is the firmware settings screen.")
    assert s.last == "Sorry about that. Bios is the firmware settings screen."
    s.say("Anything else?")
    assert s.last == "Anything else?"  # once


# --- short answers in a follow-up ---

def test_no_no_no_in_a_follow_up_is_answered(jv):
    jv.j.handle("No no no no no.", follow_up=True)
    assert jv.says("leave it") or jv.says("leaving it")
    assert not jv.j.ignored


def test_yeah_no_is_a_no(jv):
    jv.j.handle("Yeah, no.", follow_up=True)
    assert jv.said


def test_mm_is_still_quiet_noise(jv):
    jv.j.handle("Mm.", follow_up=True)
    assert not jv.said and jv.j.ignored
