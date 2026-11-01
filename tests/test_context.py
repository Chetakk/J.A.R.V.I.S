"""Follow-ups about what JARVIS just did (core/context.py): "a bit more", "too much", "and firefox too",
"the other one". Pure: nothing runs."""
import pytest

from core import learned
from core.context import Context


def after(text, value=None, choice=None):
    c = Context()
    c.done(text, value)
    if choice:
        c.chose(*choice)
    return c


@pytest.mark.parametrize("text,said,meant", [
    ("volume up", "a bit more", "volume up"),
    ("volume up", "too much", "volume down"),
    ("set volume to 40", "make it 70", "set volume to 70"),
    ("close chrome", "and firefox too", "close firefox"),
    ("close chrome", "and close firefox", "close firefox"),
    ("open chrome", "now open youtube", "open youtube"),
])
def test_a_follow_up_means(text, said, meant):
    value = "40" if "40" in text else ("chrome" if "chrome" in text else None)
    assert after(text, value).follow_up(said) == meant


@pytest.mark.parametrize("text,said", [
    ("shut down", "more"),  # "down" is a step word, but shutting down isn't a step
    ("log out", "one more time"),
    ("log in", "too much"),  # not "log out"
    ("open day tracker in vs code", "again please"),
])
def test_one_off_actions_are_not_repeated_or_reversed(text, said):
    assert after(text).follow_up(said) is None


@pytest.mark.parametrize("said", ["and mute", "also play music", "now what time is it"])
def test_a_new_request_after_and_is_not_the_last_one_renamed(said):
    assert after("close chrome", "chrome").follow_up(said) is None


def test_the_other_one_right_after_the_pick():
    assert after("pause the music", choice=(["pause the music", "mute"], "pause the music")) \
        .follow_up("the other one") == "mute"


def test_the_other_one_after_something_else_is_not_about_the_old_question():
    c = after("pause the music", choice=(["pause the music", "mute"], "pause the music"))
    c.done("open firefox", "firefox")
    assert c.follow_up("the other one") is None


def test_after_talk_more_is_not_about_the_volume():
    c = after("volume up")
    c.forget()
    assert c.follow_up("keep going") is None


def test_a_wording_that_points_at_the_screen_is_not_learned_from_claude():
    assert learned.points("open claude in vscord in this", "open day-tracker project in vs code")
    assert not learned.points("crank the music", "volume up")
    assert not learned.points("close this", "close this window")
