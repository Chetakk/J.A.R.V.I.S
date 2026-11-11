"""The dashboard: one card with what JARVIS is doing for you and what you're on, kept up to date while it's open.
It comes up by itself when JARVIS starts; "show my dashboard" (or "let me see the dashboard", "go to my dashboard"),
"what are you working on", "show running tasks", "close the dashboard".

A rail on the left: the live orb with a greeting and tiles (running, notifications, alerts, next reminder), the modes
that are on, the last action and a phrase to try. Then two columns: the computer as rings, the question waiting for
your answer (Yes / No) and your projects (the one you touched last and any open in VS Code or running under biztel
run, each with run / stop, VS Code and browser icons and its links); today's timeline, unread mail (not GitHub's) and
what else is running (terminal commands, Claude tasks, JARVIS's browser, timers). A task under way keeps its own card.
Everything is read from what JARVIS already keeps; nothing is stored for it. The slow parts (when each project was
touched, Gmail, the calendar) are fetched at most once a SLOW, Gmail and the calendar in the background."""
import os
import threading
import time

from claude.bridge import TASK_ROLE, TERMINAL_ROLE
from core import tasks, verify
from skills.base import Skill
from skills.helpers import and_list
from system import desktop
from system import google_account as google

CARD_ID = "dashboard"
EVERY = 2  # seconds between updates
SLOW = 60  # seconds between looking at the projects, mail and the calendar
FOR = 30 * 60  # it stops updating itself after this long
TASK_MINUTES = 30  # a finished task still counts in the spoken line this long
PROJECTS = 4  # projects on the card at most
MAILS = 3
TIP_EVERY = 30  # seconds each "Try saying" phrase stays
MOODS = {"hot": "running hot", "straining": "working hard", "busy": "busy", "squeezed": "low on memory",
         "sleepy": "sleepy"}


