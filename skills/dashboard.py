"""The dashboard: one card with what JARVIS is doing for you, kept up to date while it's open.
"show my dashboard", "what are you working on", "show running tasks", "close the dashboard".

It shows the question waiting for your answer (with Yes / No buttons, which answer it), the task under way with
each step ticked off (skills/tasks.py), what's running (biztel run projects, commands busy in your terminals, Claude task terminals,
JARVIS's own browser, timers), the modes that are on, and your last requests. Everything is read from what
JARVIS already keeps; nothing is stored for it. A task also ticks itself off on its own card as it runs."""
import json
import os
import threading
import time

from claude.bridge import TASK_ROLE, TERMINAL_ROLE
from core import tasks, verify
from skills.base import Skill
from skills.helpers import and_list
from skills.where_was_i import WHERE_FILE
from system import desktop

CARD_ID = "dashboard"
EVERY = 2  # seconds between updates
FOR = 30 * 60  # it stops updating itself after this long
TASK_MINUTES = 30  # a finished task stays on the dashboard this long


class Dashboard(Skill):
    def patterns(self):
        return [
            (r"(?:show |open |bring up |pull up )?(?:me )?(?:my |the |your )?(?:dashboard|task ?board|tasks? card|"
             r"activity board)|what are you (?:working on|busy with)(?: (?:right )?now| right| for me)?|"
             r"what(?:'s| is| are) (?:still )?(?:in progress|running in the background)|"
             r"(?:show|list) (?:me )?(?:the |my |your )?(?:running|active|background) (?:tasks|jobs)|"
             r"what tasks are running|(?:are there )?any (?:tasks|jobs) running", lambda m: self.show()),
            (r"(?:close|hide) (?:the |my )?(?:dashboard|task ?board)", lambda m: self.close()),
        ]

    def show(self):
        parts, text, choices = self.build()
        self.j.show({"id": CARD_ID, "title": "Dashboard", "text": text, "choices": choices})
        self.say(_spoken(parts))
        if self.j.ui:
            threading.Thread(target=self._keep_fresh, args=(text,), daemon=True).start()

    def close(self):
        ui = self.j.ui
        if not (ui and ui.card_visible() and (ui.current_card() or {}).get("id") == CARD_ID):
            return False
        ui.close_card()

    def _keep_fresh(self, shown):
        """Update the card while it's on screen; stop once it's closed or another card replaces it."""
        until = time.time() + FOR
        while time.time() < until:
            time.sleep(EVERY)
            ui = self.j.ui
            if not (ui and ui.card_visible() and (ui.current_card() or {}).get("id") == CARD_ID):
                return
            _, text, choices = self.build()
            if text != shown:
                shown = text
                ui.show_card({"id": CARD_ID, "title": "Dashboard", "text": text, "choices": choices})

    def build(self):
        """(counts for the spoken line, the card's markdown, its choices)."""
        j, now = self.j, time.time()
        lines, choices, parts = [], [], {}

        if j.asking:
            parts["question"] = 1
            lines += ["**Waiting for your answer**", "> %s" % j.asking, ""]
            choices += [{"label": "Yes", "do": "yes"}, {"label": "No", "do": "no"}]

        task = j.skills.tasks.task or (tasks.past() or [None])[-1]
        if task and (task["state"] in tasks.OPEN_STATES or now - task["at"] < TASK_MINUTES * 60):
            parts["task"] = task["state"]
            done, total = tasks.counts(task)
            lines += ["**Task: %s** · %s, %d of %d done" % (task["title"][:60], task["state"], done, total),
                      tasks.text(task), ""]
            if task["state"] in ("running", "paused") and len(choices) < 3:
                choices.append({"label": "Pause the task", "do": "pause the task"} if task["state"] == "running"
                               else {"label": "Continue the task", "do": "continue the task"})

        running = []
        for name, st in j.projects.running().items():
            p = j.projects.get(name)
            urls = " · ".join("[%s](%s)" % (l["url"].split("//")[-1], l["url"]) for l in st.get("links") or [])
            running.append("- **%s** (biztel run)%s" % (p.spoken if p else name, " · " + urls if urls else ""))
            if len(choices) < 4:
                choices.append({"label": "Stop %s" % (p.spoken if p else name), "do": "stop %s" % name})
        parts["projects"] = len(running)
        watched = {id(w) for w in getattr(getattr(j.skills, "proactive", None), "waiting", None) or []}
        jobs = _jobs()
        for job in jobs:
            where = os.path.basename(job.cwd or "") or "a terminal"
            running.append("- `%s` in %s · %s%s" % (job.command, where, _took(now - job.started),
                                                   " · I'll tell you when it's done" if id(job) in watched else ""))
        parts["jobs"] = len(jobs)
        tasks = desktop.windows_with_role(TASK_ROLE)
        running += ["- Claude task: %s" % (w.title or "terminal") for w in tasks]
        parts["claude"] = len(tasks)
        if desktop.window_with_role(TERMINAL_ROLE):
            running.append("- Claude terminal open")
        web = getattr(j.skills, "web", None)
        if web and web.agent.running():
            running.append("- My browser (the agent) is open")
            parts["agent"] = 1
        if j.cards.timers:
            running.append("- %d timer%s" % (len(j.cards.timers), "" if len(j.cards.timers) == 1 else "s"))
            parts["timers"] = len(j.cards.timers)
        lines += ["**Running**"] + (running or ["- nothing"]) + [""]

        modes = []
        focus = j.skills.focus_mode
        if focus.focusing():
            modes.append("focus, %d min left" % max(1, round((focus.focus_until - now) / 60)))
        if j.speaker.muted:
            modes.append("quiet (answers in the chat)")
        if j.speaker.sink:
            modes.append("private (headphones only)")
        if modes:
            lines += ["**On:** %s" % ", ".join(modes), ""]

        last = verify.last
        if last and now - last.at < 3600:
            mark = {True: "✓", False: "✗"}.get(last.ok, "?")
            lines += ["**Last action** %s %s: %s · %s" % (mark, last.action, last.what, _ago(now - last.at)), ""]

        asked = _requests()
        if asked:
            lines += ["**You asked**"] + ["- %s · %s" % (t, _ago(now - w)) for w, t in asked[-4:][::-1]]
        return parts, "\n".join(lines).strip(), choices


def _jobs():
    """Commands busy in your terminals (system/jobs.py), or [] without it."""
    try:
        from system import jobs
        return jobs.running()
    except Exception:
        return []


def _requests():
    try:
        with open(WHERE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
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


def _took(secs):
    secs = int(secs)
    if secs < 60:
        return "%d s" % secs
    if secs < 3600:
        return "%d min" % (secs // 60)
    return "%d h %d min" % (secs // 3600, secs % 3600 // 60)


def _ago(secs):
    return "just now" if secs < 60 else "%s ago" % _took(secs)
