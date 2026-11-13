"""Todos (ProducTVT's when it's set up, else the Day Tracker's), reminders and alarms (the Day Tracker's).

"add buy milk to my todo list", "what's on my todo list", "mark buy milk done", "remind me in 20 minutes to
stretch", "remind me at 5 pm to call Ravi", "what are my reminders", "cancel the stretch reminder".
A watcher thread says reminders out loud when they come due; the tracker page chimes for them as well.
"""
import datetime
import difflib
import math
import re
import struct
import subprocess
import threading
import time

from skills.base import Skill
from system import productvt
from skills.daytracker import DayTracker, HubDown, new_id, spoken
from skills.helpers import NTH, ORDINALS

TODO = r"(?:to ?-?dos?|to ?-?do ?list|task list|tasks)"
MAX_READ = 5
CARD = "todos"  # the to-do card's id; its ticks say "tick todo 3" / "untick todo 3"
SHORT = 60  # a to-do longer than this is said up to its first ":" or cut
LATE = 10 * 60  # a reminder that came due longer ago than this (JARVIS was off) is dropped quietly
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15,
         "twenty": 20, "thirty": 30, "forty five": 45, "a couple of": 2, "a few": 3, "half an": 0.5}
NUM = r"(\d+(?:\.\d+)?|%s)" % "|".join(sorted(WORDS, key=len, reverse=True))
IN = r"(?:in |after )?%s (seconds?|secs?|minutes?|mins?|hours?|hrs?)" % NUM
AT = r"(?:(today|tonight|tomorrow) )?(?:at )?(\d{1,2})(?:[:.](\d{2}))? ?([ap]\.? ?m\.?)?(?: (today|tonight|tomorrow))?"
# "remind me", "set a reminder", "I want you to set up a reminder": a reminder that may not say what it's for.
SET = (r"(?:(?:i want you to|i need you to|i'd like you to|i want to|i need to|go ahead and|just|to) )?"
       r"(?:remind me(?: (?:of |about )?something)?|(?:set|add|create|make|put|schedule|give me|get me|start)(?: up| in| me)?"
       r" (?:a |an |the |my |our |one )?(?:new )?(?:reminder|alarm)|(?:i'd like|i want|i need) (?:a |an )?(?:new )?"
       r"(?:reminder|alarm)|(?:new |a )?(?:reminder|alarm)(?: please)?|wake me(?: up)?)")
LATER = r"(?:in|for|after) %s (seconds?|secs?|minutes?|mins?|hours?|hrs?)" % NUM
NO_TEXT = "Reminder"  # what's kept when you didn't say what it's for
ALARM = "Alarm"  # "set an alarm for 7": not asked what for
ALARM_WORD = r"\b(?:alarms?|wake me)\b"
RING_FOR = 5 * 60  # an alarm nobody stops gives up after this
SAY_EVERY = 30  # while ringing, the time is said this often; chimes in between
SNOOZE = 10  # minutes
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# "every day", "on weekdays", "every monday": the repeat is taken out and the rest set as usual
REPEAT = re.compile(r"\s*\b(?:every ?day|daily|each day|every (?:morning|night|evening)|(?:every|on) (week ?days|weekends)|"
                    r"(?:every|on) (%s)s?)\b" % "|".join(DAYS), re.I)