class Dashboard(Skill):
    group = "overview"
    does = "A live card of what JARVIS is doing for you and what you're working on"
    phrases = ("show my dashboard (a live card: your projects, what's running, the computer, today, mail)",
               "what are you working on")
    gives = "a card kept up to date"
    permission = "auto"
    checks = "not checked: it only shows"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self._local = {"at": 0.0}  # projects: the one touched last, when each was
        self._remote = {"at": 0.0, "mail": [], "today": []}  # Gmail and the calendar, fetched off the card's thread
        self._fetching = threading.Lock()

    def patterns(self):
        return [
            (r"(?:show |open |bring up |pull up )?(?:me )?(?:my |the |your )?(?:dashboard|task ?board|tasks? card|"
             r"activity board)|what are you (?:working on|busy with)(?: (?:right )?now| right| for me)?|"
             r"what(?:'s| is| are) (?:still )?(?:in progress|running in the background)|"
             r"(?:show|list) (?:me )?(?:the |my |your )?(?:running|active|background) (?:tasks|jobs)|"
             r"what tasks are running|(?:are there )?any (?:tasks|jobs) running|"
             r"(?:let me |can i |i (?:want|wanna|would like|'d like) to )?(?:see|look at|check) (?:my |the |your )?"
             r"dashboard|(?:go|take me|switch) (?:back )?to (?:my |the )?dashboard", lambda m: self.show()),
            (r"(?:close|hide) (?:the |my )?(?:dashboard|task ?board)", lambda m: self.close()),
        ]

    def show(self, speak=True):
        parts, card = self.build()
        self.j.show(card)
        if speak:
            self.say(_spoken(parts))
        if self.j.ui:
            threading.Thread(target=self._keep_fresh, args=(card,), daemon=True).start()

    def close(self):
        ui = self.j.ui
        if not (ui and ui.card_open(CARD_ID)):
            return False
        ui.close_card(CARD_ID)

    def _keep_fresh(self, shown):
        """Update the card while it's on screen; stop once it's closed or another card replaces it."""
        until = time.time() + FOR
        while time.time() < until:
            time.sleep(EVERY)
            ui = self.j.ui
            if not (ui and ui.card_open(CARD_ID)):
                return
            _, card = self.build()
            if card != shown:
                shown = card
                ui.show_card(card)

    def build(self):
        """(counts for the spoken line, the card): a rail of the orb, tiles and status lines, then two columns: the
        computer, a question waiting and your projects; today, unread mail and what's running."""
        j, now = self.j, time.time()
        rail, mine, day, choices, parts = [], [], [], [], {}
        extras, timers, body = _from_ui(j)
        self._fetch(now)

        task = j.skills.tasks.task or (tasks.past() or [None])[-1]  # not drawn (it has its own card), only said
        if task and (task["state"] in tasks.OPEN_STATES or now - task["at"] < TASK_MINUTES * 60):
            parts["task"] = task["state"]

        if body:
            mine += ["**Computer**", "```ring\nCPU: %d%%%s\nGPU: %d%%\nMemory: %d%%%s\nTemp: %d / 100 °C%s\n```" % (
                _five(body.cpu), " warn" if body.cpu >= 85 else "", _five(body.gpu), _five(100 * (1 - body.free)),
                " warn" if body.free < 0.10 else "", _five(body.hottest), " warn" if body.hottest >= 75 else ""), ""]
        if j.asking:
            parts["question"] = 1
            mine += ["**Waiting for your answer**", "> %s" % j.asking, ""]
            choices += [{"label": "Yes", "do": "yes"}, {"label": "No", "do": "no"}]
        if self._remote["mail"]:
            from skills.gmail import _rows
            mine += ["**Unread mail**", _rows(self._remote["mail"]), ""]

        day += ["**Today**"] + (self._remote["today"] or ["{ok} Nothing on today's calendar"]) + [""]
        up = j.projects.running()
        parts["projects"] = len(up)
        shown = self._projects(up)
        day += ["**Projects**"] + [line for p in shown for line in _project(
            p, up.get(p.name), self._local["touched"].get(p.path), now)] + [""] if shown else []
        running = []  # ```rows lines: glyph | what | detail | how long
        watched = {id(w) for w in getattr(getattr(j.skills, "proactive", None), "waiting", None) or []}
        jobs = _jobs()
        for job in jobs:
            where = os.path.basename(job.cwd or "") or "a terminal"
            running.append("$ | `%s` | in %s%s | %s" % (_cell(job.command), _cell(where),
                                                        " · I'll tell you when it's done" if id(job) in watched else "",
                                                        _took(now - job.started)))
        parts["jobs"] = len(jobs)
        claude_tasks = desktop.windows_with_role(TASK_ROLE)
        running += ["✻ | Claude task | %s |" % _cell(w.title or "terminal") for w in claude_tasks]
        parts["claude"] = len(claude_tasks)
        if desktop.window_with_role(TERMINAL_ROLE):
            running.append("✻ | Claude terminal | open |")
        web = getattr(j.skills, "web", None)
        if web and web.agent.running():
            running.append("◎ | My browser | the agent is using it |")
            parts["agent"] = 1
        if j.cards.timers:
            ends = sorted(timers.values(), key=lambda t: t[1])
            running += ["⏱ | %s | | %s left" % (_cell(what.capitalize() + " timer" if what else "Timer"),
                                                 _took(max(60, end - now), up=True)) for what, end, _ in ends]
            if not ends:  # the UI didn't say when they end
                running.append("⏱ | %d timer%s | |" % (len(j.cards.timers), "" if len(j.cards.timers) == 1 else "s"))
            parts["timers"] = len(j.cards.timers)
        mine += ["**Running**"] + (["```rows\n%s\n```" % "\n".join(running)] if running
                                   else ["{ok} Nothing else running right now"])

        stats = ["%d | running" % (len(up) + len(running)), "%d | notifications" % (extras.get("unread") or 0),
                 "%d | alerts" % (extras.get("alerts") or 0)]
        soon = extras.get("next") or extras.get("weather") or ""
        if soon:  # "9:30 standup" / "27° · clear": the value, then what it is
            value, _, label = soon.partition(" ")
            stats.append("%s | %s" % (value, label.strip(" ·") or "next"))
        rail += ["```hero", _greeting(now), MOODS.get(getattr(body, "mood", None), "all calm")] + stats + ["```", ""]
        focus = j.skills.focus_mode
        modes = (["{check} focus, %d min left" % max(1, round((focus.focus_until - now) / 60))] if focus.focusing()
                 else []) + (["{check} quiet"] if j.speaker.muted else []) + (["{check} private"] if j.speaker.sink else [])
        if modes:
            rail.append("**On** " + "  ·  ".join(modes))
        last = verify.last
        if last and now - last.at < 3600:
            mark = {True: "{ok}", False: "{bad}"}.get(last.ok, "{off}")
            rail.append("**Last action** %s %s: %s · %s" % (mark, last.action, last.what, _ago(now - last.at)))
        tips = extras.get("tips") or []
        if tips:  # a new one every TIP_EVERY seconds
            rail.append("**Try saying** “%s”" % tips[int(now // TIP_EVERY) % len(tips)])

        return parts, {"id": CARD_ID, "title": "Dashboard", "place": "top", "rail": "\n".join(rail).strip(),
                       "columns": ["\n".join(c).strip() or "{ok} Nothing to show" for c in (mine, day)],
                       "choices": choices}

    def _projects(self, up):
        """The projects worth showing: the one touched last, then ones running or open in VS Code; newest first."""
        reg, local = self.j.projects, self._local
        titles = [w.title for w in desktop.windows() if w.title.endswith("Visual Studio Code")]
        found = [p for p in reg.items if p.name in up or any(p.name in t.split(" - ") for t in titles)]
        last = local.get("last")
        if last and last not in found:
            found.append(last)
        return sorted(found, key=lambda p: -local["touched"].get(p.path, 0))[:PROJECTS]

    def _fetch(self, now):
        """The slow parts, at most once a SLOW: projects here (local and quick), Gmail and the calendar on a thread."""
        if now - self._local["at"] >= SLOW:
            reg = self.j.projects
            touched = {p.path: p.touched() for p in reg.items}
            last = max(reg.items, key=lambda p: touched[p.path], default=None)
            self._local = {"at": now, "last": last, "touched": touched}
        if now - self._remote["at"] < SLOW:
            return
        if not google.connected():
            self._remote = {"at": now, "mail": [], "today": self._today(())}  # the Day Tracker's reminders alone
        elif self._fetching.acquire(blocking=False):
            self._remote["at"] = now
            threading.Thread(target=self._fetch_remote, daemon=True).start()

    def _fetch_remote(self):
        try:
            s = self.j.skills
            mail, events = [], []
            try:
                from skills.gmail import INBOX
                gm = s.gmail.api("gmail", "v1")
                got = google.run(gm.users().messages().list(  # not GitHub's notifications: pushes, CI, reviews
                    userId="me", q=INBOX + " is:unread newer_than:7d -from:github.com", maxResults=MAILS))
                mail = [m for m in (s.gmail._meta(gm, x["id"]) for x in got.get("messages") or [])
                        if not m["addr"].endswith("github.com")]
            except Exception:  # offline, signed out or a Google error: no mail rows this time
                pass
            try:
                start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
                events = s.calendar.meetings(start, start + 86400)
            except Exception:
                pass
            self._remote = {"at": self._remote["at"], "mail": mail, "today": self._today(events)}
        finally:
            self._fetching.release()

    def _today(self, events):
        """Today's meetings and the Day Tracker's reminders as a ```timeline, or [] when the day is empty."""
        cal = getattr(self.j.skills, "calendar", None)
        if not cal:
            return []
        from skills.google_calendar import _timeline
        start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        return _timeline(events, (), cal._reminders(start, start + 86400))


def _project(p, run, touched, now):
    """A project's lines: a ```rows line (name, what it is, ▶ / ■, editor and browser buttons, when), then its links
    while biztel run has it up."""
    name = p.spoken
    acts = ["media-playback-stop-symbolic Stop %s -> stop %s" % (name, p.name) if run else
            "media-playback-start-symbolic Run %s -> run %s" % (name, name),
            "text-editor-symbolic Open in VS Code -> open the %s project in vs code" % name]
    links = (run or {}).get("links") or []
    if links:
        acts.append("web-browser-symbolic Open in the browser -> open %s in the browser" % name)
    head = "```rows\n | %s%s | %s | %s | %s\n```" % (  # no icon cell: the name's initials
        _cell(name), " {check}" if run else "", _cell(p.summary or p.path), _ago(now - touched) if touched else "",
        "; ".join(acts))
    shown = " · ".join("[%s](%s)" % (l["url"].split("//")[-1], l["url"]) for l in links)
    return [head] + (["> ▶ running: " + shown] if shown else ["> ▶ running"] if run else [])


def _greeting(now):
    hour = time.localtime(now).tm_hour
    return ("Late night" if hour < 5 else "Good morning" if hour < 12 else "Good afternoon" if hour < 17
            else "Good evening")


def _from_ui(j):
    """(extras, timers, body) the UI keeps fresh anyway, or ({}, {}, None) without a UI (or a fake one)."""
    ui = j.ui
    extras, timers = getattr(ui, "extras", None), getattr(ui, "timers", None)
    body = getattr(getattr(ui, "bar", None), "body", None)
    return (extras if isinstance(extras, dict) else {}, dict(timers) if isinstance(timers, dict) else {},
            body if hasattr(body, "hottest") and getattr(body, "_cpu", None) else None)


def _cell(s):
    """Text for a ```rows cell: a "|" would start the next cell."""
    return str(s).replace("|", "/").replace("\n", " ")


def _five(v):
    """To the nearest 5, so small wobbles don't redraw the card every 2 seconds."""
    return int(5 * round(max(0, min(100, v)) / 5))


def _jobs():
    """Commands busy in your terminals (system/jobs.py), or [] without it."""
    try:
        from system import jobs
        return jobs.running()
    except Exception:
        return []


def _spoken(parts):
    things = []
    if parts.get("projects"):
        things.append("%d project%s" % (parts["projects"], "" if parts["projects"] == 1 else "s"))
    if parts.get("jobs"):
        things.append("%d command%s in your terminals" % (parts["jobs"], "" if parts["jobs"] == 1 else "s"))
    if parts.get("claude"):
        things.append("%d Claude task%s" % (parts["claude"], "" if parts["claude"] == 1 else "s"))
    if parts.get("agent"):
        things.append("my browser")
    if parts.get("timers"):
        things.append("%d timer%s" % (parts["timers"], "" if parts["timers"] == 1 else "s"))
    line = ("Running: %s." % and_list(things)) if things else "Nothing's running right now."
    if parts.get("task") == "running":
        line += " A task is under way."
    elif parts.get("task") == "paused":
        line += " A task is paused."
    if parts.get("question"):
        line += " One question is waiting for you."
    return line + " It's on the dashboard."


def _took(secs, up=False):
    """"40 s", "12 min", "1 h 5 min"; up: minutes rounded up (a timer's time left)."""
    secs = int(secs + 59) // 60 * 60 if up and secs >= 60 else int(secs)
    if secs < 60:
        return "%d s" % secs
    if secs < 3600:
        return "%d min" % (secs // 60)
    return "%d h %d min" % (secs // 3600, secs % 3600 // 60)


def _ago(secs):
    return "just now" if secs < 60 else "%s ago" % _took(secs)
