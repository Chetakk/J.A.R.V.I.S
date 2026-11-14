"""The daily briefing: "brief me", "what's my day look like", "what do I have today", "morning briefing".

One card and a short spoken line, from what the Day Tracker already keeps: today's reminders, open to-dos,
yesterday's time (and the projects it went on), today's goals and the week's. It also comes by itself once a day,
the first time you're around in the morning (settings briefing; "turn off the daily briefing").
Today's Google Calendar meetings lead it once Google is connected (skills/google_calendar.py).

Also drawn from the same data: "how did I spend my day" (a donut of today's time and the goals as rings), "show my
week" (a grid of each day's hours over four weeks) and "show my schedule" (today's reminders on a timeline; "show my
calendar" opens Google Calendar)."""
import datetime
import time

from skills.base import Skill
from skills.daytracker import NAMES, DayTracker, HubDown, spoken, totals
from skills.helpers import and_list
from skills.todos import _short

SHOW = 8  # to-dos on the card
APP = "VS Code · "  # Day Tracker labels: "VS Code · J.A.R.V.I.S"
WHY = ("It's your daily briefing: the first time you were around this morning. Say turn off the daily briefing "
       "to stop it.")


class Briefing(Skill):
    group = "briefing"
    does = "The day's briefing from the Day Tracker: reminders, to-dos, yesterday, goals; your day, week and calendar"
    phrases = ("brief me", "what's my day look like", "turn off the daily briefing",
               "how did I spend my day (a donut of today's time, goals as rings)",
               "show my week (a grid of hours a day over four weeks)", "show my schedule (today's reminders on a timeline; \"what's on my calendar\" is Google's once connected)")
    gives = "a card and a short spoken line"
    permission = "auto"
    checks = "says when the Day Tracker isn't running rather than an empty day"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.hub = DayTracker(jarvis)

    def patterns(self):
        return [
            (r"how (?:did|have) i spen[dt] (?:my |the )?(?:day|time)(?: today)?|where did (?:my |the )?(?:day|time) go"
             r"(?: today)?|(?:show |what'?s )?(?:me )?(?:my |the )?(?:time|day) (?:breakdown|split|in time)(?: today)?|"
             r"(?:show |what'?s )?(?:me )?(?:my )?time (?:spent )?today", lambda m: self.day_card()),
            (r"(?:show |give me )?(?:me )?(?:my |the )?week at a glance|(?:show|open) (?:me )?my week|how was my week"
             r"(?: so far)?|how (?:much|many hours) did i (?:work|code|study) this (?:week|month)",
             lambda m: self.week_card()),
            (r"(?:show |open |what'?s (?:on )?)(?:me )?(?:my |the )?(?:calendar|schedule|timeline)(?: for)?(?: today)?|"
             r"(?:my |today'?s )schedule(?: today)?", lambda m: self.calendar_card()),
            (r"(?:turn|switch) (?:on|off) (?:the |my )?(?:daily |morning |automatic )?briefings?|"
             r"(?:turn|switch) (?:the |my )?(?:daily |morning |automatic )?briefings? (?:on|off)|"
             r"(?:stop|start) (?:the |my )?(?:daily |morning )?briefings(?: every (?:day|morning))?|"
             r"(?:stop|start) (?:giving me|doing) (?:the |a |my )?(?:daily |morning )?briefings?"
             r"(?: every (?:day|morning))?|"
             r"(?:don'?t|no more|never) (?:brief me|(?:give me )?(?:a |the )?(?:daily |morning )?briefings?)"
             r"(?: every (?:day|morning)| in the morning)?", self.set_auto),
            (r"(?:give me |read me |do |start |what'?s )?(?:my |the |a |today'?s )?(?:daily |morning |day'?s )?"
             r"(?:briefing|rundown)(?: for today| for the day)?|brief ?me(?: on (?:my |the )?day| for (?:the|to)day)?|"
             r"what (?:does|will) (?:my |the )?day look like(?: today)?|how does (?:my |the )?day look(?: today)?|"
             r"what'?s my day (?:look(?:ing)? like|got(?: for me)?)|what'?s (?:on )?(?:my |the )?(?:plan|agenda) "
             r"(?:for )?today|what (?:do i have|have i got|is there) (?:on |going on |to do )?today|"
             r"what'?s on (?:for )?today|what'?s on my plate(?: today)?|what'?s happening today",
             lambda m: self.brief()),
        ]

    def brief(self):
        card, line = self.build()
        self.j.memory.set_pref("briefed", time.strftime("%Y-%m-%d"))
        self.j.show(card)
        self.say(line)

    def set_auto(self, m):
        on = not any(w in m.group(0).lower().split() for w in ("off", "stop", "don't", "dont", "no", "never"))
        self.j.memory.set_pref("briefing_auto", on)
        self.say("Okay, I'll brief you the first time you're around each morning." if on else
                 "Okay, no more morning briefings. Just say brief me when you want one.")

    def watch(self):
        """Every 10 s from jarvis.watch: the morning briefing, once a day, when you're around."""
        cfg = self.j.settings.get("briefing") or {}
        if not self.j.memory.pref("briefing_auto", cfg.get("auto", True)):
            return
        if self.j.memory.pref("briefed") == time.strftime("%Y-%m-%d"):
            return
        if not cfg.get("from", 5) <= time.localtime().tm_hour < cfg.get("until", 12):
            return
        tell = self.j.skills.proactive
        if time.time() - self.j.memory.data.get("last_seen", 0) > 60 or tell.holding() or self.j.asking:
            return  # not around yet, focusing, quiet hours, or answering something
        self.j.memory.set_pref("briefed", time.strftime("%Y-%m-%d"))
        card, line = self.build()
        if tell.tell("briefing", line, WHY, hold=False):
            self.j.inbox.put(("ctl", {"show": card}))

    def build(self):
        """(the card, the spoken line)."""
        now = time.time()
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        date = time.strftime("%A %-d %B", time.localtime(now))
        try:
            state = self.hub._state()
        except HubDown:
            return ({"title": "Briefing: " + date, "text": "The Day Tracker isn't running, so there are no to-dos, "
                     "reminders or time to show. Start it with: `systemctl --user start day-tracker`"},
                    "It's %s. The day tracker isn't running, so I can't see your to-dos or reminders." % date)
        reminders = sorted((r for r in state.get("reminders") or []
                            if now * 1000 <= r["at"] < (midnight + 86400) * 1000), key=lambda r: r["at"])
        todos = self.j.skills.todos.open_todos()  # ProducTVT's when it's set up, else the tracker's; newest first
        yday = totals(state, now=midnight - 1)
        today = totals(state, now=now)
        monday = midnight - time.localtime(now).tm_wday * 86400  # the tracker's weeks start on Monday
        week = totals(state, now=now, since=monday)
        worked_on = _projects(state.get("autoEntries") or [], midnight - 86400, midnight)

        lines, said = [], ["It's %s." % date]
        calendar = getattr(self.j.skills, "calendar", None)
        meet_lines, meet_said = calendar.for_briefing() if calendar else ([], "")  # Google Calendar, when connected
        # one "Today" timeline of meetings and reminders, as on the dashboard (the reminders alone without Google)
        lines += ["**Today**"] + (meet_lines or self.j.skills.dashboard._today(()) or
                                  ["{ok} Nothing on today's calendar"])
        if meet_said:
            said.append(meet_said)
        gmail = getattr(self.j.skills, "gmail", None)
        mail_lines, mail_said = gmail.for_briefing() if gmail else ([], "")
        if mail_lines:
            lines += [""] + mail_lines
        if mail_said:
            said.append(mail_said)
        if reminders:
            first = reminders[0]
            said.append("%s: %s at %s." % ("One reminder" if len(reminders) == 1 else "%d reminders, first" % len(
                reminders), first["text"], _clock(first["at"] / 1000)))
        if todos:
            lines += ["", "**To-dos (%d open)**" % len(todos)] + ["- " + t["text"] for t in todos[:SHOW]]
            if len(todos) > SHOW:
                lines.append("- and %d more" % (len(todos) - SHOW))
            said.append("%s, the newest is %s." % ("One open to-do" if len(todos) == 1 else
                                                    "%d open to-dos" % len(todos), _short(todos[0]["text"])))
        else:
            lines += ["", "**To-dos**", "- None open"]
        goals = _goals(state.get("goals") or {}, today)
        if goals:
            lines += ["", "**Goals today**", _rings(state.get("goals") or {}, today)]  # (the rings say it all)
        weekly = _goals(state.get("weeklyGoals") or {}, week, days_left=6 - time.localtime(now).tm_wday)
        if weekly:
            lines += ["", "**This week**"] + weekly
        spent = [(c, v) for c, v in sorted(yday.items(), key=lambda kv: -kv[1]) if v >= 60 and c in NAMES]
        if spent:
            lines += ["", "**Yesterday**", _stack("", spent),
                      "- " + ", ".join("%s %s" % (spoken(v), NAMES[c]) for c, v in spent)]
            if worked_on:
                lines.append("- Most time on " + ", ".join("%s (%s)" % (n, spoken(s)) for n, s in worked_on))
            said.append("Yesterday: %s%s." % (and_list("%s %s" % (spoken(v), NAMES[c]) for c, v in spent[:2]),
                                               ", mostly on %s" % worked_on[0][0] if worked_on else ""))
        if not (reminders or todos or spent):
            said.append("No reminders or to-dos, and nothing tracked yesterday.")
        said.append("It's on your screen.")
        card = {"title": "Briefing: " + date, "text": "\n".join(lines).strip()}
        if mail_said:
            card["choices"] = [{"label": "Read them", "do": "any new emails"}]
        return card, " ".join(said)


    # --- drawn cards from the same data ---

    def _state_or_say(self):
        try:
            return self.hub._state()
        except HubDown:
            self.say("The Day Tracker isn't running, so I can't see your time. Start it with systemctl --user "
                     "start day-tracker.")
            return None

    def day_card(self):
        state = self._state_or_say()
        if state is None:
            return
        today = totals(state)
        spent = [(c, v) for c, v in sorted(today.items(), key=lambda kv: -kv[1]) if v >= 60 and c in NAMES]
        if not spent:
            self.say("Nothing's been tracked today yet.")
            return
        lines = ["```donut\n%s\nunit: h\n```" % "\n".join("%s: %.1f" % (NAMES[c].capitalize(), v / 3600)
                                                           for c, v in spent)]
        goals = state.get("goals") or {}
        if _rings(goals, today):
            lines += ["", "**Goals**", _rings(goals, today)] + _goals(goals, today)
        self.j.show({"id": "day", "title": "Your day: " + time.strftime("%A %-d %B"), "text": "\n".join(lines)})
        self.say("Today so far: %s." % and_list("%s %s" % (spoken(v), NAMES[c]) for c, v in spent[:3]))

    def week_card(self, weeks=4):
        state = self._state_or_say()
        if state is None:
            return
        now = time.time()
        today = datetime.date.today()
        days, cats = [], {}
        for back in range(weeks * 7 - 1, -1, -1):
            day = today - datetime.timedelta(days=back)
            start = time.mktime(day.timetuple())
            got = totals(state, now=min(now, start + 86400 - 1), since=start)
            work = sum(v for c, v in got.items() if c in ("work", "study"))
            days.append("%s: %.1f" % (day.isoformat(), work / 3600))
            if back < 7:
                for c, v in got.items():
                    if c in NAMES:
                        cats[c] = cats.get(c, 0) + v
        week = [(c, v) for c, v in sorted(cats.items(), key=lambda kv: -kv[1]) if v >= 60]
        lines = ["**Work and study, hours a day**", "```heat\n%s\n```" % "\n".join(days)]
        if week:
            lines += ["", "**Last 7 days**", _stack("", week)]
        self.j.show({"id": "week", "title": "Week at a glance", "text": "\n".join(lines)})
        self.say("The last 7 days: %s." % and_list("%s %s" % (spoken(v), NAMES[c]) for c, v in week[:3])
                 if week else "Nothing's been tracked in the last week.")

    def calendar_card(self):
        state = self._state_or_say()
        if state is None:
            return
        now = time.time()
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        reminders = sorted((r for r in state.get("reminders") or []
                            if midnight * 1000 <= r["at"] < (midnight + 86400) * 1000), key=lambda r: r["at"])
        if not reminders:
            self.say("Nothing's on today's calendar: no reminders set for today.")
            return
        later = [r for r in reminders if r["at"] >= now * 1000]
        self.j.show({"id": "calendar", "title": "Today: " + time.strftime("%A %-d %B"),
                     "text": _timeline(reminders) + "\n\n" + "\n".join(
                         "- %s %s  %s" % ("{off}" if r["at"] < now * 1000 else "{check}", _clock(r["at"] / 1000),
                                          r["text"]) for r in reminders)})
        self.say("%d thing%s today%s." % (len(reminders), "" if len(reminders) == 1 else "s",
                                          ", next: %s at %s" % (later[0]["text"], _clock(later[0]["at"] / 1000))
                                          if later else ", all done"))


