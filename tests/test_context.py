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
    assert learned.points("open the folder on my screen in vs code", "open 1440-biztel-app in vs code")
    assert not learned.points("crank the music", "volume up")
    assert not learned.points("close this", "close this window")


@pytest.mark.parametrize("said", ["thank you too", "love you too", "and good night too", "and him too",
                                  "and all apps too", "and everything too", "and also please"])
def test_talk_with_too_is_not_a_name_to_close(said):
    assert after("close chrome", "chrome").follow_up(said) is None


@pytest.mark.parametrize("text,said,meant", [
    ("set volume to 40", "make it 400", None),  # not 400 percent
    ("set volume to 40", "make it seventy", "set volume to 70"),
    ("set volume to 40", "twenty five", "set volume to 25"),
    ("set volume to 40 in 40 seconds", "60", None),  # which 40?
])
def test_a_new_number(text, said, meant):
    assert after(text, "40").follow_up(said) == meant


@pytest.mark.parametrize("text,said,meant", [
    ("turn the light on in the hall", "too much", "turn the light off in the hall"),
    ("scroll down in chrome", "too far", "scroll up in chrome"),
    ("go back", "too far", "go forward"),
    ("volume up", "too loud", "volume down"),
    ("volume up", "no, less", "volume down"),
    ("volume up", "a lot more", "volume up"),
])
def test_the_other_way_swaps_the_step_word(text, said, meant):
    assert after(text).follow_up(said) == meant


@pytest.mark.parametrize("text,said,meant", [
    ("open firefox", "and chrome too", "open chrome"),  # no value given: the name after "open"
    ("add milk to my todo list", "and eggs too", "add eggs to my todo list"),
])
def test_the_name_is_found_when_none_was_given(text, said, meant):
    assert after(text).follow_up(said) == meant


def test_the_other_one_whatever_the_case():
    assert after("chrome", choice=(["Chrome", "chromium"], "chrome")).follow_up("the other one") == "chromium"


@pytest.mark.parametrize("talk", ["how are you", "thanks", "tell me a joke"])
def test_after_small_talk_more_means_nothing(jv, talk):
    jv.do("volume up")
    jv.do(talk)
    jv.clear()
    jv.do("a bit more")
    assert not jv.shell.ran(r"pactl set-sink-volume") and jv.says("lost track")


def test_after_it_could_not_zoom_more_is_not_another_try(jv):
    jv.do("zoom in")
    assert jv.says("don't see a browser")
    jv.clear()
    jv.do("more")
    assert jv.says("lost track")


def test_more_after_undoing_volume_up_goes_on_down(jv):
    jv.do("volume up")
    jv.do("cancel that")
    jv.clear()
    jv.do("more")
    assert jv.j.context.text == "volume down"


def test_thank_you_too_after_close_chrome_closes_nothing(jv):
    jv.do("close chrome", "yes")
    jv.clear()
    jv.do("thank you too", "yes")
    assert not jv.shell.ran(r"pkill")
