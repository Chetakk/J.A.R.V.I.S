"""What the idle compact bar may show besides "Ready", gathered on a loop off the GTK thread: the weather at home, the
next reminder, things you can say, and how many notifications sit in GNOME's list."""
import json
import re
import time

from gi.repository import Gio, GLib

from skills.media import playing_player
from skills.weather import FORECAST, SKY, _get
from voice.listen import active_window_class, active_window_title

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


# Which "Try saying" groups each skill's phrases belong to (by class name); a skill may be in several. General is
# every skill not in another group, plus the ones named under "general" (useful anywhere, though they have a group).
KINDS = {"work": {"Dev", "Projects", "Gmail", "Calendar", "GoogleChat", "Drive", "People", "Files", "Organise",
                  "Layouts", "Windows", "Clipboard", "ClipboardHistory", "CopyBetween", "Tasks", "Wrap", "WhereWasI",
                  "Controls", "Dictation", "Nano", "Notes", "Linked", "FocusMode", "Notifications", "Documents",
                  "Web", "Lookup"},
         "study": {"Reading", "Lookup", "Maths", "Notes", "FocusMode", "Documents", "Linked", "Videos", "Web",
                   "Media", "Drive", "Files", "Clipboard"},
         "leisure": {"Media", "Videos", "Audio", "ShareAudio", "BrowserTabs", "Web", "Lookup"},
         "phone": {"Phone", "Notifications", "People"},
         "fixit": {"Troubleshoot", "SlowComputer", "Frozen", "Network", "Display", "Install", "Screen", "Audio",
                   "Projects"},
         "general": {"Media", "Maths", "Lookup", "Notes", "Clipboard", "Screen", "Windows", "FocusMode",
                     "Notifications", "Calendar", "Gmail", "Web"}}
SIMILAR = {"work": "study", "study": "work", "leisure": "phone", "phone": "leisure", "fixit": "work"}
# What the focused window says you're doing: (kind, WM_CLASS words, title words), first match wins.
DOING = [("study", r"evince|okular|zathura", r"study ?buddy|lecture|course|udemy|coursera|nptel|tutorial|\.pdf\b"),
         ("work", r"^code$|terminal|tilix|kitty|alacritty|obsidian|libreoffice",
          r"gmail|google (?:drive|docs|sheets|slides|calendar|chat|meet)|github|jira|visual studio code"),
         ("leisure", r"spotify|vlc|mpv|totem|vesktop|discord",
          r"youtube|netflix|prime video|hotstar|jiocinema|spotify|twitch"),
         ("phone", r"scrcpy", r"gsconnect"),
         ("fixit", r"gnome-system-monitor|gnome-control-center|synaptic", r"system monitor|settings"),
         ("leisure", r"chrom|firefox|brave", r"")]  # any other browsing


def doing(window_class, title, playing):
    """The kind of thing you're doing now, from the focused window ("" when it doesn't say): music playing
    behind an unknown window counts as leisure. playing() is only asked then (it runs gdbus)."""
    cls, title = " ".join(window_class).lower(), title.lower()
    for kind, classes, titles in DOING:
        if (titles and re.search(titles, title)) or re.search(classes, cls):
            return kind
    return "leisure" if playing() else ""


def tip_mix(skills):
    """{kind: tips} with every kind's tips in a 2-1-1 pattern (2 of the kind, 1 similar, 1 general), and "" for
    general alone. A cycled list, so the dashboard's every-30-s turn and the bar's random pick both keep the mix."""
    pools = {k: tips(s for s in skills if type(s).__name__ in names) for k, names in KINDS.items()}
    general = tips(s for s in skills if type(s).__name__ in KINDS["general"] or
                   not any(type(s).__name__ in names for names in KINDS.values()))
    del pools["general"]
    mix = {"": general}
    for kind, own in pools.items():
        similar = pools[SIMILAR[kind]]
        if not own:
            mix[kind] = general
            continue
        rounds = max(-(-len(own) // 2), len(similar), len(general))  # every tip comes up at least once
        mix[kind] = [p for i in range(rounds) for p in (own[2 * i % len(own)], own[(2 * i + 1) % len(own)],
                                                         similar and similar[i % len(similar)],
                                                         general and general[i % len(general)]) if p]
    return mix


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
    mix = tip_mix(s.all)
    extras, weather_at, reminders_at = {"tips": mix[""]}, 0.0, 0.0
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
        extras["tips"] = mix[doing(active_window_class(), active_window_title(), playing_player)]
        extras["unread"] = tray_count(bus)
        extras["alerts"] = s.proactive.unread()  # JARVIS's own, unread: the full bar's button for "show my alerts"
        jarvis.ui.set_extras(dict(extras))
        time.sleep(TICK)
