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


def test_a_phrase_with_its_blank_left_in_is_never_run_or_learned(jv):
    # "find the offer letter mail" -> Claude sent the pattern "did <name> email me": JARVIS looked for mail from
    # "<name>", said "Nothing from <name> this week", and learned that wording.
    from core import learned
    jv.claude.reply({"do": ["did <name> email me"]})
    jv.do("ask claude to fetch the offer letter mail")
    assert jv.says("couldn't actually do that") and not jv.says("<name>")
    assert "did <name> email me" not in learned.load()["wordings"].values()
    assert not learned.worth_learning("find the offer letter mail", "did <name> email me", learned.load())
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


# --- Claude Code's summaries come in on time ---

def test_a_terminal_summary_is_said_at_once(jv):
    jv.j._message({"say": "All done. The tests pass.", "why": "Claude Code finished a reply."})
    assert jv.says("The tests pass")
    assert jv.j.inbox.empty()  # not waiting behind the request being handled


def test_a_card_or_phrase_still_goes_to_the_main_loop(jv):
    jv.j._message({"do": "open youtube"})
    assert jv.j.inbox.get_nowait() == ("ctl", {"do": "open youtube"})


# --- several requests in one go ---

def test_sentences_and_counting_are_split(jv):
    assert jv.j._split("Open YouTube. Turn the volume down.") == ["Open YouTube", "Turn the volume down"]
    assert jv.j._split("First open youtube, then turn the volume down") == ["open youtube", "turn the volume down"]
    assert jv.j._split("One, open youtube. Two, volume up.") == ["open youtube", "volume up"]


def test_known_parts_dont_wait_for_claude(jv):
    # One part needs Claude: the others are still done by JARVIS itself, and Claude only gets its own part.
    assert jv.j._split("Open YouTube. Then what's a good name for a cat?") == [
        "Open YouTube", "what's a good name for a cat?"]
    jv.do("Open YouTube. Then what's a good name for a cat?")
    asked = jv.claude.asked()
    assert len(asked) == 1 and "cat" in asked[0] and "User now says: Open YouTube" not in asked[0]
    assert jv.shell.ran("youtube")


def test_lists_stay_whole(jv):
    assert jv.j._split("add bread and milk to my list") is None
    assert jv.j._split("play Mr. Bean on youtube") is None


def test_a_then_chain_with_an_unknown_part_stays_one_plan(jv):
    assert jv.j._split("work out a sum, then delete old.txt please") is None


# --- Claude thinks in the background: what you say meanwhile isn't kept waiting ---

import threading  # noqa: E402


def _slow_claude(jv):
    """Claude answers only once the returned event is set (the fake answers at once otherwise)."""
    gate, fast = threading.Event(), jv.j.claude.ask

    def ask(*a, **k):
        gate.wait(5)
        return fast(*a, **k)
    jv.j.claude.ask = ask
    jv.j.background = True
    return gate


def _ask(jv, text):
    jv.j.requests += 1  # (as the main loop counts each request it takes)
    jv.j.handle(text)


def _answer_comes_in(jv):
    kind, job = jv.j.inbox.get(timeout=5)
    assert kind == "claude"
    jv.j._claude_busy -= 1
    jv.j._claude_done(job)
    return job


def test_a_request_while_claude_thinks_is_done_at_once(jv):
    gate = _slow_claude(jv)
    jv.claude.reply({"say": "How about Whiskers?"})
    _ask(jv, "ask claude what's a good name for a cat")
    assert jv.j._claude_busy == 1
    _ask(jv, "open youtube")  # not stuck behind Claude
    assert jv.shell.ran("youtube")
    gate.set()
    _answer_comes_in(jv)
    assert jv.says("On your earlier question: How about Whiskers?")  # you moved on: it says which one it answers
    assert jv.j._claude_busy == 0


def test_an_answer_with_nothing_in_between_has_no_preamble(jv):
    gate = _slow_claude(jv)
    jv.claude.reply({"say": "How about Whiskers?"})
    _ask(jv, "ask claude what's a good name for a cat")
    gate.set()
    _answer_comes_in(jv)
    assert jv.says("How about Whiskers?") and not jv.says("earlier question")


def test_a_second_question_waits_its_turn(jv):
    gate = _slow_claude(jv)
    jv.claude.reply({"say": "Whiskers."}).reply({"say": "Rex."})
    _ask(jv, "ask claude for a cat name")
    _ask(jv, "ask claude for a dog name")
    assert jv.says("once Claude's done with the last one")
    gate.set()
    assert _answer_comes_in(jv)["text"].endswith("cat name")
    assert _answer_comes_in(jv)["text"].endswith("dog name")  # in the order asked, one at a time


def test_stop_drops_the_questions_waiting(jv):
    gate = _slow_claude(jv)
    _ask(jv, "ask claude for a cat name")
    _ask(jv, "ask claude for a dog name")
    jv.j.stop_claude()
    gate.set()
    _answer_comes_in(jv)
    _answer_comes_in(jv)
    assert len(jv.claude.asked()) <= 1  # the waiting one never went to Claude
    assert jv.j._claude_busy == 0


def test_the_orb_shows_claude_while_it_thinks(jv):
    gate = _slow_claude(jv)
    _ask(jv, "ask claude for a cat name")
    jv.j.state("idle")
    assert ("set_state", ("claude",)) in jv.ui.calls
    gate.set()
    _answer_comes_in(jv)
