"""A new request said while JARVIS waits for a yes or no: done next, instead of being read as "no" and lost."""
import queue

import pytest

JUNK = "wibble wobble flurb"


def queued(jv):
    try:
        return jv.j.inbox.get_nowait()
    except queue.Empty:
        return None


def test_a_new_request_is_not_taken_for_an_answer(jv):
    jv.answers.append("Open hot star.")
    assert jv.j.ask("Shall I have Claude fix that?") is False
    assert queued(jv) == ("again", "Open hot star.") and jv.j.interrupted


@pytest.mark.parametrize("answer, yes", [("yes please", True), ("no thanks", False), ("not now", False),
                                         ("nope", False), ("cancel", False)])
def test_real_answers_stay_answers(jv, answer, yes):
    jv.answers.append(answer)
    assert jv.j.ask("Shall I have Claude fix that?") is yes
    assert queued(jv) is None and not jv.j.interrupted


def test_saying_its_verb_back_is_still_a_yes(jv):
    jv.answers.append("close it")
    assert jv.j.ask("Close discord?") is True and queued(jv) is None


def test_something_that_is_neither_stays_a_no(jv):
    jv.answers.append(JUNK)
    assert jv.j.ask("Shall I have Claude fix that?") is False and queued(jv) is None


def unsure_about(jv, monkeypatch):
    monkeypatch.setattr(jv.j.intents, "candidates", lambda text: [("open new chat on claude", 0.67)])
    monkeypatch.setattr(jv.j.intents, "match", lambda text: (None, 0.0))
    monkeypatch.setattr(jv.j.intents, "left", lambda text: False)


def test_the_old_request_is_dropped_not_sent_to_claude(jv, monkeypatch):
    """Seen live: "Did you mean ...?", then "Open hot star" made Claude answer the request before it."""
    unsure_about(jv, monkeypatch)
    jv.do(JUNK, "open hot star")
    assert not jv.claude.calls and queued(jv) == ("again", "open hot star")


def test_with_no_answer_the_request_still_goes_to_claude(jv, monkeypatch):
    unsure_about(jv, monkeypatch)
    jv.claude.reply("Sure.")
    jv.do(JUNK)
    assert jv.claude.calls
