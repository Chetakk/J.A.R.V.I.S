"""Todos and reminders, kept in the Day Tracker (~/Projects/day-tracker) so its page shows them too.

"add buy milk to my todo list", "what's on my todo list", "mark buy milk done", "remind me in 20 minutes to
stretch", "remind me at 5 pm to call Ravi", "what are my reminders", "cancel the stretch reminder".
A watcher thread says reminders out loud when they come due; the tracker page chimes for them as well.
"""
import datetime
import difflib
import re
import threading
import time

from skills.base import Skill
from skills.daytracker import DayTracker, HubDown, new_id, spoken

TODO = r"(?:to ?-?dos?|todo list|to ?do list|task list|tasks)"
MAX_READ = 5
LATE = 10 * 60  # a reminder that came due longer ago than this (JARVIS was off) is dropped quietly
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15,
         "twenty": 20, "thirty": 30, "forty five": 45, "a couple of": 2, "a few": 3, "half an": 0.5}
NUM = r"(\d+(?:\.\d+)?|%s)" % "|".join(sorted(WORDS, key=len, reverse=True))
IN = r"in %s (seconds?|secs?|minutes?|mins?|hours?|hrs?)" % NUM
AT = r"(?:(today|tonight|tomorrow) )?(?:at )?(\d{1,2})(?:[:.](\d{2}))? ?([ap]\.? ?m\.?)?(?: (today|tonight|tomorrow))?"


def due_in(n, unit):
    n = WORDS.get(n.lower()) if n.lower() in WORDS else float(n)
    return time.time() + n * {"s": 1, "m": 60, "h": 3600}[unit[0].lower()]


