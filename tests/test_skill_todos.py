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


# --- the to-do card ---

def three(jv):
    for t in ("buy milk", "call ravi", "renew the passport"):
        jv.do("add %s to my todo list" % t)
    jv.clear()
    jv.do("show me the todos")
    return jv.ui.current_card()


def test_reading_shows_a_card_whose_ticks_work(jv, hub):
    card = three(jv)
    assert card["id"] == "todos" and card["tick"] == "tick todo {n}"
    assert card["text"].splitlines() == ["- [ ] Renew the passport", "- [ ] Call ravi", "- [ ] Buy milk"]
    jv.do("tick todo 2")  # what a click on the second box says
    assert [t["done"] for t in hub.state["todos"]] == [False, True, False]
    assert jv.ui.current_card()["text"].splitlines()[1] == "- [x] Call ravi"  # stays on the card, ticked
    jv.do("untick todo 2")
    assert not any(t["done"] for t in hub.state["todos"])


def test_long_lists_are_on_the_card_not_read_out(jv, hub):
    for n in range(5):
        jv.do("add task number %d with quite a long description: the details go here to my todo list" % n)
    jv.clear()
    jv.do("what's on my todo list")
    assert jv.said == ["You have 5 open todos, they're on the card. The newest is Task number 4 with quite a long "
                       "description."]


def test_mark_the_second_one_done_goes_by_the_card(jv, hub):
    three(jv)
    jv.do("mark the second one done")
    assert jv.says("Done: Call ravi.")
    assert [t["done"] for t in hub.state["todos"]] == [False, True, False]
    jv.do("mark call ravi as not done")
    assert jv.says("Okay, Call ravi is open again.")


def test_delete_removes_it_rather_than_ticking(jv, hub):
    three(jv)
    jv.do("delete buy milk from my todo list")
    assert texts(hub.state["todos"]) == ["Call ravi", "Renew the passport"]
    assert "Buy milk" not in jv.ui.current_card()["text"]


def test_clear_asks_first(jv, hub):
    three(jv)
    jv.do("clear my todo list", "no")
    assert not any(t["done"] for t in hub.state["todos"])
    jv.do("clear my todo list", "yes")
    assert all(t["done"] for t in hub.state["todos"])


# --- alarms ---

def ring_now(jv, hub, monkeypatch, text=todos.ALARM, **extra):
    """An alarm due now, rung by one watcher round; the chime and the waits between are skipped."""
    monkeypatch.setattr(todos, "chime", lambda: None)
    monkeypatch.setattr(todos, "RING_FOR", 0.3)
    hub.state["reminders"] = [dict({"id": "a", "text": text, "at": time.time() * 1000 - 1000, "fired": False}, **extra)]
    run_watcher_rounds(monkeypatch, jv, 1)
    return jv.j.skills.todos


def test_an_alarm_rings_until_stopped(jv, hub, monkeypatch):
    t = ring_now(jv, hub, monkeypatch)
    assert jv.ui.card_open("alarm")
    assert jv.said[0].startswith("It's ") and jv.said[0].endswith("Time to wake up.")
    jv.do("stop")
    assert t.ringing.is_set() and not jv.ui.card_open("alarm")
    assert jv.says("Alarm off.")


def test_snooze_sets_it_again_in_ten_minutes(jv, hub, monkeypatch):
    ring_now(jv, hub, monkeypatch)
    jv.do("snooze")
    again = hub.state["reminders"][-1]
    assert again["text"] == todos.ALARM and 9.9 * 60 < again["at"] / 1000 - time.time() <= 10 * 60
    assert jv.says("Okay, snoozing for 10 minutes.")


def test_stop_the_alarm_with_none_ringing_sets_nothing(jv, hub):
    jv.do("stop the alarm")
    assert jv.said == ["There's no alarm ringing."]
    assert hub.state["reminders"] == []


def test_wake_me_up_is_an_alarm(jv, hub):
    jv.do("wake me up at 7 am")
    assert texts(hub.state["reminders"]) == [todos.ALARM]
    assert jv.said[0].startswith("Okay, alarm set")
    jv.clear()
    jv.do("what alarms do i have")
    assert jv.said[0].startswith("Alarm ") and "7 AM" in jv.said[0]


def test_every_day_repeats_and_comes_round_again(jv, hub, monkeypatch):
    jv.do("remind me every day at 9 am to take medicine")
    r = hub.state["reminders"][0]
    assert r["repeat"] == "daily" and r["text"] == "take medicine"
    assert "every day at 9 AM" in jv.said[0]
    r["at"] = time.time() * 1000 - 1000  # it's due
    jv.clear()
    run_watcher_rounds(monkeypatch, jv, 1)
    assert jv.said == ["Reminder: take medicine."]
    r = hub.state["reminders"][0]
    assert not r.get("said") and 0 < r["at"] / 1000 - time.time() <= 86400  # set for tomorrow, not dropped


def test_weekday_alarm_skips_the_weekend(jv, hub):
    jv.do("set an alarm on weekdays at 7 am")
    r = hub.state["reminders"][0]
    assert r["repeat"] == "weekdays" and datetime.datetime.fromtimestamp(r["at"] / 1000).weekday() < 5


def test_move_a_reminder(jv, hub):
    jv.do("remind me in 20 minutes to stretch")
    jv.do("move the stretch reminder to in 1 hour")
    assert 59 * 60 < hub.state["reminders"][0]["at"] / 1000 - time.time() <= 3600
    assert jv.says("Okay, moved it to")


def test_spoken_time_and_filler_words(jv, hub):
    jv.do("remind me at uh three twenty five that i have a meeting")
    r = hub.state["reminders"][0]
    assert r["text"] == "i have a meeting"
    assert datetime.datetime.fromtimestamp(r["at"] / 1000).minute == 25


def test_turn_off_the_alarm_with_none_ringing_cancels_the_next_one(jv, hub):
    jv.do("set an alarm for 7 am")
    jv.do("remind me in 20 minutes to stretch")
    jv.do("turn off the alarm")
    assert texts(hub.state["reminders"]) == ["stretch"]
    assert jv.says("Cancelled the alarm.")


def test_cancel_the_7_am_alarm(jv, hub):
    jv.do("set an alarm for 7 am")
    jv.do("set an alarm for 8 am")
    jv.do("cancel the 7 am alarm")
    assert len(hub.state["reminders"]) == 1 and "8" in todos.spoken_time(hub.state["reminders"][0]["at"] / 1000)
