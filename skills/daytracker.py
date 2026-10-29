"""Day Tracker (~/Projects/day-tracker): start and stop its timer, and say where today went.

Talks to the tracker's local hub (http://localhost:7313). The hub logs computer time on its own; the manual
timer is for what it can't see (workouts, breaks, offline study), the same as the tracker page's buttons.
"""
import json
import os
import random
import string
import time
import urllib.error
import urllib.request

from skills.base import Skill

HUB = os.environ.get("DAY_TRACKER_URL", "http://localhost:7313")
# The fields the tracker page owns; PUT /api/state replaces them wholesale.
PAGE_FIELDS = ("entries", "active", "goals", "breakAfter", "focus", "weeklyGoals", "summaryAt", "todos", "reminders")
MIN_MANUAL = 10  # seconds; the hub drops shorter timers too
CATS = {"work": "work", "study": "study", "studying": "study", "entertainment": "fun", "fun": "fun",
        "workout": "workout", "work out": "workout", "working out": "workout", "exercise": "workout", "gym": "workout",
        "break": "break", "a break": "break"}
NAMES = {"work": "work", "study": "study", "fun": "entertainment", "workout": "workout", "break": "break"}
CAT_WORDS = "|".join(sorted(CATS, key=len, reverse=True))


class HubDown(Exception):
    pass


def new_id():
    """Same shape as the tracker's own ids: time in base 36 plus a few random characters."""
    ms, digits, out = int(time.time() * 1000), string.digits + string.ascii_lowercase, ""
    while ms:
        ms, d = divmod(ms, 36)
        out = digits[d] + out
    return out + "".join(random.choices(string.ascii_lowercase + string.digits, k=5))


