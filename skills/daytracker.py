"""Day Tracker (~/Projects/day-tracker): everything its page does, by voice.

Talks to the tracker's local hub (http://localhost:7313). The hub logs computer time on its own; the manual
timer is for what it can't see (workouts, breaks, offline study), the same as the tracker page's buttons.
Timer and countdowns ("study for 25 minutes"), notes on the session, time you forgot to log ("I worked out for
30 minutes", "log study from 6 to 7"), fixing or deleting the last session, the time you were away, daily and
weekly goals and limits, break reminders, the daily summary time, today / yesterday / this week, and the page
itself ("open the day tracker", "show the big timer"). The page picks every change up within 5 seconds.
"""
import datetime
import json
import os
import random
import re
import shlex
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
# Something already done, said in the past tense: "I worked out for 30 minutes", "I studied from 6 to 7".
DID = {"worked out": "workout", "exercised": "workout", "went to the gym": "workout", "studied": "study",
       "worked": "work", "took a break": "break", "had a break": "break", "was on a break": "break",
       "rested": "break", "had fun": "fun", "watched youtube": "fun", "played games": "fun", "gamed": "fun"}
DID_WORDS = "|".join(sorted(DID, key=len, reverse=True))
NUMS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "twenty five": 25, "thirty": 30, "forty": 40,
        "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half an": 0.5, "half a": 0.5}
N = r"(?:\d+(?:\.\d+)?|%s)" % "|".join(sorted(NUMS, key=len, reverse=True))
# "25 minutes", "25-minute", "an hour and a half", "1 hour 20 minutes", "half an hour"
DUR = r"(%s[ -]?(?:hours?|hrs?)(?: and a half)?(?:,? (?:and )?%s[ -]?(?:minutes?|mins?))?|%s[ -]?(?:minutes?|mins?))" % (
    N, N, N)
CLOCK = r"(\d{1,2})(?:[:.](\d{2}))? ?([ap]\.? ?m\.?)?"
COUNTDOWN_LATE = 120  # a countdown that ran out longer ago than this (JARVIS was off) is logged without a word


def seconds(dur):
    """"an hour and a half" -> 5400, "25 minutes" -> 1500, "1 hour 20 minutes" -> 4800."""
    total = 0
    for n, unit in re.findall(r"\b(%s)[ -]?(hours?|hrs?|minutes?|mins?)\b" % N, dur.lower()):
        total += (NUMS[n] if n in NUMS else float(n)) * (3600 if unit[0] == "h" else 60)
    return total + (1800 if "and a half" in dur.lower() else 0)


def _hours(h, ampm):
    """The 24-hour clock hours "6" could mean: 6 and 18 without am/pm."""
    h, ampm = int(h), (ampm or "").replace(".", "").replace(" ", "").lower()
    if ampm == "pm":
        return [h + 12 if h < 12 else h]
    if ampm == "am":
        return [0 if h == 12 else h]
    return [h, h + 12] if h < 12 else [h]


def past_span(h1, m1, ap1, h2, m2, ap2, yesterday=False, now=None):
    """"from 6 to 7 pm", for something already done -> (start, end) timestamps: the latest such span that has
    ended (yesterday's, when it says so). None if the times don't make a span."""
    now = now or time.time()
    today = datetime.date.fromtimestamp(now)
    for back in ((1,) if yesterday else (0, 1)):
        day, best = today - datetime.timedelta(days=back), None
        for a in _hours(h1, ap1):
            for b in _hours(h2, ap2):
                if a > 23 or b > 23 or int(m1 or 0) > 59 or int(m2 or 0) > 59:
                    continue
                s = datetime.datetime.combine(day, datetime.time(a, int(m1 or 0))).timestamp()
                e = datetime.datetime.combine(day, datetime.time(b, int(m2 or 0))).timestamp()
                if s < e <= now + 60 and e - s <= 12 * 3600 and (not best or e > best[1]):
                    best = (s, e)
        if best:
            return best
    return None


def clock(ts):
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0").replace(":00", "")


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


def midnight(days_back=0, now=None):
    day = datetime.date.fromtimestamp(now or time.time()) - datetime.timedelta(days=days_back)
    return datetime.datetime.combine(day, datetime.time()).timestamp()