def due_at(day1, hour, minute, ampm, day2, now=None):
    """Clock time -> timestamp. No am/pm: the next time that hour comes round (5 at 3pm means 5pm)."""
    now = datetime.datetime.fromtimestamp(now or time.time())
    day = (day1 or day2 or "").lower()
    h, m = int(hour), int(minute or 0)
    if h > 23 or m > 59:
        return None
    ampm = (ampm or "").replace(".", "").replace(" ", "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    elif not ampm and day == "tonight" and h < 12:
        h += 12
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if day == "tomorrow":
        t += datetime.timedelta(days=1)
    elif not ampm and h <= 12 and t <= now and t + datetime.timedelta(hours=12) > now:
        t += datetime.timedelta(hours=12)
    if t <= now:
        t += datetime.timedelta(days=1)
    return t.timestamp()


def spoken_time(ts):
    t, today = datetime.datetime.fromtimestamp(ts), datetime.date.today()
    clock = t.strftime("%I:%M %p").lstrip("0").replace(":00", "")
    if t.date() == today:
        return "at " + clock
    return ("tomorrow at " if t.date() == today + datetime.timedelta(days=1) else t.strftime("%A at ")) + clock


class Todos(Skill):
    def __init__(self, jarvis, watch=True):
        super().__init__(jarvis)
        self.hub = DayTracker(jarvis)
        if watch:
            threading.Thread(target=self._watch, daemon=True, name="reminders").start()

    def patterns(self):
        """"what's pending" stays with project notes; these say "todo" or "remind"."""
        return [
            (r"remind me %s(?: to| about| that)? (.+)|remind me(?: to| about| that)? (.+?) %s" % (IN, IN), self.remind_in),
            (r"remind me %s(?: to| about| that)? (.+)|remind me(?: to| about| that)? (.+?) %s" % (AT, AT), self.remind_at),
            (r"(?:what are|read|list|show me|tell me|any) (?:my |the )?reminders(?: today| for today)?|"
             r"what (?:did i ask you to remind me|reminders do i have)(?: about)?", lambda m: self.reminders()),
            (r"(?:cancel|delete|remove|clear) (?:the |my )?(?:(.+?) )?reminders?(?: (?:about|for|to) (.+))?", self.cancel),
            (r"(?:add|put) (.+?) (?:to|on|in) my %s|(?:add|create|make)(?: a)?(?: new)? to ?-?do(?: to| for)?:? (.+)" % TODO,
             self.add),
            (r"(?:mark|tick off|check off|cross off) (?:the )?(.+?)(?: to ?-?do| task)?(?: as)?"
             r"(?: done| complete| completed| finished)?", self.done),
            (r"(?:what'?s|what is|what are|read(?: me)?|show me|list|tell me)(?: on| in)? my %s(?: today)?|my %s|"
             r"(?:how many|any) %s (?:do i have|left|open)(?: today)?" % (TODO, TODO, TODO), lambda m: self.read()),
        ]

    def _guard(fn):
        def wrapped(self, *a):
            try:
                return fn(self, *a)
            except HubDown:
                self.say("The day tracker isn't running. Start it with: systemctl --user start day-tracker.")
        wrapped.__name__ = fn.__name__
        return wrapped

    def _list(self, key):
        return self.hub._state().get(key) or []

    def _change(self, key, fn):
        """Run fn(list) on the tracker's todos or reminders and save; returns what fn returned."""
        out = {}

        def change(state):
            items = state.get(key) or []
            out["r"] = fn(items)
            state[key] = items

        self.hub._save(change)
        return out["r"]

    # --- todos ---

    @_guard
    def read(self):
        todos = [t for t in self._list("todos") if not t.get("done")]
        if not todos:
            self.say("Your todo list is clear.")
            return
        todos.sort(key=lambda t: -t.get("created", 0))
        top = [t["text"] for t in todos[:MAX_READ]]
        more = len(todos) - len(top)
        line = "You have %d open todo%s: %s" % (len(todos), "" if len(todos) == 1 else "s", "; ".join(top))
        self.say(line + (", and %d more." % more if more else "."))

    @_guard
    def add(self, m):
        text = next(g for g in m.groups() if g).strip(" .")
        if text.lower().startswith("to "):  # "add a todo to call Ravi"
            text = text[3:]
        text = text[:1].upper() + text[1:]
        self._change("todos", lambda items: items.append(
            {"id": new_id(), "text": text[:200], "done": False, "created": int(time.time() * 1000)}))
        self.say("Added %s to your todos." % text)

    @_guard
    def done(self, m):
        want = m.group(1).strip().lower()

        def tick(items):
            open_ = [t for t in items if not t.get("done")]
            texts = [t["text"].lower() for t in open_]
            hits = [t for t, x in zip(open_, texts) if want in x]
            if not hits:
                hits = [open_[texts.index(c)] for c in difflib.get_close_matches(want, texts, n=1, cutoff=0.5)]
            if len(hits) == 1:
                hits[0]["done"] = True
            return hits

        hits = self._change("todos", tick)
        if not hits:
            return False  # not a todo: let the request carry on
        if len(hits) > 1:
            self.say("Which one? %s." % " or ".join(t["text"] for t in hits[:3]))
        else:
            self.say("Done: %s." % hits[0]["text"])

    # --- reminders ---

    def _remind(self, text, at):
        text = text.strip(" .")
        self._change("reminders", lambda items: items.append(
            {"id": new_id(), "text": text[:200], "at": int(at * 1000), "fired": False}))
        self.say("Okay, I'll remind you %s." % (
            "in %s" % _in_words(at - time.time()) if at - time.time() <= 3 * 3600 else spoken_time(at)))

    @_guard
    def remind_in(self, m):
        g = m.groups()
        n, unit, text = (g[0], g[1], g[2]) if g[0] else (g[4], g[5], g[3])
        self._remind(text, due_in(n, unit))

    @_guard
    def remind_at(self, m):
        g = m.groups()
        when, text = (g[0:5], g[5]) if g[1] else (g[7:12], g[6])
        at = due_at(*when)
        if at is None:
            return False
        self._remind(text, at)

    @_guard
    def reminders(self):
        now = time.time() * 1000
        rems = sorted((r for r in self._list("reminders") if r["at"] > now), key=lambda r: r["at"])
        if not rems:
            self.say("You have no reminders coming up.")
            return
        self.say("; ".join("%s %s" % (r["text"], spoken_time(r["at"] / 1000)) for r in rems[:MAX_READ]) + ".")

    @_guard
    def cancel(self, m):
        want = " ".join(g for g in m.groups() if g).lower().strip()
        now = time.time() * 1000

        def drop(items):
            live = [r for r in items if r["at"] > now]
            hits = [r for r in live if want in r["text"].lower()] if want and want not in ("that", "the", "my", "all") else \
                (live if want == "all" else live[-1:])  # "cancel that reminder": the newest one
            if len(hits) == 1 or want == "all":
                for r in hits:
                    items.remove(r)
            return hits

        hits = self._change("reminders", drop)
        if not hits:
            self.say("I couldn't find that reminder.")
        elif len(hits) > 1 and want != "all":
            self.say("Which one? %s." % " or ".join(r["text"] for r in hits[:3]))
        else:
            self.say("Cancelled %s." % ("all reminders" if want == "all" else "the reminder to " + hits[0]["text"]))

    def _watch(self):
        while True:
            time.sleep(15)
            try:
                now = time.time() * 1000
                if not any(not r.get("said") and r["at"] <= now for r in self._list("reminders")):
                    continue
                due = []

                def mark(items):
                    for r in items:
                        if not r.get("said") and r["at"] <= now:
                            r["said"] = True
                            if now - r["at"] < LATE * 1000:
                                due.append(r["text"])

                self._change("reminders", mark)
                for text in due:
                    self.say("Reminder: %s." % text)
            except Exception as e:  # hub down or restarting: try again next round
                if not isinstance(e, HubDown):
                    print("[reminders] %r" % e)


def _in_words(secs):
    secs = round(secs)
    if secs < 60:
        return "%d seconds" % secs
    return spoken(round(secs / 60) * 60)
