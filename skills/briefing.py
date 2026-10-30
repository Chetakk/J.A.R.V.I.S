"""The daily briefing: "brief me", "what's my day look like", "what do I have today", "morning briefing".

One card and a short spoken line, from what the Day Tracker already keeps: today's reminders, open to-dos,
yesterday's time (and the projects it went on), today's goals and the week's. It also comes by itself once a
day, the first time you're around in the morning (settings briefing; "turn off the daily briefing").
A calendar joins it once one is connected."""
import time

from skills.base import Skill
from skills.daytracker import NAMES, DayTracker, HubDown, spoken, totals
from skills.helpers import and_list

SHOW = 8  # to-dos on the card
APP = "VS Code · "  # Day Tracker labels: "VS Code · J.A.R.V.I.S"
WHY = ("It's your daily briefing: the first time you were around this morning. Say turn off the daily briefing "
       "to stop it.")


class Briefing(Skill):
    group = "briefing"
    does = "The day's briefing from the Day Tracker: reminders, to-dos, yesterday, goals"
    phrases = ("brief me", "what's my day look like", "turn off the daily briefing")
    gives = "a card and a short spoken line"
    permission = "auto"
    checks = "says when the Day Tracker isn't running rather than an empty day"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.hub = DayTracker(jarvis)

    def patterns(self):
        return [
            (r"(?:turn|switch) (?:on|off) (?:the |my )?(?:daily |morning |automatic )?briefings?|"
             r"(?:turn|switch) (?:the |my )?(?:daily |morning |automatic )?briefings? (?:on|off)|"
             r"(?:stop|start) (?:the |my )?(?:daily |morning )?briefings(?: every (?:day|morning))?|"
             r"(?:stop|start) (?:giving me|doing) (?:the |a |my )?(?:daily |morning )?briefings?"
             r"(?: every (?:day|morning))?|"
             r"(?:don'?t|no more|never) (?:brief me|(?:give me )?(?:a |the )?(?:daily |morning )?briefings?)"
             r"(?: every (?:day|morning)| in the morning)?", self.set_auto),
            (r"(?:give me |read me |do |start |what'?s )?(?:my |the |a |today'?s )?(?:daily |morning |day'?s )?"
             r"(?:briefing|rundown)(?: for today| for the day)?|brief me(?: on (?:my |the )?day| for (?:the|to)day)?|"
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
        todos = sorted((t for t in state.get("todos") or [] if not t.get("done")), key=lambda t: -t.get("created", 0))
        yday = totals(state, now=midnight - 1)
        today = totals(state, now=now)
        monday = midnight - time.localtime(now).tm_wday * 86400  # the tracker's weeks start on Monday
        week = totals(state, now=now, since=monday)
        worked_on = _projects(state.get("autoEntries") or [], midnight - 86400, midnight)

        lines, said = [], ["It's %s." % date]
        if reminders:
            lines += ["**Reminders today**"] + ["- %s  %s" % (_clock(r["at"] / 1000), r["text"]) for r in reminders]
            first = reminders[0]
            said.append("%s: %s at %s." % ("One reminder" if len(reminders) == 1 else "%d reminders, first" % len(
                reminders), first["text"], _clock(first["at"] / 1000)))
        if todos:
            lines += ["", "**To-dos (%d open)**" % len(todos)] + ["- " + t["text"] for t in todos[:SHOW]]
            if len(todos) > SHOW:
                lines.append("- and %d more" % (len(todos) - SHOW))
            said.append("%s, the newest is %s." % ("One open to-do" if len(todos) == 1 else
                                                    "%d open to-dos" % len(todos), todos[0]["text"]))
        else:
            lines += ["", "**To-dos**", "- None open"]
        goals = _goals(state.get("goals") or {}, today)
        if goals:
            lines += ["", "**Goals today**"] + goals
        weekly = _goals(state.get("weeklyGoals") or {}, week, days_left=6 - time.localtime(now).tm_wday)
        if weekly:
            lines += ["", "**This week**"] + weekly
        spent = [(c, v) for c, v in sorted(yday.items(), key=lambda kv: -kv[1]) if v >= 60 and c in NAMES]
        if spent:
            lines += ["", "**Yesterday**", "- " + ", ".join("%s %s" % (spoken(v), NAMES[c]) for c, v in spent)]
            if worked_on:
                lines.append("- Most time on " + ", ".join("%s (%s)" % (n, spoken(s)) for n, s in worked_on))
            said.append("Yesterday: %s%s." % (and_list("%s %s" % (spoken(v), NAMES[c]) for c, v in spent[:2]),
                                               ", mostly on %s" % worked_on[0][0] if worked_on else ""))
        if not (reminders or todos or spent):
            said.append("No reminders or to-dos, and nothing tracked yesterday.")
        said.append("It's on your screen.")
        return {"title": "Briefing: " + date, "text": "\n".join(lines).strip()}, " ".join(said)


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