def spoken(secs):
    """3725 -> "1 hour 2 minutes"."""
    mins = int(secs // 60)
    h, m = divmod(mins, 60)
    parts = ["%d hour%s" % (h, "" if h == 1 else "s")] if h else []
    if m or not h:
        parts.append("%d minute%s" % (m, "" if m == 1 else "s"))
    return " ".join(parts)


def totals(state, now=None, since=None):
    """Seconds per category since local midnight (or `since`): manual entries, detected sessions, and whatever
    is running."""
    now = now or time.time()
    day = since or time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    spans = [(e["cat"], e["start"] / 1000, e["end"] / 1000)
             for e in (state.get("entries") or []) + (state.get("autoEntries") or [])]
    if state.get("active"):
        spans.append((state["active"]["cat"], state["active"]["start"] / 1000, now))
    if state.get("auto"):
        spans.append((state["auto"]["cat"], state["auto"]["start"] / 1000, state["auto"]["last"] / 1000))
    out = {}
    for cat, start, end in spans:
        secs = min(end, now) - max(start, day)
        if secs > 0:
            out[cat] = out.get(cat, 0) + secs
    return out


class DayTracker(Skill):
    def patterns(self):
        """They go before the project rules ("start work" isn't a project)."""
        c = "(%s)" % CAT_WORDS
        return [
            (r"(?:start|begin|track|log)(?: tracking| logging| timing| a| my| the)* %s(?: timer| session| time)?(?: now)?|"
             r"(?:i'?m |i am )?(?:going to |gonna )?(?:start )?(?:take|taking) %s|"
             r"(?:i'?m |i am )(?:going to |gonna )?(?:start )?%s(?:ing)?(?: now)?" % (c, c, c), self.start),
            (r"(?:stop|end|pause) (?:the |my )?(?:day ?tracker |tracker )?(?:timer|tracking|session)|"
             r"(?:i'?m |i am )?done (?:with )?(?:my |the )?(?:%s|timer)" % CAT_WORDS, lambda m: self.stop()),
            (r"what am i tracking|what'?s (?:the )?(?:timer|tracker) (?:on|at|running)|is (?:the |a )?timer running",
             lambda m: self.current()),
            (r"how (?:long|much|many hours) (?:did|have) i (?:been )?%s(?:ed|ing)?(?: today)?|"
             r"how much %s (?:time )?(?:did i do |have i done |today)(?:today)?" % (c, c), self.how_long),
            (r"how'?s my day(?: going)?|(?:my |today'?s )?day (?:summary|tracker)|where did my (?:day|time) go(?: today)?|"
             r"how am i doing today|(?:give me )?(?:a |my )?summary of (?:my |the )?day|how did i spend (?:my |the )?day",
             lambda m: self.summary()),
        ]

    # --- hub ---

    def _call(self, method, path, body=None):
        req = urllib.request.Request(HUB + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 409:  # the page or a heartbeat changed things first: the hub sends its state back
                return {"conflict": json.load(e)}
            raise
        except (urllib.error.URLError, OSError):
            raise HubDown()

    def _state(self):
        return self._call("GET", "/api/state")

    def _save(self, change):
        """Apply `change(state)` and PUT it; retry once if someone else wrote in between."""
        state = self._state()
        for _ in range(2):
            change(state)
            r = self._call("PUT", "/api/state", {"rev": state["rev"], "state": {k: state.get(k) for k in PAGE_FIELDS}})
            if "conflict" not in r:
                return
            state = r["conflict"]
        raise RuntimeError("the tracker kept changing under me")

    def _guard(fn):
        def wrapped(self, *a):
            try:
                return fn(self, *a)
            except HubDown:
                self.say("The day tracker isn't running. Start it with: systemctl --user start day-tracker.")
        wrapped.__name__ = fn.__name__
        return wrapped

    # --- skills ---

    @staticmethod
    def _close(state, now):
        a = state.get("active")
        if a and now - a["start"] / 1000 >= MIN_MANUAL:
            state.setdefault("entries", []).append(
                {"id": new_id(), "cat": a["cat"], "start": a["start"], "end": int(now * 1000), "note": a.get("note", "")})
        state["active"] = None

    @_guard
    def start(self, m):
        cat = CATS[next(g for g in m.groups() if g).lower()]
        before = {}

        def change(state):
            now = time.time()
            a = state.get("active")
            before["cat"] = a and a["cat"]
            if a and a["cat"] == cat:
                return
            self._close(state, now)
            state["active"] = {"cat": cat, "start": int(now * 1000), "note": ""}

        self._save(change)
        if before["cat"] == cat:
            self.say("Already timing %s." % NAMES[cat])
        elif before["cat"]:
            self.say("Stopped %s, timing %s." % (NAMES[before["cat"]], NAMES[cat]))
        else:
            self.say("Timing %s." % NAMES[cat])

    @_guard
    def stop(self):
        ran = {}

        def change(state):
            a = state.get("active")
            ran["a"] = a
            if a:
                self._close(state, time.time())

        self._save(change)
        a = ran["a"]
        if not a:
            self.say("No timer is running.")
        else:
            self.say("Stopped %s after %s." % (NAMES[a["cat"]], spoken(time.time() - a["start"] / 1000)))

    @_guard
    def current(self):
        s = self._state()
        now = time.time()
        if s.get("active"):
            a = s["active"]
            self.say("Timing %s, %s so far." % (NAMES[a["cat"]], spoken(now - a["start"] / 1000)))
        elif s.get("auto") and now - s["auto"]["last"] / 1000 < 60:
            a = s["auto"]
            self.say("No timer, but it's logging %s from %s, %s so far." % (
                NAMES[a["cat"]], a.get("label") or "the computer", spoken(a["last"] / 1000 - a["start"] / 1000)))
        else:
            self.say("Nothing is being tracked right now.")

    @_guard
    def how_long(self, m):
        cat = CATS[next(g for g in m.groups() if g).lower()]
        s = self._state()
        secs = totals(s).get(cat, 0)
        goal = (s.get("goals") or {}).get(cat) or {}
        line = "%s of %s today." % (spoken(secs).capitalize(), NAMES[cat]) if secs >= 60 else \
            "No %s today yet." % NAMES[cat]
        if goal.get("mins"):
            left = goal["mins"] * 60 - secs
            if goal.get("kind") == "max":
                line += " That's %s over your limit." % spoken(-left) if left < 0 else \
                    " %s left before your limit." % spoken(left).capitalize()
            else:
                line += " Goal met." if left <= 0 else " %s to go for your goal." % spoken(left).capitalize()
        self.say(line)

    @_guard
    def summary(self):
        s = self._state()
        t = totals(s)
        if not any(v >= 60 for v in t.values()):
            self.say("Nothing tracked today yet.")
            return
        parts = ["%s %s" % (spoken(v), NAMES[c]) for c, v in sorted(t.items(), key=lambda kv: -kv[1]) if v >= 60]
        line = "Today: " + ", ".join(parts) + "."
        notes = []
        for c, g in (s.get("goals") or {}).items():
            if not g.get("mins") or c not in NAMES:
                continue
            secs = t.get(c, 0)
            if g.get("kind") == "max" and secs > g["mins"] * 60:
                notes.append("%s is %s over its limit" % (NAMES[c], spoken(secs - g["mins"] * 60)))
            elif g.get("kind") != "max" and secs < g["mins"] * 60:
                notes.append("%s left for %s" % (spoken(g["mins"] * 60 - secs), NAMES[c]))
        if notes:
            line += " " + "; ".join(notes).capitalize() + "."
        self.say(line)