def _timeline(reminders):
    return "```timeline\n%s\n```" % "\n".join("%s %s" % (time.strftime("%H:%M", time.localtime(r["at"] / 1000)),
                                                          r["text"].replace("\n", " ")) for r in reminders[:12])


def _rings(goals, done):
    """The tracker's daily goals as rings (```ring): green once met, red when an "at most" goal is over."""
    rows = []
    for c, g in goals.items():
        if not g.get("mins") or c not in NAMES:
            continue
        secs, aim = done.get(c, 0), g["mins"] * 60
        state = (" bad" if secs > aim else "") if g.get("kind") == "max" else (" ok" if secs >= aim else "")
        rows.append("%s: %.1f/%.1f h%s" % (NAMES[c].capitalize(), secs / 3600, aim / 3600, state))
    return "```ring\n%s\n```" % "\n".join(rows[:8]) if rows else ""


def _stack(label, spent):
    return "```stack\n%s: %s | h\n```" % (label, ", ".join("%s %.1f" % (NAMES[c], v / 3600) for c, v in spent))


def _clock(ts):
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0").replace(":00", "")


def _goals(goals, done, days_left=None):
    """Card lines for the tracker's goals: "- Study: 1 hour of 2 hours", "- Fun: 20 minutes, at most 3 hours"."""
    out = []
    for c, g in goals.items():
        if not g.get("mins") or c not in NAMES:
            continue
        secs, aim = done.get(c, 0), g["mins"] * 60
        have = spoken(secs) if secs >= 60 else "0"
        if g.get("kind") == "max":
            line = "%s, at most %s" % (have, spoken(aim)) + (" (over)" if secs > aim else "")
        elif secs >= aim:
            line = "%s of %s ✓" % (have, spoken(aim))
        else:
            line = "%s of %s" % (have, spoken(aim))
            if days_left:
                line += " (%s a day to make it)" % spoken((aim - secs) / (days_left + 1))
        out.append("- %s: %s" % (NAMES[c].capitalize(), line))
    return out


def _projects(entries, start, end):
    """[(name, seconds)] the Day Tracker saw most between start and end: VS Code projects and apps, top 3."""
    seen = {}
    for e in entries:
        if not start * 1000 <= e.get("start", 0) < end * 1000:
            continue
        for label, ms in (e.get("parts") or {e.get("label") or "": e.get("end", 0) - e.get("start", 0)}).items():
            if label:
                name = label[len(APP):] if label.startswith(APP) else label
                seen[name] = seen.get(name, 0) + ms / 1000
    return [(n, s) for n, s in sorted(seen.items(), key=lambda kv: -kv[1]) if s >= 600][:3]
