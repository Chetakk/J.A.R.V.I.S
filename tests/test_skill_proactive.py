"""What JARVIS brings up by itself (skills/proactive.py): every heads-up keeps its reason, muted kinds stay
quiet (the ones that matter can't be muted), focus mode holds them for later, quiet hours by voice."""
import time

import pytest


@pytest.fixture
def p(jv):
    jv.j.settings["quiet_hours"] = {"enabled": False}  # (the test shouldn't depend on the time of day)
    return jv.j.skills.proactive


def said_later(jv):
    out = []
    while not jv.j.inbox.empty():
        kind, line = jv.j.inbox.get_nowait()
        if kind == "say":
            out.append(line)
    return out


def test_a_heads_up_is_said_and_its_reason_kept(jv, p):
    assert p.tell("cpu", "Heads up, the CPU has been at 95 percent for a minute.", "CPU over 90 percent for 60 s.")
    assert said_later(jv) == ["Heads up, the CPU has been at 95 percent for a minute."]
    jv.j.recent.append("JARVIS said: Heads up, the CPU has been at 95 percent for a minute.")
    jv.do("why did you say that")
    assert jv.said == ["CPU over 90 percent for 60 s."]


def test_why_after_an_ordinary_answer_goes_to_claude(jv, p):
    p.tell("cpu", "Heads up, the CPU is busy.", "CPU over 90 percent.")
    jv.j.recent.append("JARVIS said: Paris is the capital of France.")
    jv.do("why did you say that")
    assert jv.claude.calls and not jv.says("CPU over 90 percent")


def test_mute_a_kind(jv, p):
    jv.do("don't warn me about the cpu again")
    assert jv.says("Okay, no more CPU alerts.")
    assert not p.tell("cpu", "Heads up, CPU.", "why") and said_later(jv) == []
    jv.do("turn the cpu alerts back on")
    assert p.tell("cpu", "Heads up, CPU.", "why")


def test_the_ones_that_matter_cant_be_muted(jv, p):
    p.tell("health", "I can't hear the microphone.", "No sound from the mic for 90 s.")
    jv.j.recent.append("JARVIS said: I can't hear the microphone.")
    jv.do("don't warn me about that again")  # "that" = the last heads-up: the mic
    assert jv.says("I'll keep telling you about that one")
    assert not jv.j.memory.pref("muted_alerts")


def test_focus_holds_heads_ups_then_says_them_after(jv, p):
    jv.do("focus for 25 minutes")
    assert not p.tell("disk", "Heads up, the disk is 97 percent full.", "Disk over 95 percent.")
    assert said_later(jv) == []
    jv.do("stop focusing")
    p.watch()
    assert said_later(jv) == ["While you were focusing: The disk is 97 percent full."]


def test_things_you_asked_for_are_said_even_while_focusing(jv, p):
    jv.do("focus for 25 minutes")
    assert p.tell("job", "Your build has finished.", "You asked me to.", hold=False)
    assert said_later(jv) == ["Your build has finished."]


def test_recent_alerts(jv, p):
    p.tell("cpu", "Heads up, CPU busy.", "CPU over 90.")
    p.tell("disk", "Heads up, disk full.", "Disk over 95.")
    jv.do("show my recent alerts")
    assert jv.says("2 things today", "The latest: Heads up, disk full.") and "CPU over 90." in jv.card_text()


def test_quiet_hours_by_voice(jv, p):
    jv.do("quiet hours from 10 pm to 6 am")
    assert jv.says("Quiet hours are now 10 pm to 6 am.")
    assert jv.j.memory.pref("quiet_hours")["start"] == 22 and jv.j.memory.pref("quiet_hours")["end"] == 6
    jv.do("turn off quiet hours")
    assert jv.says("Quiet hours are off.")


# --- failure cases ---

def test_why_with_nothing_brought_up(jv, p):
    jv.do("why did that alert come up")
    assert jv.said == ["I haven't brought anything up in the last hour."]


def test_recent_with_none(jv, p):
    jv.do("show my recent alerts")
    assert jv.said == ["I haven't brought anything up today."]


def test_quiet_hours_that_dont_make_sense(jv, p):
    jv.do("quiet hours from 10 pm to 10 pm")
    assert jv.says("Sorry, I didn't get those hours.")


def test_tell_me_when_with_nothing_running(jv, p, monkeypatch):
    from system import jobs
    monkeypatch.setattr(jobs, "running", lambda: [])
    jv.do("tell me when it finishes")
    assert jv.said == ["Nothing's running in a terminal right now."]