HOURS = {w: n for n, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split(), 1)}
TEENS = {w: n for n, w in enumerate("ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), 10)}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}


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


def on_days(ts, repeat):
    """The first time at or after `ts` (same clock time) on a day `repeat` allows."""
    t = datetime.datetime.fromtimestamp(ts)
    for _ in range(8):
        day = t.weekday()
        if repeat == "daily" or (repeat == "weekdays" and day < 5) or (repeat == "weekends" and day >= 5) \
                or repeat == DAYS[day]:
            break
        t += datetime.timedelta(days=1)
    return t.timestamp()


def tidy(text):
    """"uh", "um" out; spoken clock times as digits ("at three twenty five" -> "at 3:25", "at seven" -> "at 7")."""
    text = re.sub(r"\b(?:uh+|um+|erm|uhm|hmm)\b,?\s*", "", text, flags=re.I).strip()

    def clock(m):
        h, rest = HOURS[m.group(1).lower()], (m.group(2) or "").lower().split()
        if not rest or rest == ["o'clock"]:
            return "at %d" % h
        if rest[0] == "oh" and len(rest) == 2 and rest[1] in HOURS and HOURS[rest[1]] < 10:
            mins = HOURS[rest[1]]
        elif rest[0] in TEENS and len(rest) == 1:
            mins = TEENS[rest[0]]
        elif rest[0] in TENS and len(rest) <= 2 and (len(rest) == 1 or (rest[1] in HOURS and HOURS[rest[1]] < 10)):
            mins = TENS[rest[0]] + (HOURS[rest[1]] if len(rest) == 2 else 0)
        else:
            return m.group(0)
        return "at %d:%02d" % (h, mins)

    word = r"(?:oh|o'clock|%s)" % "|".join(list(HOURS) + list(TEENS) + list(TENS))
    return re.sub(r"\bat (%s)((?: %s){0,2})\b" % ("|".join(HOURS), word), clock, text, flags=re.I)


def spoken_time(ts):
    t, today = datetime.datetime.fromtimestamp(ts), datetime.date.today()
    clock = t.strftime("%I:%M %p").lstrip("0").replace(":00", "")
    if t.date() == today:
        return "at " + clock
    return ("tomorrow at " if t.date() == today + datetime.timedelta(days=1) else t.strftime("%A at ")) + clock


class Todos(Skill):
    group = "reminders and to-dos"
    does = "To-dos and reminders, kept in the Day Tracker"
    phrases = ("remind me in 10 minutes to <x>", "remind me at 5 pm to <x>", "remind me every day at 9 to <x>",
               "what are my reminders", "move the <x> reminder to 6 pm", "cancel the <x> reminder",
               "wake me up at 7 (rings until stop / snooze)", "set an alarm on weekdays at 7", "what alarms do i have",
               "add <x> to my todo list", "what's on my todo list (a card; click a box to tick it)",
               "mark <x> done / mark the second one done", "delete <x> from my todo list", "clear my todo list")
    gives = "speech; said when a reminder is due"
    permission = "auto"
    checks = "saved in the Day Tracker and read back; a tracker that's down is said, never pretended"

    def __init__(self, jarvis, watch=True):
        super().__init__(jarvis)
        self.hub = DayTracker(jarvis)
        # With ProducTVT set up (the tray's config), todos live there, so its top-bar tray and web app show them.
        self.pv = productvt.Todos() if productvt.available() else None
        if watch:
            threading.Thread(target=self._watch, daemon=True, name="reminders").start()

    def handle(self, text):
        text = tidy(text)
        r = REPEAT.search(text)
        if r and re.search(r"\b(?:remind|reminder|alarm|wake me)\b", text, re.I):
            # "remind me every day at 9 to ...", "wake me up on weekdays at 7": set as usual, then repeated
            self._repeat = (r.group(1) or r.group(2) or "daily").lower().replace(" ", "")
            try:
                return super().handle((text[:r.start()] + text[r.end():]).strip())
            finally:
                self._repeat = None
        return super().handle(text)

    def patterns(self):
        """"what's pending" stays with project notes; these say "todo" or "remind"."""
        return [
            # a ringing alarm first: "stop the alarm" mustn't set a new one
            (r"(?:stop|turn off|switch off|dismiss|silence|shut off|kill|end)(?: the| that| my)?(?: alarm| ringing| it)?|"
             r"(?:turn|switch|shut) (?:it|that|the alarm) off|i'?m (?:up|awake)|alarm off|okay i'?m up",
             lambda m: self.stop_alarm() or (self.say("There's no alarm ringing.") if re.search(
                 r"alarm|ringing", m.group(0), re.I) and not re.match(r"turn|switch|shut", m.group(0), re.I) else False)),
            (r"snooze(?: it| that| the alarm| the reminder)?(?: for %s ?(minutes?|mins?))?|(?:give me )?%s more minutes?"
             % (NUM, NUM), self.snooze),
            (r"(?:what|which|list|show|read|any)(?: are)?(?: me)?(?: my| the)? alarms(?: do i have| are set| set)?|"
             r"(?:do i have|have i got|is there) (?:an |any )?alarms?(?: set)?(?: for tomorrow| tomorrow)?",
             lambda m: self.reminders(alarms=True)),
            (r"(?:move|change|push|reschedule|shift|update) (?:the |my |that )?(?:(.+?) )?(reminder|alarm)s?"
             r"(?: (?:about|for|to) (.+?))? (?:to|till|until|back to) (.+)", self.move),
            # A time but nothing to remind about ("set a reminder in 2 minutes"): asks what it's for.
            (r"%s %s" % (SET, LATER), self.remind_later),
            (r"%s (?:for )?%s" % (SET, AT), self.remind_clock),
            (r"%s(?: for me| please)?" % SET, self.remind_when),
            (r"remind me %s(?: to| about| that)? (.+)|remind me(?: to| about| that)? (.+?) %s" % (IN, IN), self.remind_in),
            (r"remind me %s(?: to| about| that)? (.+)|remind me(?: to| about| that)? (.+?) %s" % (AT, AT), self.remind_at),
            (r"(?:what are|read|list|show me|tell me|any) (?:my |the )?reminders(?: today| for today)?|"
             r"what (?:did i ask you to remind me|reminders do i have)(?: about)?", lambda m: self.reminders()),
            (r"(?:cancel|delete|remove|clear|turn off|switch off) (?:the |my |all )?(?:(.+?) )?(?:reminders?|alarms?)"
             r"(?: (?:about|for|to|at) (.+))?", self.cancel),
            (r"(un)?tick todo (\d+)", self.tick_n),  # a tick clicked on the to-do card
            (r"(?:add|put) (.+?) (?:to|on|in|onto) (?:(?:my|the) )?%s|(?:add|create|make)(?: a)?(?: new)? to ?-?do(?: to| for)?:? (.+)"
             % TODO, self.add),
            (r"(?:clear|empty|wipe)(?: out)? (?:my |the |all (?:of )?(?:my )?)?%s|(?:mark|tick off|check off) (?:all|everything)"
             r"(?: of them| on (?:my|the) %s)?(?: as)? done" % (TODO, TODO), lambda m: self.clear()),
            (r"(?:delete|remove|erase|drop|take|scratch) (?:the )?(.+?) (?:from|off|out of) (?:my |the )?%s|"
             r"(?:delete|remove) (?:the )?(.+?) (?:to ?-?do|task)" % (TODO, ), self.delete),
            (r"(?:untick|uncheck|unmark) (?:the )?(.+?)(?: to ?-?do| task)?|mark (?:the )?(.+?) (?:as )?"
             r"(?:not done|undone|open|not finished)", self.undone),
            (r"(?:mark|tick|tick off|check off|cross off) (?:the )?(.+?)(?: to ?-?do| task)?(?: as)?"
             r"(?: done| complete| completed| finished)?|(?:the )?(%s(?: one)?|number \d\d?) (?:to ?-?do |task )?is (?:done|finished)"
             % NTH, self.done),
            (r"(?:what'?s|what is|what are|read(?: me)?|show(?: me)?|open|list|tell me|give me)(?: on| in)?(?: my| the)? %s(?: today)?|"
             r"(?:my |the )?%s|(?:how many|any) %s (?:do i have|left|open)(?: today)?" % (TODO, TODO, TODO),
             lambda m: self.read()),
        ]

    def _guard(fn):
        def wrapped(self, *a, **k):
            try:
                return fn(self, *a, **k)
            except HubDown:
                self.say("The day tracker isn't running. Start it with: systemctl --user start day-tracker.")
            except productvt.Unavailable as e:
                self.say(str(e))
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

    def _open(self):
        """Open todos, newest first, as {text, ...}: from ProducTVT when it's set up, else the Day Tracker."""
        if self.pv:
            return [{"id": t["id"], "text": t["title"]} for t in self.pv.open()]
        todos = [t for t in self._list("todos") if not t.get("done")]
        return sorted(todos, key=lambda t: -t.get("created", 0))

    def open_todos(self):
        """Open to-dos for the briefing and project cards ([] when the store is down)."""
        try:
            return self._open()
        except (HubDown, productvt.Unavailable):
            return []

    def _card(self, todos=None):
        """Show the to-dos as a checklist card; its ticks click through to tick_n."""
        if todos is not None:
            self.shown = [dict(t, done=False) for t in todos]
        open_ = sum(not t["done"] for t in self.shown)
        self.j.show({"id": CARD, "title": "To-dos (%d open)" % open_, "tick": "tick todo {n}", "untick": "untick todo {n}",
                     "text": "\n".join("- [%s] %s" % ("x" if t["done"] else " ", t["text"]) for t in self.shown)
                     or "Nothing open.", "stay": False})

    def _card_up(self):
        ui = self.j.ui
        return bool(ui and ui.card_open(CARD))

    @_guard
    def read(self):
        todos = self._open()
        if not todos:
            self.say("Your todo list is clear.")
            return
        self._card(todos)
        n = len(todos)
        if n <= 3 and all(len(t["text"]) <= SHORT for t in todos):
            self.say("You have %d open todo%s: %s." % (n, "" if n == 1 else "s", "; ".join(t["text"] for t in todos)))
        else:
            self.say("You have %d open todos, they're on the card. The newest is %s." % (n, _short(todos[0]["text"])))

    @_guard
    def add(self, m):
        text = next(g for g in m.groups() if g).strip(" .")
        if text.lower().startswith("to "):  # "add a todo to call Ravi"
            text = text[3:]
        text = text[:1].upper() + text[1:]
        if self.pv:
            self.pv.add(text[:200])
        else:
            self._change("todos", lambda items: items.append(
                {"id": new_id(), "text": text[:200], "done": False, "created": int(time.time() * 1000)}))
        self.say(["Added %s to your todos." % text,
                  "%s is on the list. I'll make sure it doesn't escape." % text,
                  "Added %s. The list grows." % text, "Okay, %s is on your list." % text,
                  "Noted: %s, on your todos." % text])
        if self._card_up():
            self._card(self._open())

    def _find(self, want, todos):
        """The to-dos `want` names: "the second one" / "number 2" (as on the card or last read), the exact text,
        words in it, or a close spelling."""
        want = re.sub(r"^(?:the |number |no\.? )+|(?: one| item| todo| task)$", "", want.strip().lower())
        if want in ("it", "that", "this", "them", "all"):
            return []
        n = ORDINALS.get(want) or (int(want) if want.isdigit() else 0)
        if n:
            listed = getattr(self, "shown", None) or todos
            n = len(listed) if n == -1 else n
            pick = listed[n - 1] if 0 < n <= len(listed) else None
            return [t for t in todos if pick and t["id"] == pick["id"]]
        texts = [t["text"].lower() for t in todos]
        hits = [t for t, x in zip(todos, texts) if x == want] or [t for t, x in zip(todos, texts) if want in x]
        if not hits:
            words = set(re.findall(r"\w+", want)) - {"the", "a", "to", "my"}
            hits = [t for t, x in zip(todos, texts) if words and words <= set(re.findall(r"\w+", x))]
        if not hits:
            hits = [todos[texts.index(c)] for c in difflib.get_close_matches(want, texts, n=1, cutoff=0.5)]
        return hits

    def _set(self, hits, done):
        """Tick (or untick) one to-do in the store and on the card."""
        t = hits[0]
        if self.pv:
            self.pv.set_done(t["id"], done)
        else:
            def flip(items):
                for i in items:
                    if i["id"] == t["id"]:
                        i["done"] = done
            self._change("todos", flip)
        for s in getattr(self, "shown", None) or ():
            if s["id"] == t["id"]:
                s["done"] = done
        if self._card_up():
            self._card()

    def _all(self):
        """The open to-dos as {id, text, done}."""
        return [dict(t, done=False) for t in self._open()]

    @_guard
    def done(self, m):
        want = next(g for g in m.groups() if g)
        todos = self._all()
        hits = self._find(want, todos)
        if not hits:
            return False  # not a todo: let the request carry on
        if len(hits) > 1:
            self.say("Which one? %s." % " or ".join(_short(t["text"]) for t in hits[:3]))
            return
        self._set(hits, True)
        self.say(["Done: %s." % _short(hits[0]["text"]),
                  "Done: %s. That's one less thing." % _short(hits[0]["text"]),
                  "%s, ticked off. Feels good, doesn't it?" % _short(hits[0]["text"]),
                  "Ticked off: %s." % _short(hits[0]["text"]),
                  "Nicely done, that's %s off the list." % _short(hits[0]["text"])])

    @_guard
    def undone(self, m):
        """Reopen a ticked to-do: one ticked on the card (or, in the Day Tracker, any ticked one)."""
        want = next(g for g in m.groups() if g)
        ticked = [t for t in getattr(self, "shown", None) or () if t["done"]]
        if not self.pv and not ticked:
            ticked = [dict(t, done=True) for t in self._list("todos") if t.get("done")]
        hits = [t for t in self._find(want, getattr(self, "shown", None) or ticked) if t["done"]]
        if not hits:
            return False  # "uncheck dark mode" and the like aren't about to-dos
        if len(hits) > 1:
            self.say("Which one? %s." % " or ".join(_short(t["text"]) for t in hits[:3]))
            return
        self._set(hits, False)
        self.say(["Okay, %s is open again." % _short(hits[0]["text"]),
                  "%s is back on the list. It wasn't quite done." % _short(hits[0]["text"]),
                  "Back on the list: %s." % _short(hits[0]["text"])])

    @_guard
    def tick_n(self, m):
        """A tick clicked on the to-do card: quiet, it's already ticked there."""
        shown = getattr(self, "shown", None) or []
        n = int(m.group(2))
        if not 0 < n <= len(shown):
            return
        self._set([shown[n - 1]], not m.group(1))
        self.j.chat("sys", "%s: %s" % ("reopened" if m.group(1) else "done", shown[n - 1]["text"]))

    @_guard
    def delete(self, m):
        want = next(g for g in m.groups() if g)
        hits = self._find(want, self._all())
        if not hits:
            self.say("I couldn't find that on your todo list.")
            return
        if len(hits) > 1:
            self.say("Which one? %s." % " or ".join(_short(t["text"]) for t in hits[:3]))
            return
        t = hits[0]
        if self.pv:
            self.pv.delete(t["id"])
        else:
            self._change("todos", lambda items: items.remove(next(i for i in items if i["id"] == t["id"])))
        if getattr(self, "shown", None):
            self.shown = [s for s in self.shown if s["id"] != t["id"]]
            if self._card_up():
                self._card()
        self.say(["Removed %s from your todos." % _short(t["text"]),
                  "%s is off the list. Not done, just gone." % _short(t["text"]),
                  "Off your todos: %s." % _short(t["text"])])

    @_guard
    def clear(self):
        todos = self._all()
        if not todos:
            self.say("Your todo list is already clear.")
            return
        if not self.j.ask("Tick off all %d of your to-dos?" % len(todos)):
            self.say(["Okay, I left them.",
                      "Fine, they stay.", "Alright, they stay."])
            return
        for t in todos:
            self._set([t], True)
        self.say(["Done, all %d ticked off." % len(todos),
                  "All %d ticked off. Look at you go." % len(todos), "All %d ticked off. A clean list." % len(todos)])

    # --- reminders ---

    def _remind(self, text, at):
        text = text.strip(" .")
        repeat = getattr(self, "_repeat", None)
        if repeat:
            at = on_days(at, repeat)
        item = {"id": new_id(), "text": text[:200], "at": int(at * 1000), "fired": False}
        if repeat:
            item["repeat"] = repeat
        self._change("reminders", lambda items: items.append(item))
        when = "in %s" % _in_words(at - time.time()) if at - time.time() <= 3 * 3600 else spoken_time(at)
        if repeat:
            when = _every(repeat, at) + ("" if repeat == "daily" else ", starting " + spoken_time(at))
        self.say(["Okay, alarm set %s." % when, "Alarm set, %s." % when,
                  "Right, I'll wake you %s." % when] if text == ALARM
                 else ["Okay, I'll remind you %s." % when,
                       "Alarm set %s. I'll be the one shouting." % when,
                       "Right, I'll get you up %s." % when, "I'll remind you %s." % when])

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
    def remind_later(self, m):
        self._remind(self._what(m), due_in(m.group(1), m.group(2)))

    @_guard
    def remind_clock(self, m):
        at = due_at(*m.groups())
        if at is None:
            return False
        self._remind(self._what(m), at)

    @_guard
    def remind_when(self, m):
        """Just "set a reminder": asks when (and what for) and goes on with the answer. An alarm: only when."""
        alarm = re.search(ALARM_WORD, m.group(0), re.I)
        answer = (self.j._answer("Sure, what time?" if alarm else "Sure, when? And what for?") or "")
        answer = answer.replace(",", "").strip(" .!?")
        if not answer or re.match(r"(?:no|nothing|never ?mind|cancel|forget it)\b", answer, re.I):
            self.say("Okay, no alarm." if alarm else "Okay, no reminder.")
            return
        lead = "set an alarm " if alarm else "remind me "
        if not self.handle(lead + re.sub(r"^(?:remind me|set an alarm)(?: for)? ", "", answer, flags=re.I)):
            self.say("Sorry, I didn't get the time. Say something like: remind me in 10 minutes to stretch.")

    def _what(self, m):
        """Asks what a reminder is for; NO_TEXT when there's no answer. An alarm isn't asked about."""
        if re.search(ALARM_WORD, m.group(0), re.I):
            return ALARM
        answer = (self.j._answer("Sure. What should I remind you about?") or "").strip(" .!?")
        answer = re.sub(r"^(?:to|about|that) ", "", answer, flags=re.I)
        if not answer or re.match(r"(?:no|nothing|nope|just remind me|anything)\b", answer, re.I):
            return NO_TEXT
        return answer

    @_guard
    def reminders(self, alarms=False):
        now = time.time() * 1000
        rems = sorted((r for r in self._list("reminders") if r["at"] > now and (not alarms or r["text"] == ALARM)),
                      key=lambda r: r["at"])
        if not rems:
            self.say("You have no alarms set." if alarms else "You have no reminders coming up.")
            return
        self.say("; ".join("%s %s%s" % ("Alarm" if r["text"] == ALARM else r["text"], spoken_time(r["at"] / 1000),
                                        ", " + _every(r["repeat"], r["at"] / 1000) if r.get("repeat") else "")
                           for r in rems[:MAX_READ]) + ".")

    @_guard
    def cancel(self, m):
        want = " ".join(g for g in m.groups() if g).lower().strip()
        now = time.time() * 1000
        alarm = re.search(ALARM_WORD, m.group(0), re.I)
        if alarm and want not in ("a", "an", "one", "some", "that", "the", "my", "all", ""):  # "cancel the 7 am alarm"
            want = re.sub(r"[^a-z0-9]", "", want)

        def drop(items):
            live = [r for r in items if r["at"] > now and (r["text"] == ALARM) == bool(alarm)]
            if alarm and want and want not in ("a", "an", "one", "some", "that", "the", "my", "all"):
                hits = [r for r in live if want in re.sub(r"[^a-z0-9]", "", spoken_time(r["at"] / 1000).lower())]
                if len(hits) == 1:
                    items.remove(hits[0])
                return hits
            if want in ("a", "an", "one", "some"):  # "cancel a reminder": the only one, or ask which
                if len(live) == 1:
                    items.remove(live[0])
                return live
            hits = [r for r in live if want in r["text"].lower()] if want and want not in ("that", "the", "my", "all") else \
                (live if want == "all" else live[-1:])  # "cancel that reminder": the newest one
            if len(hits) == 1 or want == "all":
                for r in hits:
                    items.remove(r)
            return hits

        hits = self._change("reminders", drop)
        if not hits:
            self.say("I couldn't find that alarm." if alarm else "I couldn't find that reminder.")
        elif len(hits) > 1 and want != "all":
            self.say("Which one? %s." % " or ".join(r["text"] for r in hits[:3]))
        else:
            text = hits[0]["text"]
            self.say("Cancelled %s." % (("all alarms" if alarm else "all reminders") if want == "all" else
                                        "the alarm" if text == ALARM else
                                        "the reminder" if text == NO_TEXT else "the reminder to " + text))

    @_guard
    def move(self, m):
        """"move the stretch reminder to 6 pm", "change my alarm to 7:30", "push it to in 10 minutes"."""
        name, kind, about, when = m.group(1) or "", m.group(2).lower(), m.group(3) or "", m.group(4)
        at = parse_when(when)
        if at is None:
            return False
        alarm, want = kind == "alarm", (name + " " + about).strip().lower()
        now = time.time() * 1000

        def change(items):
            live = [r for r in items if r["at"] > now and (r["text"] == ALARM) == alarm]
            hits = [r for r in live if want in r["text"].lower()] if want and want not in ("that", "the", "my", "it") \
                else live
            if len(hits) == 1:
                hits[0]["at"], hits[0]["said"] = int((on_days(at, hits[0]["repeat"]) if hits[0].get("repeat") else at) * 1000), False
            return hits

        hits = self._change("reminders", change)
        if not hits:
            self.say("I couldn't find that %s." % kind)
        elif len(hits) > 1:
            self.say("Which one? %s." % " or ".join("%s %s" % (r["text"], spoken_time(r["at"] / 1000)) for r in hits[:3]))
        else:
            self.say("Okay, moved %s to %s." % ("the alarm" if alarm else "it", spoken_time(hits[0]["at"] / 1000)))

    def ring(self, r):
        """An alarm going off: a chime and the time said, until "stop" / "snooze" or RING_FOR runs out."""
        self.stop_alarm(quiet=True)
        stop = self.ringing = threading.Event()
        self.rang = r
        clock = spoken_time(r["at"] / 1000).replace("at ", "", 1)
        line = "It's %s. Time to wake up." % clock if r["text"] == ALARM else "It's %s. %s." % (clock, r["text"])
        if self.j.ui:
            self.j.ui.show_card({"id": "alarm", "title": "Alarm", "stay": True,
                                 "text": "# %s\n%s" % (clock, "" if r["text"] == ALARM else r["text"]),
                                 "choices": [{"label": "Stop", "do": "stop the alarm"},
                                             {"label": "Snooze %d min" % SNOOZE, "do": "snooze the alarm"}]})
        self.j.skills.proactive.note("alarm", line, "You set an alarm for %s." % clock)

        def loop():
            start, said = time.time(), 0
            while not stop.is_set() and time.time() - start < RING_FOR:
                if time.time() - said >= SAY_EVERY:
                    self.say(line)
                    self.j.follow_up()  # "stop" / "snooze" without the name
                    said = time.time()
                elif not self.j.speaker.busy():
                    chime()
                stop.wait(3)
            if not stop.is_set():
                stop.set()
                if self.j.ui:
                    self.j.ui.close_card("alarm")
                self.say("I stopped the alarm after %d minutes." % (RING_FOR // 60))

        threading.Thread(target=loop, daemon=True, name="alarm").start()

    def stop_alarm(self, quiet=False):
        """Stop a ringing alarm; False (nothing ringing) lets "stop" mean what it usually does."""
        ringing = getattr(self, "ringing", None)
        if not ringing or ringing.is_set():
            return False
        ringing.set()
        self.j.speaker.stop()
        if self.j.ui:
            self.j.ui.close_card("alarm")
        if not quiet:
            self.say(["Alarm off.", "Okay, it's off. Good morning.",
                      "Alarm off. Up and at them.", "Alarm's off. Rise and shine."])
        return True

    @_guard
    def snooze(self, m):
        r = getattr(self, "rang", None)
        if not r or not getattr(self, "ringing", None) or time.time() - r["at"] / 1000 > RING_FOR + 60:
            self.say("There's no alarm ringing.")
            return
        g = [x for x in m.groups() if x]
        minutes = (WORDS.get(g[0].lower()) or float(g[0])) if g and (g[0].lower() in WORDS or g[0].replace(".", "").isdigit()) \
            else SNOOZE
        self.stop_alarm(quiet=True)
        at = time.time() + minutes * 60
        self._change("reminders", lambda items: items.append(
            {"id": new_id(), "text": r["text"], "at": int(at * 1000), "fired": False, "alarm": True}))
        self.say(["Okay, snoozing for %s." % _in_words(minutes * 60),
                  "Fine, %s more. I'll be back." % _in_words(minutes * 60),
                  "Snoozing %s. Enjoy it." % _in_words(minutes * 60),
                  "Snoozed. %s more." % _in_words(minutes * 60).capitalize()])

    def _watch(self):
        while True:
            time.sleep(5)
            try:
                self.hub.finish_countdown()  # "study for 25 minutes" ran out: logged and said
                now = time.time() * 1000
                if not any(not r.get("said") and r["at"] <= now for r in self._list("reminders")):
                    continue
                due = []

                def mark(items):
                    for r in items:
                        if not r.get("said") and r["at"] <= now:
                            if now - r["at"] < LATE * 1000:
                                due.append(dict(r))
                            if r.get("repeat"):  # the next one: same time on the next day it's for
                                at = r["at"] / 1000
                                while at * 1000 <= now:
                                    at = on_days(at + 86400, r["repeat"])
                                r["at"] = int(at * 1000)
                            else:
                                r["said"] = True

                self._change("reminders", mark)
                for r in due:
                    if r["text"] == ALARM or r.get("alarm"):
                        self.ring(r)
                    else:
                        self.say(["This is your reminder.",
                                  "Here's that reminder.", "Your reminder, as asked."] if r["text"] == NO_TEXT else
                                 ["Reminder: %s." % r["text"],
                                  "Remember this: %s." % r["text"], "A reminder: %s." % r["text"]])
            except Exception as e:  # hub down or restarting: try again next round
                if not isinstance(e, HubDown):
                    print("[reminders] %r" % e)


def _in_words(secs):
    secs = round(secs)
    if secs < 60:
        return "%d seconds" % secs
    return spoken(round(secs / 60) * 60)


def _short(text):
    """A long to-do said up to its first ":" (or cut at a word), so it's not read out in full."""
    if len(text) <= SHORT:
        return text
    head = text.split(":")[0]
    return head if 8 <= len(head) <= SHORT else text[:SHORT].rsplit(" ", 1)[0]


def parse_when(text):
    """"6 pm", "7:30", "tomorrow at 9", "in 10 minutes" -> a timestamp, or None."""
    text = tidy(text.strip(" .")).lower()
    m = re.fullmatch(r"(?:in |after )?%s (seconds?|secs?|minutes?|mins?|hours?|hrs?)(?: from now)?" % NUM, text)
    if m:
        return due_in(m.group(1), m.group(2))
    m = re.fullmatch(AT, text)
    return due_at(*m.groups()) if m and m.group(2) else None


def _every(repeat, at):
    clock = spoken_time(at).split("at ")[-1]
    return {"daily": "every day", "weekdays": "on weekdays", "weekends": "at weekends"}.get(
        repeat, "every " + repeat.capitalize()) + " at " + clock


def chime():
    """Three short beeps (an alarm ringing). Blocks for about a second; never raises."""
    tone = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 988 * i / 22050))) for i in range(3300))
    gap = b"\0\0" * 2200
    try:
        subprocess.run(["aplay", "-q", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"], input=(tone + gap) * 3,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
    except (OSError, subprocess.SubprocessError):
        pass
