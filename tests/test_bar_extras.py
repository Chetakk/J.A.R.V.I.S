"""What the idle compact bar may add after "Ready" (ui/extras.py): the next reminder, things to try, the feed."""
import time
from types import SimpleNamespace


def test_the_next_reminder_is_the_soonest_in_the_next_12_hours():
    from ui.extras import next_reminder

    now = time.mktime((2026, 11, 9, 20, 0, 0, 0, 0, -1))
    rems = [{"text": "call Ravi", "at": (now + 5400) * 1000}, {"text": "standup", "at": (now + 1800) * 1000},
            {"text": "old", "at": (now - 60) * 1000}, {"text": "next week", "at": (now + 13 * 3600) * 1000}]
    assert next_reminder(rems, now) == "8:30 standup"
    assert next_reminder([{"text": "Alarm", "at": (now + 600) * 1000}], now) == "8:10 Alarm"
    assert next_reminder(rems[2:], now) == ""


def test_tips_are_phrases_you_can_say_as_they_are():
    from ui.extras import tips

    skills = [SimpleNamespace(phrases=("brief me", "remind me in <n> minutes", "show my week (a grid of hours)",
                                       "what's the weather like in the city where I was born", "type"))]
    assert tips(skills) == ["brief me", "what's the weather like in the city where I was born"]  # any length


def test_the_notification_count_is_what_is_in_gnomes_list():
    from gi.repository import GLib
    from ui.extras import tray_count

    class Bus:
        def __init__(self, reply):
            self.reply = reply

        def call_sync(self, *a):
            if isinstance(self.reply, Exception):
                raise self.reply
            return SimpleNamespace(unpack=lambda: self.reply)

    assert tray_count(Bus((True, "3"))) == 3
    assert tray_count(Bus((False, ""))) == 0
    assert tray_count(Bus(GLib.Error("no shell"))) == 0
    assert tray_count(None) == 0


def test_the_feed_hands_the_bar_weather_reminder_tips_and_notifications(monkeypatch):
    import ui.extras as extras

    got = []

    class Stop(Exception):
        pass

    def sleep(_s):
        raise Stop

    monkeypatch.setattr(extras.time, "sleep", sleep)
    monkeypatch.setattr(extras, "_get", lambda url, params: {"current": {"temperature_2m": 23.6, "weather_code": 2}})
    monkeypatch.setattr(extras, "tray_count", lambda bus: 2)
    skills = SimpleNamespace(all=[SimpleNamespace(phrases=("brief me",))],
                             weather=SimpleNamespace(home=lambda: "Bengaluru", find=lambda p: {"lat": 1, "lon": 2}),
                             todos=SimpleNamespace(_list=lambda key: []))
    jarvis = SimpleNamespace(skills=skills, ui=SimpleNamespace(set_extras=got.append))
    try:
        extras.feed(jarvis)
    except Stop:
        pass
    assert got == [{"tips": ["brief me"], "weather": "24° · cloudy", "next": "", "unread": 2}]
