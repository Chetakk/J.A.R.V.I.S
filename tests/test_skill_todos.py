"""Todos and reminders (skills/todos.py), kept in the Day Tracker hub: added, read, ticked off, reminded on time,
cancelled; a hub that's down is said, never a pretend "done"."""
import datetime
import time
import types

import pytest

from harness import Hub
from skills import todos
from skills.todos import Todos

WATCH = Todos._watch  # the real watcher loop (the jv fixture turns it off so it can't outlive a test)


@pytest.fixture
def hub(jv):
    return Hub(jv.net)


def texts(items):
    return [i["text"] for i in items]


def test_add_and_read_todos(jv, hub):
    jv.do("add buy milk to my todo list")
    assert jv.said == ["Added Buy milk to your todos."]
    jv.do("add a todo to call Ravi")
    assert texts(hub.state["todos"]) == ["Buy milk", "Call Ravi"]
    jv.clear()
    jv.do("what's on my todo list")
    assert jv.said == ["You have 2 open todos: Call Ravi; Buy milk."]


def test_tick_off_a_todo(jv, hub):
    jv.do("add buy milk to my todo list")
    jv.do("mark buy milk done")
    assert hub.state["todos"][0]["done"] is True
    assert jv.says("Done: Buy milk.")


def test_tick_off_by_a_close_spelling(jv, hub):
    jv.do("add renew the passport to my todo list")
    jv.do("tick off renew passport")
    assert hub.state["todos"][0]["done"] is True


def test_two_matches_asks_which_and_ticks_neither(jv, hub):
    jv.do("add call ravi about rent to my todo list")
    jv.do("add call ravi about the car to my todo list")
    jv.clear()
    jv.do("mark call ravi done")
    assert jv.said[0].startswith("Which one?")
    assert not any(t["done"] for t in hub.state["todos"])


def test_empty_list(jv, hub):
    jv.do("what's on my todo list")
    assert jv.said == ["Your todo list is clear."]


def test_remind_in_minutes(jv, hub):
    jv.do("remind me in 20 minutes to stretch")
    r = hub.state["reminders"][0]
    assert r["text"] == "stretch" and abs(r["at"] / 1000 - (time.time() + 1200)) < 5
    assert jv.said == ["Okay, I'll remind you in 20 minutes."]


def test_remind_at_a_clock_time(jv, hub):
    jv.do("remind me at 5 pm to call Ravi")
    at = datetime.datetime.fromtimestamp(hub.state["reminders"][0]["at"] / 1000)
    assert (at.hour, at.minute) == (17, 0) and at > datetime.datetime.now()
    assert hub.state["reminders"][0]["text"] == "call Ravi"


def test_set_a_reminder_asks_when_and_what(jv, hub):
    jv.do("set a reminder", "in 10 minutes to drink water")
    assert jv.questions == ["Sure, when? And what for?"]
    assert texts(hub.state["reminders"]) == ["drink water"]


def test_set_a_reminder_with_no_answer_sets_none(jv, hub):
    jv.do("set a reminder")
    assert jv.says("Okay, no reminder.")
    assert hub.state["reminders"] == []


def test_a_time_but_no_reason_asks_what_for(jv, hub):
    jv.do("set a reminder in 2 minutes")  # no answer to "what for?": still set, just "Reminder"
    assert jv.questions == ["Sure. What should I remind you about?"]
    assert texts(hub.state["reminders"]) == ["Reminder"]


def test_list_and_cancel_reminders(jv, hub):
    jv.do("remind me in 20 minutes to stretch")
    jv.do("remind me in 2 hours to call mom")
    jv.clear()
    jv.do("what are my reminders")
    assert jv.said[0].startswith("stretch at ") and "call mom" in jv.said[0]
    jv.do("cancel the stretch reminder")
    assert texts(hub.state["reminders"]) == ["call mom"]
    assert jv.says("Cancelled the reminder to stretch.")


def test_page_saving_at_the_same_time_is_retried_not_lost(jv, hub):
    jv.do("add buy milk to my todo list")
    hub.conflicts = 1
    jv.do("add buy eggs to my todo list")
    assert texts(hub.state["todos"]) == ["Buy milk", "Buy eggs"]


def test_watcher_says_a_due_reminder_once(jv, hub, monkeypatch):
    now = time.time() * 1000
    hub.state["reminders"] = [{"id": "a", "text": "stretch", "at": now - 1000, "fired": False},
                              {"id": "b", "text": "old one", "at": now - 3600 * 1000, "fired": False},
                              {"id": "c", "text": "later", "at": now + 600 * 1000, "fired": False}]
    run_watcher_rounds(monkeypatch, jv, 2)
    assert jv.said == ["Reminder: stretch."]  # once, and the one JARVIS missed by an hour isn't said late
    assert [r.get("said", False) for r in hub.state["reminders"]] == [True, True, False]


def run_watcher_rounds(monkeypatch, jv, rounds):
    left = [rounds]

    def sleep(seconds):
        if not left[0]:
            raise StopIteration
        left[0] -= 1
    monkeypatch.setattr(todos, "time", types.SimpleNamespace(time=time.time, sleep=sleep))
    with pytest.raises(StopIteration):
        WATCH(jv.j.skills.todos)


# --- failure cases ---

@pytest.mark.parametrize("text", ["add buy milk to my todo list", "what's on my todo list",
                                  "remind me in 20 minutes to stretch", "what are my reminders"])
def test_hub_down_is_said_not_pretended(jv, text):  # no Hub: nothing answers on :7313
    jv.do(text)
    assert jv.said == ["The day tracker isn't running. Start it with: systemctl --user start day-tracker."]


def test_cancel_a_reminder_that_isnt_there(jv, hub):
    jv.do("cancel the dentist reminder")
    assert jv.said == ["I couldn't find that reminder."]


def test_marking_something_that_isnt_a_todo_is_handed_on(jv, hub):
    jv.do("add buy milk to my todo list")
    jv.clear()
    jv.do("mark this email as read")
    assert not hub.state["todos"][0]["done"]
    assert not jv.says("Done:")


def test_impossible_clock_time_sets_nothing(jv, hub):
    jv.do("remind me at 27 to call Ravi")
    assert hub.state["reminders"] == []
