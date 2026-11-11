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


@pytest.mark.parametrize("said", ["three", "3", "number three", "option 3", "open number three",
                                  "play the third one", "I want the third one", "three please", "Three."])
def test_pick_by_saying_its_number(jv, card, said):
    card(choices=[{"label": c, "do": "remember that I like %s" % c} for c in ("tea", "coffee", "juice")])
    jv.do(said)
    assert jv.j.memory.preferences()[0]["value"] == "I like juice"


def test_number_to_is_number_two(jv, card):
    card(choices=[{"label": c, "do": "remember that I like %s" % c} for c in ("tea", "coffee")])
    jv.do("number to")
    assert jv.j.memory.preferences()[0]["value"] == "I like coffee"


@pytest.mark.parametrize("said", ["twelve", "number 12", "the twelfth one", "12th"])
def test_a_long_list_past_the_buttons(jv, card, said):
    card(choices=[{"label": "%d" % n, "do": "remember that I like number %d" % n} for n in range(1, 21)])
    jv.do(said)
    assert jv.j.memory.preferences()[0]["value"] == "I like number 12"


def test_seventeen_is_not_seven(jv, card):
    card(choices=[{"label": "%d" % n, "do": "remember that I like number %d" % n} for n in range(1, 21)])
    jv.do("seventeen")
    assert jv.j.memory.preferences()[0]["value"] == "I like number 17"


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
    assert jv.cards[0]["title"] == "pasta timer" and "```countdown" in jv.cards[0]["text"]
    assert "total: 600" in jv.cards[0]["text"]  # the ring counts down by itself from there
    jv.do("cancel the timer")
    assert jv.says("cancelled")
    deadline = time.time() + 2
    while jv.j.cards.timers and time.time() < deadline:  # its thread notices and goes
        time.sleep(0.05)
    assert not jv.j.cards.timers
    sets = [c[1] for c in jv.ui.calls if c[0] == "set_timer"]  # the compact bar counts it down, then lets it go
    assert sets[0][1] == "pasta" and sets[0][2] > time.time() + 590 and sets[-1][1:] == ("pasta",)


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


# --- several cards at once ---

def test_a_second_card_opens_beside_the_first(jv):
    jv.j.show({"title": "One", "text": "first"})
    jv.j.show({"title": "Two", "text": "second"})
    assert [c["title"] for c in jv.ui.open] == ["One", "Two"]
    assert jv.ui.current_card()["title"] == "Two"  # "read it", "copy that" are about the newest


def test_several_cards_in_one_go(jv):
    jv.j.show([{"title": "Laptop %d" % n, "text": "spec"} for n in range(1, 4)])
    assert [c["title"] for c in jv.ui.open] == ["Laptop 1", "Laptop 2", "Laptop 3"]


def test_claude_can_show_several_cards(jv):
    jv.claude.reply({"say": "Here are both.", "show": [{"title": "ThinkPad", "text": "- 14 inch"},
                                                       {"title": "MacBook Air", "text": "- 13 inch"}]})
    jv.do("which laptop should i get for coding on the go")
    assert [c["title"] for c in jv.ui.open] == ["ThinkPad", "MacBook Air"]


def test_at_most_ten_cards_the_oldest_goes(jv):
    for n in range(1, 13):
        jv.j.show({"title": "Card %d" % n, "text": "x"})
    assert len(jv.ui.open) == 10 and jv.ui.open[0]["title"] == "Card 3"


@pytest.mark.parametrize("said", ["close all the cards", "close the cards", "clear my cards", "hide all cards",
                                  "close them all"])
def test_close_every_card(jv, said):
    jv.j.show({"title": "One", "text": "first"})
    jv.j.show({"title": "Two", "text": "second"})
    jv.do(said)
    assert jv.ui.open == []


def test_a_card_with_the_same_id_updates_in_place_not_beside(jv):
    jv.j.show({"id": "progress", "title": "Copying", "text": "[[10%]]"})
    jv.j.show({"title": "Other", "text": "x"})
    jv.j.show({"id": "progress", "title": "Copying", "text": "[[60%]]"})
    assert [c["title"] for c in jv.ui.open] == ["Copying", "Other"] and "60%" in jv.ui.open[0]["text"]


def test_close_it_closes_only_the_newest_card(jv):
    jv.j.show({"title": "One", "text": "first"})
    jv.j.show({"title": "Two", "text": "second"})
    jv.do("close it")
    assert [c["title"] for c in jv.ui.open] == ["One"]


def test_a_timer_keeps_counting_behind_a_newer_card(jv, monkeypatch):
    jv.do("set a timer for 10 minutes for pasta", wait=0.2)
    deadline = time.time() + 2
    while not jv.ui.open and time.time() < deadline:
        time.sleep(0.05)
    timer_id = jv.ui.open[0]["id"]
    jv.j.show({"title": "Weather", "text": "sunny"})
    assert jv.j.cards._showing(timer_id)  # still on screen, so it keeps updating
    jv.do("cancel the timer")
    assert [c["title"] for c in jv.ui.open] == ["Weather"]  # only the timer's card went


def test_a_button_that_says_the_first_one_doesnt_pick_itself_forever(jv):
    # Claude once made to-do buttons whose phrase was "the first one": a click recursed until Python gave up.
    jv.j.show({"title": "Open to-dos", "text": "- [ ] a", "choices": [{"label": "JARVIS wake 2", "do": "the first one"}]})
    jv.do("the first one")
    assert jv.said == ["Picked JARVIS wake 2."]


DRIVE = {"id": "drive-share-1", "title": "Shared with you", "text": "[PLANT INTELLIGENCE](https://docs.google.com/d/1)",
         "choices": [{"label": "Open it", "do": "open the first link"}]}
MAIL = {"id": "mail-1", "title": "Mail from Docker", "text": "Still exploring Docker MCP?",
        "choices": [{"label": "Read it", "do": "remember that I like reading"},
                    {"label": "Add to my to-dos", "do": "remember that I like docker"}]}


@pytest.mark.parametrize("said", ["open it", "Open it.", "can you open it"])
def test_open_it_presses_an_older_cards_open_button_and_opens_its_link(jv, said):
    jv.ui.show_card(DRIVE)
    jv.ui.show_card(MAIL)  # newer, and has no link: "open the first link" must be about the Drive card
    jv.do(said)
    assert jv.questions == [] and jv.shell.ran(r"docs\.google\.com/d/1")


@pytest.mark.parametrize("said, value", [("add to my to-dos", "I like docker"), ("Add to my todos.", "I like docker"),
                                         ("read it", "I like reading")])
def test_say_what_a_button_shows(jv, said, value):
    jv.ui.show_card(DRIVE)
    jv.ui.show_card(MAIL)
    jv.do(said)
    assert jv.j.memory.preferences()[0]["value"] == value


def test_open_that_with_no_open_button_reads_or_opens_the_link(jv):
    jv.ui.show_card(MAIL)
    jv.do("open that")
    assert jv.j.memory.preferences()[0]["value"] == "I like reading"
    jv.ui.close_card(every=True)
    jv.ui.show_card({"title": "Docs", "text": "[a doc](https://example.com/a)"})
    jv.do("open it")
    assert jv.shell.ran(r"example\.com/a")


def test_a_label_that_is_its_own_phrase_is_not_pressed_forever(jv, card):
    card(choices=["remember that I like tea"])
    jv.do("remember that I like tea")
    assert jv.j.memory.preferences()[0]["value"] == "I like tea"
