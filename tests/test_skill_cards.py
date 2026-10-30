"""What you can say about the card on screen (skills/cards.py): pick a choice, open a link, read, copy, save, pin;
timers; cards that update themselves (only for commands that are safe to repeat)."""
import os
import time

import pytest

from harness import Clock
from skills import cards


@pytest.fixture
def card(jv):
    def show(**kw):
        jv.ui.show_card(dict({"title": "Pick", "text": "Some text"}, **kw))
    return show


@pytest.fixture(autouse=True)
def stop_timers(jv):
    yield
    for stop in list(jv.j.cards.timers.values()):
        stop.set()


def test_pick_a_choice_by_number_runs_its_phrase(jv, card):
    card(choices=[{"label": "Tea", "do": "remember that I like tea"}, {"label": "Coffee", "do": "remember that I like coffee"}])
    jv.do("the second one")
    assert jv.j.memory.preferences()[0]["value"] == "I like coffee"


def test_pick_the_last(jv, card):
    card(choices=[{"label": "A", "do": "remember that a is 1"}, {"label": "B", "do": "remember that b is 2"}])
    jv.do("the last one")
    from skills import notes
    assert notes.load_notes()[-1]["text"] == "b is 2"


def test_there_are_only_so_many_options(jv, card):
    card(choices=[{"label": "A", "do": "x"}])
    jv.do("the third one")
    assert jv.said == ["There are only 1 options."]


def test_without_choices_the_second_one_goes_elsewhere(jv, card):
    card()
    jv.do("the second one")
    assert not jv.says("options")


def test_open_a_link_from_the_card(jv, card):
    card(text="[docs](https://example.com/docs) and [repo](https://example.com/repo)")
    jv.do("open the second link")
    assert jv.shell.ran(r"^xdg-open https://example.com/repo$")


def test_copy_and_save_the_card(jv, card):
    card(title="Network check", text="- all fine")
    jv.do("copy that")
    assert ("xclip -selection clipboard", "- all fine") in jv.shell.inputs
    jv.do("save that")
    saved = os.listdir(cards.CARDS_DIR)
    assert len(saved) == 1 and saved[0].endswith("network-check.md")


def test_read_a_card_aloud(jv, card):
    card(text="Two things: milk and eggs.")
    jv.do("read it to me")
    assert jv.says("milk and eggs")


def test_pin_and_unpin(jv, card):
    card()
    jv.do("pin it")
    assert ("pin_card", ()) in jv.ui.calls
    jv.do("unpin")
    assert ("unpin_cards", ()) in jv.ui.calls


# --- failure cases: with no card, these phrases aren't about one ---

@pytest.mark.parametrize("text", ["copy that", "save that", "read it to me", "pin it"])
def test_no_card_means_nothing_to_do(jv, text):
    jv.do(text)  # (it carries on to the usual routing)
    assert not jv.shell.inputs and not os.path.exists(cards.CARDS_DIR)
    assert not [c for c in jv.ui.calls if c[0] in ("pin_card", "unpin_cards", "card_step")]


def test_a_picture_only_card_has_nothing_to_read(jv, card):
    card(text="", image="/tmp/x.png")
    jv.do("read it to me")
    assert jv.said == ["There's only a picture on it."]


def test_a_card_with_links_but_open_the_fifth(jv, card):
    card(text="[docs](https://example.com/docs)")
    jv.do("open the fifth link")
    assert jv.said == ["There are only 1 links on the card."]
    assert not jv.shell.ran(r"^xdg-open")


# --- timers ---

def test_a_timer_shows_a_card_and_can_be_cancelled(jv):
    jv.do("set a timer for 10 minutes for pasta", wait=0.2)
    assert jv.said[0] == "Okay, pasta timer for 10 minutes."
    assert jv.j.cards.timers
    deadline = time.time() + 2
    while not jv.cards and time.time() < deadline:
        time.sleep(0.05)
    assert jv.cards[0]["title"] == "pasta timer" and "10:00" in jv.cards[0]["text"] or "9:5" in jv.cards[0]["text"]
    jv.do("cancel the timer")
    assert jv.says("cancelled")
    deadline = time.time() + 2
    while jv.j.cards.timers and time.time() < deadline:  # its thread notices and goes
        time.sleep(0.05)
    assert not jv.j.cards.timers


def test_cancel_with_no_timer(jv):
    jv.do("cancel the timer")
    assert jv.said == ["There's no timer running."]


def test_a_timer_that_runs_out_says_so(jv, monkeypatch):
    jv.do("set a timer for 1 second", wait=0.2)
    kind, line = jv.j.inbox.get(timeout=4)
    assert (kind, line) == ("say", "Time's up!")


def test_not_a_number_of_minutes_isnt_a_timer(jv):
    jv.do("set a timer for banana minutes", wait=0.2)
    assert not jv.j.cards.timers


# --- live cards ---

def test_a_live_card_only_repeats_safe_commands(jv):
    jv.j.show({"title": "Disk", "text": "{output}", "refresh": {"command": "rm -rf ~/x", "every": 2}})
    assert not jv.shell.ran(r"rm")
    assert jv.cards[-1]["title"] == "Disk"  # shown once, as it is
    assert ("add_chat", ("sys", "not refreshing (needs your OK each time): rm -rf ~/x")) in jv.ui.calls


def test_a_live_card_refreshes_a_safe_command_and_stops_when_closed(jv, monkeypatch):
    monkeypatch.setattr(cards, "time", Clock())
    jv.shell.on(r"^df ", out="/dev/sda1 9G")
    jv.j.show({"title": "Disk", "text": "Free:\n{output}", "refresh": {"command": "df -h /", "every": 2, "for": 10}})
    jv.settle(wait=3)
    assert jv.shell.ran(r"^df -h /$")
    assert "/dev/sda1 9G" in jv.cards[0]["text"] and "updated" in jv.cards[0]["text"]