def totals(state, now=None, since=None):
    """Seconds per category from local midnight (or `since`) until `now`: manual entries, detected sessions, and
    whatever is running."""
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
        c, d, t = "(%s)" % CAT_WORDS, DUR, CLOCK
        goal = r"(weekly |daily |week'?s |day'?s )?"
        return [
            # Countdowns: "study for 25 minutes", "start a 25 minute work timer", "work timer for an hour".
            (r"(?:start|set|begin)(?: me)?(?: a| an)? %s(?: long)? %s(?: timer| session| countdown)?|"
             r"(?:start |set )?(?:a |an |the )?%s (?:timer|session|countdown) for %s|"
             r"(?:i'?m |i am |let me |let'?s )?(?:going to |gonna )?(?:start )?%s for (?:the next )?%s(?: now)?"
             % (d, c, c, d, c, d), self.countdown),
            # Time you forgot: "log 30 minutes of workout", "I worked out for 30 minutes", "log study from 6 to 7".
            (r"(?:log|add|record|track) %s (?:of )?%s( yesterday)?|i (?:did|spent|had) %s (?:of )?%s( yesterday)?|"
             r"i (%s) for %s( yesterday)?|(?:log|add|record) (?:a |an )?%s(?: session)? (?:of|for) %s( yesterday)?"
             % (d, c, d, c, DID_WORDS, d, c, d), self.log_time),
            (r"(?:log|add|record|track) (?:a |an |my )?%s(?: session)? from %s (?:to|till|until) %s( yesterday)?|"
             r"i (%s) from %s (?:to|till|until) %s( yesterday)?" % (c, t, t, DID_WORDS, t, t), self.log_span),
            (r"(?:add |put )?(?:a )?note (?:on|to|for) (?:the |this |my )?(?:session|timer)[:,]? (.+)|"
             r"(?:label|call|name) (?:this|the|my) (?:session|timer)(?: as)? (.+)", self.note),
            (r"(?:cancel|discard|throw away|scrap|reset|bin) (?:the |this |my )?(?:timer|session|countdown)"
             r"(?: without (?:logging|saving) it)?|don'?t (?:log|save) (?:this|the) (?:session|timer)",
             lambda m: self.discard()),
            (r"(?:delete|remove|undo|get rid of) (?:the |my |that )?last (?:session|entry|thing (?:you|i) logged)",
             lambda m: self.delete_last()),
            (r"(?:the |my |that )?last (?:session|entry|one) (?:was|should be|should have been) (?:actually )?%s|"
             r"(?:change|make|switch|move|mark) (?:the |my |that )?last (?:session|entry)(?: to| into| as) %s|"
             r"that (?:session )?was (?:actually )?%s(?: time)?" % (c, c, c), self.relabel),
            (r"(?:log|mark|count|save|put) (?:that|the|my) (?:gap|time away|away time|time i was away|break in "
             r"tracking)(?: as)? (?:a |an )?(%s|meeting)|i was (in a meeting|at the gym|working out|on a break|"
             r"studying|working) (?:while i was away|when i was away|just now|while away)" % CAT_WORDS, self.gap),
            # Goals and limits. "limit" is an at-most, "at least"/"target" an at-least; "goal" keeps what it was.
            (r"set (?:my |the )?%s%s (goal|limit|target|minimum|maximum) (?:to|at|of) (?:at least |at most )?%s"
             r"(?: a day| per day| a week| per week)?|limit (?:my )?%s to %s( a day| per day| a week| per week)?|"
             r"i want to %s at least %s a (day|week)" % (goal, c, d, c, d, c, d), self.set_goal),
            (r"(?:remove|clear|turn off|delete|drop|cancel) (?:my |the )?%s%s (?:goal|limit|target)" % (goal, c),
             self.clear_goal),
            (r"what are my (?:daily |weekly )?(?:goals|limits)|(?:read|tell me|list|show me) my (?:daily |weekly )?"
             r"(?:goals|limits)|(?:my )?(?:day ?tracker|tracker) goals", lambda m: self.goals()),
            (r"(?:remind me to take a break|nudge me to take a break|break reminders?) every %s|"
             r"(?:set )?(?:the |my )?break reminders? (?:to|at|after|for|every) %s|"
             r"(?:remind me|tell me) to (?:take a )?break after %s of (?:work|study|working|studying)" % (d, d, d),
             self.break_every),
            (r"(?:turn off|stop|disable|no more|mute) (?:the |my )?break reminders?|"
             r"(?:stop|don'?t) remind(?:ing)? me to take (?:a )?breaks?", lambda m: self.break_off()),
            (r"(?:turn on|start|enable) (?:the |my )?break reminders?", lambda m: self.break_on()),
            (r"(?:send|give|show) (?:me )?(?:my |the )?daily summary at %s|"
             r"(?:set |move )?(?:the |my )?daily summary(?: time| notification)? (?:to|at|for) %s" % (t, t),
             self.summary_at),
            (r"(?:turn off|stop|disable|no more) (?:the |my )?daily summary(?: notification)?", self.summary_at),
            (r"how (?:was|is|did|has) my week(?: been| go| going)?|(?:my |the )?(?:weekly|week'?s) summary|"
             r"(?:summary of|how did i spend) (?:my |the |this )?week|where did my week go|"
             r"how (?:much|long|many hours) (?:did|have) i (?:been )?%s(?:ed|ing)? this week" % c, self.week),
            (r"how (?:was|did) yesterday(?: go)?|yesterday'?s summary|summary (?:of|for) yesterday|"
             r"how did i spend yesterday|where did yesterday go|"
             r"how (?:much|long|many hours) did i %s(?:ed|ing)? yesterday" % c, self.yesterday),
            (r"(?:show|open)(?: me)? (?:the |my )?(?:big|fullscreen|full screen|focus|day ?tracker) (?:timer|clock)|"
             r"(?:full ?screen|focus) timer|(?:make|put) the timer full ?screen", lambda m: self.open_page("#focus")),
            (r"(?:open|show)(?: me)? (?:the |my )?day ?tracker(?: page| app)?", lambda m: self.open_page("")),
            (r"how (?:long|much time) (?:is )?left (?:on|in) (?:the |my )?(?:timer|countdown|session)|"
             r"how long (?:have i got|do i have) left", lambda m: self.current()),
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
        if s.get("active") and s["active"].get("plan"):
            a = s["active"]
            left = a["start"] / 1000 + a["plan"] / 1000 - now
            self.say("%s left of your %s of %s." % (spoken(max(left, 0)).capitalize(), spoken(a["plan"] / 1000),
                                                      NAMES[a["cat"]]))
        elif s.get("active"):
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

    # --- countdowns and time you forgot ---

    def _cat(self, m, words=CATS):
        return next(words[g.lower()] for g in m.groups() if g and g.lower() in words)

    @_guard
    def countdown(self, m):
        dur = seconds(next(g for g in m.groups() if g and seconds(g)))
        cat = self._cat(m)
        if not 60 <= dur <= 5 * 3600:
            self.say("A countdown can be from a minute to five hours.")
            return
        before = {}

        def change(state):
            now = time.time()
            a = state.get("active")
            before["cat"] = a and a["cat"]
            if a and a["cat"] == cat:  # the running timer becomes the countdown, from now
                a["plan"] = int((now - a["start"] / 1000 + dur) * 1000)
                return
            self._close(state, now)
            state["active"] = {"cat": cat, "start": int(now * 1000), "note": "", "plan": int(dur * 1000)}

        self._save(change)
        stopped = "Stopped %s. " % NAMES[before["cat"]] if before["cat"] and before["cat"] != cat else ""
        self.say("%s%s of %s, starting now. I'll tell you when it's up." % (stopped, spoken(dur).capitalize(),
                                                                           NAMES[cat]))

    def finish_countdown(self):
        """Every few seconds (skills/todos.py's watcher): a countdown that ran out is logged as ending on time, the
        way the tracker page does it, and said out loud, whoever logged it."""
        now = time.time()
        a = self._state().get("active")
        if a and a.get("plan") and now * 1000 < a["start"] + a["plan"]:
            self._counting = a
            return
        if a and a.get("plan"):
            def change(state):
                b = state.get("active")
                if b and b.get("plan") and b["start"] == a["start"]:
                    state.setdefault("entries", []).append({"id": new_id(), "cat": b["cat"], "start": b["start"],
                                                            "end": b["start"] + b["plan"], "note": b.get("note", "")})
                    state["active"] = None
            self._save(change)
        else:
            a, was = None, getattr(self, "_counting", None)
            # Gone, and it had run out: the page logged it. Gone early: you stopped it.
            if was and now * 1000 >= was["start"] + was["plan"]:
                a = was
        self._counting = None
        if a and now - (a["start"] + a["plan"]) / 1000 < COUNTDOWN_LATE:
            self.say("Time's up: %s of %s logged." % (spoken(a["plan"] / 1000), NAMES[a["cat"]]))

    def _add_entry(self, cat, start, end, note=""):
        self._save(lambda state: state.setdefault("entries", []).append(
            {"id": new_id(), "cat": cat, "start": int(start * 1000), "end": int(end * 1000), "note": note}))

    @_guard
    def log_time(self, m):
        groups = [g for g in m.groups() if g]
        dur = seconds(next(g for g in groups if seconds(g)))
        cat = self._cat(m, dict(CATS, **DID))
        yesterday = any(g.strip() == "yesterday" for g in groups)
        if not 60 <= dur <= 16 * 3600:
            self.say("I can log from a minute to sixteen hours.")
            return
        end = time.time() - (86400 if yesterday else 0)
        self._add_entry(cat, end - dur, end)
        self.say("Logged %s of %s%s." % (spoken(dur), NAMES[cat],
                                         " yesterday, ending at %s" % clock(end) if yesterday else ", ending now"))

    @_guard
    def log_span(self, m):
        g = m.groups()
        cat, rest = (CATS[g[0].lower()], g[1:8]) if g[0] else (DID[g[8].lower()], g[9:16])
        span = past_span(*rest[:6], yesterday=bool(rest[6]))
        if not span:
            self.say("I couldn't make a time span out of that. Try: log study from 6 to 7 pm.")
            return
        self._add_entry(cat, *span)
        self.say("Logged %s of %s, %s to %s%s." % (spoken(span[1] - span[0]), NAMES[cat], clock(span[0]),
                                                   clock(span[1]), " yesterday" if rest[6] else ""))

    # --- the running session and the last one ---

    @_guard
    def note(self, m):
        text = next(g for g in m.groups() if g).strip().rstrip(".")
        where = {}

        def change(state):
            if state.get("active"):
                state["active"]["note"] = text[:80]
                where["it"] = "the running session"
            elif state.get("entries"):
                max(state["entries"], key=lambda e: e["end"])["note"] = text[:80]
                where["it"] = "your last session"

        self._save(change)
        self.say("Noted on %s." % where["it"] if where else "There's no session to put a note on.")

    @_guard
    def discard(self):
        a = self._state().get("active")
        if not a:
            self.say("No timer is running.")
            return
        if not self.j.ask("Discard %s of %s without logging it?" % (spoken(time.time() - a["start"] / 1000),
                                                                    NAMES[a["cat"]])):
            self.say("Okay, it's still running.")
            return
        self._save(lambda state: state.update(active=None))
        self.say("Discarded.")

    def _last(self, state):
        """("entries" or "autoEntries", the session that ended last), or None."""
        both = [("entries", e) for e in state.get("entries") or []] + \
               [("autoEntries", e) for e in state.get("autoEntries") or []]
        return max(both, key=lambda kv: kv[1]["end"], default=None)

    def _describe(self, kind, e):
        what = NAMES.get(e["cat"], e["cat"])
        label = " (%s)" % e["label"] if kind == "autoEntries" and e.get("label") else ""
        return "%s of %s%s, %s to %s" % (spoken((e["end"] - e["start"]) / 1000), what, label,
                                         clock(e["start"] / 1000), clock(e["end"] / 1000))

    @_guard
    def delete_last(self):
        last = self._last(self._state())
        if not last:
            self.say("There's nothing logged to delete.")
            return
        kind, e = last
        if not self.j.ask("Delete %s?" % self._describe(kind, e)):
            self.say("Okay, kept it.")
            return
        if kind == "autoEntries":
            self._call("DELETE", "/api/auto/" + e["id"])
        else:
            self._save(lambda state: state.update(entries=[x for x in state["entries"] if x["id"] != e["id"]]))
        self.say("Deleted.")

    @_guard
    def relabel(self, m):
        cat = self._cat(m)
        last = self._last(self._state())
        if not last:
            self.say("There's nothing logged yet.")
            return
        kind, e = last
        if e["cat"] == cat:
            self.say("It's already %s." % NAMES[cat])
            return
        if kind == "autoEntries":
            self._call("PATCH", "/api/auto/" + e["id"], {"cat": cat})
        else:
            def change(state):
                for x in state["entries"]:
                    if x["id"] == e["id"]:
                        x["cat"] = cat
            self._save(change)
        self.say("Changed %s to %s." % (self._describe(kind, e), NAMES[cat]))

    @_guard
    def gap(self, m):
        said = next(g for g in m.groups() if g).lower()
        cat = {"meeting": "work", "in a meeting": "work", "at the gym": "workout", "working out": "workout",
               "on a break": "break", "studying": "study", "working": "work"}.get(said) or CATS[said]
        gap = self._state().get("gap")
        if not gap:
            self.say("There's no time away waiting to be logged.")
            return
        self._call("POST", "/api/gap", {"cat": cat, "note": "Meeting" if "meeting" in said else ""})
        self.say("Logged %s to %s as %s." % (clock(gap["start"] / 1000), clock(gap["end"] / 1000),
                                             "a meeting" if "meeting" in said else NAMES[cat]))

    # --- goals, break reminders, the daily summary ---

    @staticmethod
    def _weekly(m):
        text = m.group(0).lower()
        return bool(re.search(r"\bweek(?:ly|'?s)?\b", text))

    @_guard
    def set_goal(self, m):
        groups = [g for g in m.groups() if g]
        cat = self._cat(m)
        mins = round(seconds(next(g for g in groups if seconds(g))) / 60)
        weekly, text = self._weekly(m), m.group(0).lower()
        key = "weeklyGoals" if weekly else "goals"
        if not 1 <= mins <= (7 * 24 * 60 if weekly else 24 * 60):
            self.say("That's more time than there is.")
            return
        out = {}

        def change(state):
            goals = state.get(key) or {}
            g = goals.get(cat) or {"mins": 0, "kind": "max" if cat == "fun" else "min"}
            if re.search(r"\blimit\b|\bmaximum\b|at most", text):
                g["kind"] = "max"
            elif re.search(r"\btarget\b|\bminimum\b|at least", text):
                g["kind"] = "min"
            g["mins"] = mins
            goals[cat] = g
            state[key] = goals
            out["g"] = g

        self._save(change)
        self.say("%s %s for %s is now %s %s." % ("Weekly" if weekly else "Daily",
                                               "limit" if out["g"]["kind"] == "max" else "goal", NAMES[cat],
                                               "at most" if out["g"]["kind"] == "max" else "at least",
                                               spoken(mins * 60)))

    @_guard
    def clear_goal(self, m):
        cat, weekly = self._cat(m), self._weekly(m)
        key = "weeklyGoals" if weekly else "goals"

        def change(state):
            if (state.get(key) or {}).get(cat):
                state[key][cat]["mins"] = 0

        self._save(change)
        self.say("Cleared your %s %s goal." % ("weekly" if weekly else "daily", NAMES[cat]))

    @_guard
    def goals(self):
        s = self._state()
        parts = []
        for key, per in (("goals", "a day"), ("weeklyGoals", "a week")):
            for cat, g in (s.get(key) or {}).items():
                if g.get("mins") and cat in NAMES:
                    parts.append("%s %s %s %s" % (NAMES[cat], "at most" if g.get("kind") == "max" else "at least",
                                                  spoken(g["mins"] * 60), per))
        brk = s.get("breakAfter") or 0
        self.say(("Your goals: %s." % "; ".join(parts) if parts else "You haven't set any goals.") +
                 (" Break reminders after %s of work or study." % spoken(brk * 60) if brk else
                  " Break reminders are off."))

    @_guard
    def break_every(self, m):
        mins = round(seconds(next(g for g in m.groups() if g)) / 60)
        if not 5 <= mins <= 240:
            self.say("Break reminders can be from 5 minutes to 4 hours.")
            return
        self._save(lambda state: state.update(breakAfter=mins))
        self.say("I'll nudge you to take a break after %s of work or study." % spoken(mins * 60))

    @_guard
    def break_off(self):
        self._save(lambda state: state.update(breakAfter=0))
        self.say("Break reminders are off.")

    @_guard
    def break_on(self):
        def change(state):
            if not state.get("breakAfter"):
                state["breakAfter"] = 50
        self._save(change)
        self.say("Break reminders are on: after %s of work or study." % spoken(self._state()["breakAfter"] * 60))

    @_guard
    def summary_at(self, m):
        g = m.groups()
        if not any(g):
            self._save(lambda state: state.update(summaryAt=""))
            self.say("The daily summary is off.")
            return
        h, mm, ampm = g[0:3] if g[0] else g[3:6]
        hour = _hours(h, ampm)[-1]  # no am/pm: "at 9" is 9 pm, since a summary of the day comes in the evening
        if hour > 23 or int(mm or 0) > 59:
            self.say("That isn't a time.")
            return
        at = "%02d:%02d" % (hour, int(mm or 0))
        self._save(lambda state: state.update(summaryAt=at))
        self.say("The daily summary will come at %s." % clock(datetime.datetime.combine(
            datetime.date.today(), datetime.time(hour, int(mm or 0))).timestamp()))

    # --- yesterday and this week ---

    def _report(self, t, goals, what, scale=1):
        parts = ["%s %s" % (spoken(v), NAMES[c]) for c, v in sorted(t.items(), key=lambda kv: -kv[1])
                 if v >= 60 and c in NAMES]
        if not parts:
            return "Nothing tracked %s." % what
        line = "%s: %s." % (what.capitalize(), ", ".join(parts))
        notes = []
        for c, g in (goals or {}).items():
            if not g.get("mins") or c not in NAMES:
                continue
            goal, secs = g["mins"] * 60 * scale, t.get(c, 0)
            if g.get("kind") == "max" and secs > goal:
                notes.append("%s is %s over its limit" % (NAMES[c], spoken(secs - goal)))
            elif g.get("kind") != "max":
                notes.append("%s goal met" % NAMES[c] if secs >= goal else
                             "%s short of the %s goal" % (spoken(goal - secs), NAMES[c]))
        return line + (" " + "; ".join(notes).capitalize() + "." if notes else "")

    @_guard
    def yesterday(self, m):
        s = self._state()
        t = totals(s, now=midnight(0), since=midnight(1))
        if m.groups() and m.group(1):
            cat = CATS[m.group(1).lower()]
            self.say("%s of %s yesterday." % (spoken(t.get(cat, 0)).capitalize(), NAMES[cat]) if t.get(cat, 0) >= 60
                     else "No %s yesterday." % NAMES[cat])
            return
        self.say(self._report(t, s.get("goals"), "yesterday"))

    @_guard
    def week(self, m):
        s, now = self._state(), time.time()
        monday = midnight(datetime.date.fromtimestamp(now).weekday(), now)
        t = totals(s, since=monday)
        if m.groups() and m.group(1):
            cat = CATS[m.group(1).lower()]
            goal = (s.get("weeklyGoals") or {}).get(cat) or {}
            line = "%s of %s this week." % (spoken(t.get(cat, 0)).capitalize(), NAMES[cat])
            if goal.get("mins"):
                left = goal["mins"] * 60 - t.get(cat, 0)
                line += (" %s left before your weekly limit." % spoken(left).capitalize() if left >= 0 else
                         " That's %s over your weekly limit." % spoken(-left)) if goal.get("kind") == "max" else \
                    (" Weekly goal met." if left <= 0 else " %s to go for your weekly goal." % spoken(left).capitalize())
            self.say(line)
            return
        self.say(self._report(t, s.get("weeklyGoals"), "this week"))

    # --- the page ---

    def open_page(self, where):
        try:
            self._call("GET", "/api/ping")
        except HubDown:
            self.say("The day tracker isn't running. Start it with: systemctl --user start day-tracker.")
            return
        self.j.executor.run("xdg-open %s" % shlex.quote(HUB + "/" + where),
                            reply="Here's the big timer." if where else "Opening the day tracker.", window=True)
