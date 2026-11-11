"""What the idle compact bar may show besides "Ready", gathered on a loop off the GTK thread: the weather at home, the
next reminder, things you can say, and how many notifications sit in GNOME's list."""
import json
import re
import time

from gi.repository import Gio, GLib

from skills.weather import FORECAST, SKY, _get

WEATHER_EVERY, REMINDERS_EVERY, TICK = 1800, 60, 3  # seconds; TICK: a dismissed notification leaves the bar soon
COUNT = "imports.ui.main.messageTray.getSources().reduce((n,s)=>n+s.notifications.length,0)"


def tray_count(bus):
    """How many notifications are in GNOME's list: dismissed ones are gone from it. 0 if the shell won't say."""
    try:
        ok, out = bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell", "Eval",
                                GLib.Variant("(s)", (COUNT,)), None, Gio.DBusCallFlags.NONE, 1000, None).unpack()
        return int(json.loads(out)) if ok else 0
    except (GLib.Error, AttributeError, ValueError, TypeError):  # AttributeError: no session bus
        return 0


def tips(skills):
    """Things you can say just as they are: no <x> to fill in, no (notes), two words or more ("type" alone means
    little). Any length: the bar gives a tip all its room and scrolls one that still won't fit."""
    return [p for s in skills for p in s.phrases if not re.search(r"[<(]", p) and " " in p]


def next_reminder(reminders, now):
    """"9:30 standup" for the soonest reminder in the next 12 hours (short: the bar has little room), else ""."""
    soon = sorted((r for r in reminders if now < r["at"] / 1000 < now + 12 * 3600), key=lambda r: r["at"])
    if not soon:
        return ""
    text = soon[0]["text"]
    return "%s %s" % (time.strftime("%-I:%M", time.localtime(soon[0]["at"] / 1000)), text)


def feed(jarvis):
    """Runs on its own thread for good, handing jarvis.ui a fresh copy every TICK seconds."""
    s = jarvis.skills
    extras, weather_at, reminders_at = {"tips": tips(s.all)}, 0.0, 0.0
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        bus = None
    while True:
        now = time.time()
        if now - weather_at > WEATHER_EVERY:
            weather_at = now
            try:
                where = s.weather.find(s.weather.home())
                c = _get(FORECAST, {"latitude": where["lat"], "longitude": where["lon"],
                                    "current": "temperature_2m,weather_code"})["current"]
                sky = SKY.get(c["weather_code"], "mixed").split(" with")[0].split()[-1]  # "mostly clear": "clear"
                extras["weather"] = "%d° · %s" % (round(c["temperature_2m"]), sky)  # short: the bar has little room
            except (OSError, ValueError, KeyError, TypeError):
                weather_at = now - WEATHER_EVERY + 300  # offline or no such place: try again in 5 minutes
        if now - reminders_at > REMINDERS_EVERY:
            reminders_at = now
            try:
                extras["next"] = next_reminder(s.todos._list("reminders"), now)
            except Exception:  # the day tracker isn't running (HubDown) or answered oddly: no reminder line
                extras["next"] = ""
        extras["unread"] = tray_count(bus)
        extras["alerts"] = s.proactive.unread()  # JARVIS's own, unread: the full bar's button for "show my alerts"
        jarvis.ui.set_extras(dict(extras))
        time.sleep(TICK)
