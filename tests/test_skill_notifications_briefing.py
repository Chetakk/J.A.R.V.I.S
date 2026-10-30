"""Desktop notifications (skills/notifications.py, system/notifications.py) and the daily briefing
(skills/briefing.py: from the Day Tracker, with the hub down said plainly)."""
import time

import pytest

from harness import Hub


@pytest.fixture
def notes(jv):
    w = jv.j.skills.notifications.watcher
    w.proc = type("P", (), {"poll": lambda self: None})()  # "running" (no real dbus-monitor)
    w.read_at = time.time() - 600
    return w


def test_new_notifications_are_read_out_once(jv, notes):
    notes.add("Slack", "Raj", "lunch at 1?")
    notes.add("Calendar", "Standup in 5 minutes", "")
    jv.do("any new notifications")
    assert jv.said == ["2 new notifications. Slack, Raj: lunch at 1?. Calendar, Standup in 5 minutes."] or jv.says(
        "2 new notifications", "Raj: lunch at 1?")
    jv.clear()
    jv.do("any new notifications")
    assert jv.says("Nothing new, but in the last hour there were 2")


def test_many_are_counted_by_app_not_read_out(jv, notes):
    for i in range(6):
        notes.add("Slack", "msg %d" % i, "")
    notes.add("Mail", "Invoice", "")
    jv.do("read my notifications")
    assert jv.says("7 new notifications", "6 from Slack and 1 from Mail", "on the card")
    assert "msg 5" in jv.card_text()


def test_from_one_app(jv, notes):
    notes.add("Slack", "Raj", "lunch at 1?")
    notes.add("Mail", "Invoice", "")
    jv.do("what did slack send me")
    assert jv.says("1 from Slack", "Raj: lunch at 1?") and not jv.says("Invoice")


def test_summarise_goes_to_claude_with_the_notifications(jv, notes):
    notes.add("Slack", "Raj", "lunch at 1?")
    notes.add("Mail", "Server down", "prod is failing")
    jv.do("summarise my notifications")
    assert jv.claude.asked("Server down") and jv.claude.asked("lunch at 1?")


def test_forget_drops_them(jv, notes):
    notes.add("Slack", "Raj", "hi")
    jv.do("clear my notifications")
    assert notes.recent() == []


def test_markup_is_stripped_and_jarvis_own_are_ignored(jv, notes):
    notes.add("Mail", "New mail", "<b>Invoice</b> overdue")
    notes.add("notify-send", "JARVIS", "Focus time's over")
    assert [n["body"] for n in notes.recent()] == ["Invoice overdue"]


# --- failure cases ---

def test_not_watching_is_said_not_nothing_new(jv):
    jv.do("any new notifications")  # the watcher isn't running
    assert jv.said == ["I'm not watching notifications right now, so I can't tell."]


def test_nothing_from_an_app(jv, notes):
    notes.add("Mail", "Invoice", "")
    jv.do("notifications from slack")
    assert jv.said == ["Nothing from slack since I started."]


def test_asking_what_someone_sent_with_no_match_goes_to_claude(jv, notes):
    notes.add("Mail", "Invoice", "")
    jv.do("what did slack send me")  # could be a person: not answered as "nothing"
    assert jv.claude.asked("what did slack send me")


def test_a_person_isnt_an_app(jv, notes):
    notes.add("Mail", "Invoice", "")
    jv.do("what did my manager send me")
    assert not jv.says("Nothing from") and jv.claude.asked("my manager")


def test_the_latest_with_none_seen(jv, notes):
    jv.do("what was that notification")
    assert jv.said == ["I haven't seen any notifications since I started."]


# --- briefing ---

def test_the_briefing_from_the_day_tracker(jv):
    now = time.time()
    Hub(jv.net, todos=[{"id": "a", "text": "Buy milk", "done": False, "created": 1},
                       {"id": "b", "text": "Old", "done": True, "created": 0}],
        reminders=[{"id": "r", "text": "call Ravi", "at": int((now + 3600) * 1000), "fired": False}])
    jv.do("brief me")
    assert jv.says("1 open to-do" if False else "One open to-do", "Buy milk") or jv.says("open to-do")
    assert jv.says("One reminder: call Ravi")
    text = jv.card_text()
    assert "Buy milk" in text and "Old" not in text and "call Ravi" in text


def test_an_empty_day_says_so(jv):
    Hub(jv.net)
    jv.do("what's my day look like")
    assert jv.says("No reminders or to-dos, and nothing tracked yesterday.")


def test_hub_down_is_said_not_an_empty_day(jv):
    jv.do("brief me")
    assert jv.says("The day tracker isn't running, so I can't see your to-dos or reminders.")
    assert not jv.says("No reminders or to-dos")


def test_the_automatic_briefing_comes_once_a_day_and_can_be_turned_off(jv):
    jv.do("turn off the daily briefing")
    assert jv.j.memory.pref("briefing_auto") is False
    jv.do("start the morning briefings")
    assert jv.j.memory.pref("briefing_auto") is True
