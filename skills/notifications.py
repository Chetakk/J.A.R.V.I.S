""""Any new notifications", "read my notifications", "what did slack send me", "summarise my notifications":
the desktop notifications since JARVIS started (system/notifications.py watches the session bus, in memory only).
"New" means since you last heard them. Up to four are read out, more are counted by app; "summarise" asks Claude
(in system mode it counts them too). "Forget my notifications" drops the list."""
import time
from collections import Counter

from skills.base import Skill
from skills.helpers import and_list
from system.notifications import Watcher

NOTES = r"(?:notifications?|notifs?|alerts? on my (?:screen|desktop)|pop-?ups)"
SPOKEN = 4  # read out at most this many; more are summed up


class Notifications(Skill):
    group = "notifications"
    does = "Your desktop notifications since JARVIS started: read, by app, summarised"
    phrases = ("any new notifications", "read my notifications", "notifications from <app>",
               "summarise my notifications", "clear my notifications")
    gives = "speech and a card"
    permission = "auto"
    checks = "says when it isn't watching rather than 'nothing new'"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.watcher = Watcher()

    def watch(self):
        """From jarvis.watch(): start the reader, and restart it if dbus-monitor ever stops."""
        self.watcher.start()

    def patterns(self):
        return [
            (r"(?:summari[sz]e|sum up|give me a summary of|go through) (?:all )?(?:my |the )?(?:new |recent |latest |"
             r"today'?s )?%s(?: for me)?" % NOTES, lambda m: self.read(summarise=True)),
            (r"(?:read|show|tell me|give me|list|check|what are|go over)(?: me)? (?:all )?(?:my |the )?(?:new |recent |"
             r"latest |last few |today'?s )?%s(?: (?:today|i missed|from today|i got))?|(?:do i have|have i got|"
             r"did i get|are there|got|any) (?:any )?(?:new )?%s(?: (?:today|yet|for me))?|what %s (?:did i (?:get|"
             r"miss)|have i (?:got|had|missed)|came in|have come in|are there)(?: (?:today|earlier|while i was (?:away|"
             r"out|busy|focusing)))?" % (NOTES, NOTES, NOTES), lambda m: self.read()),
            (r"(?:what (?:was|did) (?:that|the last|the latest) %s(?: say)?|(?:read|repeat) (?:me )?(?:that|the last|"
             r"the latest) %s(?: again)?)" % (NOTES, NOTES), lambda m: self.last()),
            (r"(?:read |show |any )?(?:my |the )?%s from (.+)|what did (.+?) (?:notify me about|send me)(?: today)?|"
             r"(?:read|show|any)(?: me)? (?:my |the )?(.+?) %s" % (NOTES, NOTES), self.from_app),
            (r"(?:clear|forget|delete|wipe) (?:all )?(?:my |the )?%s" % NOTES, lambda m: self.forget()),
        ]

    def read(self, summarise=False):
        w = self.watcher
        if not w.running():
            self.say("I'm not watching notifications right now, so I can't tell.")
            return
        new = w.recent(w.read_at)
        items = new or w.recent(time.time() - 3600)
        if not items:
            last = w.recent()
            self.say("No new notifications." + (" The last was from %s, %s." % (last[-1]["app"], _ago(last[-1]["at"]))
                                                if last else ""))
            return
        w.seen()
        self._card(items, "New notifications" if new else "Notifications in the last hour")
        what = "%d new notification%s" % (len(new), "s" if len(new) != 1 else "") if new else \
            "Nothing new, but in the last hour there %s %d" % ("was" if len(items) == 1 else "were", len(items))
        if (summarise and len(items) > 1) or len(items) > SPOKEN:
            if summarise and self.j.mode != "sys":
                self.j._ask_claude(
                    "Sum up these desktop notifications for me (newest last):\n\n%s\n\nIn \"say\", start with \"%s\", "
                    "then in one or two short sentences what matters: who wants what, anything urgent. No list."
                    % (_lines(items), what))
                return
            apps = Counter(n["app"] for n in items)
            self.say("%s: %s. They're on the card." % (what, and_list("%d from %s" % (c, a) for a, c in apps.most_common())))
            return
        self.say("%s. %s" % (what, " ".join(_spoken(n) for n in items)))

    def last(self):
        items = self.watcher.recent()
        if not items:
            self.say("I haven't seen any notifications since I started.")
            return
        self.say("%s %s." % (_spoken(items[-1]), _ago(items[-1]["at"]).capitalize()))

    def from_app(self, m):
        app = next(g for g in m.groups() if g).strip()
        if app.lower() in ("new", "recent", "latest", "all", "the", "my", "today's"):
            return self.read()
        items = self.watcher.recent(app=app)
        if not items and m.group(2):
            return False  # "what did my manager send me": not an app's notifications
        if not items:
            self.say("Nothing from %s since I started." % app)
            return
        self.watcher.seen()
        self._card(items, "Notifications from %s" % app)
        spoken = items[-SPOKEN:]
        self.say("%d from %s%s. %s" % (len(items), app, ", the latest %d" % len(spoken) if len(items) > SPOKEN else "",
                                       " ".join(_spoken(n, app=False) for n in spoken)))

    def forget(self):
        self.watcher.clear()
        self.say("Done, I've forgotten them. The ones in the top bar stay until you clear them there.")

    def _card(self, items, title):
        self.j.show({"title": title, "text": "\n".join(
            "- **%s** %s · %s%s" % (n["app"], time.strftime("%H:%M", time.localtime(n["at"])), n["title"],
                                    ": " + n["body"].replace("\n", " ") if n["body"] else "") for n in items[-30:])})


def _spoken(n, app=True):
    text = n["title"] + (": " + n["body"].split("\n")[0] if n["body"] else "")
    text = text if text[-1:] in ".!?" else text + "."
    return "%s, %s" % (n["app"], text) if app else text


def _lines(items):
    return "\n".join("- %s %s: %s%s" % (time.strftime("%H:%M", time.localtime(n["at"])), n["app"], n["title"],
                                         " | " + n["body"] if n["body"] else "") for n in items)


def _ago(at):
    mins = int((time.time() - at) // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return "%d minute%s ago" % (mins, "s" if mins != 1 else "")
    return "at %s" % time.strftime("%H:%M", time.localtime(at))
