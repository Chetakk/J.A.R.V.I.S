"""Coding on your projects: git status, open in VS Code, run the tests, "ask Claude to ... in X project",
"open the project I was working on yesterday"."""
import json
import os
import shlex
import subprocess
import urllib.request

from skills.base import Skill
from skills.daytracker import HUB
from skills.files import WHEN, _since
from skills.helpers import PROJECTS

# "open / reopen / bring up the project I worked on (yesterday, last time)"
_BACK_TO = r"open|continue|resume|reopen|go back to|back to|pick up|get back to|bring up|load"
_WORKED = (r" (?:that |which )?i (?:(?:was|were|have been|'?ve been|had been) (?:working on|on|doing|coding(?: on)?)|"
           r"worked on)")
_AFTER = r"(?: (?:last time|before|earlier))?(?: in vs ?code)?"


class Dev(Skill):
    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.projects = jarvis.projects

    def patterns(self):
        return [
            (r"git status(?: (?:of|for|in))?(?: (?:the )?(.+?)(?: project)?)?|(?:any )?changes in (?:the )?(.+?)(?: project)?|"
             r"what changed in (?:the )?(.+?)(?: project)?", self.git_status),
            (r"(?:%s)(?: with| on)? (?:the |my |that )?(?:(?:last|latest|previous|most recent|recent) )?project(?:%s)?"
             r"(?: (%s))?%s|(?:%s) (?:the |my |that )?(?:thing|stuff|work|code)%s(?: (%s))?%s|"
             r"(?:open|continue|resume|reopen|load) (yesterday|today|last night|this morning)'?s project"
             % (_BACK_TO, _WORKED, WHEN, _AFTER, _BACK_TO, _WORKED, WHEN, _AFTER), self.last_project),
            (r"open (?:the )?(.+?) project(?: in vs ?code)?|open (?:the )?(.+?) in vs ?code", self.open_project),
            (r"run (?:the )?tests(?: (?:in|for|of) (?:the )?(.+?)(?: project)?)?", self.run_tests),
            (r"(?:ask|tell|have) claude (?:to )?(.+?) in (?:the )?(.+?) project", self.claude_project),
        ]

    def _project_path(self, name):
        p = self.projects.find(name)
        return p.path if p else None

    def git_status(self, m):
        name = next((g for g in m.groups() if g), None)
        path = self._project_path(name) if name else os.path.join(PROJECTS, "J.A.R.V.I.S")
        if not path:
            return False if name else None
        if not os.path.isdir(os.path.join(path, ".git")):
            self.say("%s isn't a git project." % os.path.basename(path))
            return
        res = subprocess.run(["git", "-C", path, "status", "--porcelain=v1", "-b"], capture_output=True, text=True,
                             timeout=10)
        lines = res.stdout.splitlines()
        branch = lines[0][3:].split("...")[0] if lines else "?"
        changed = [l for l in lines[1:] if not l.startswith("??")]
        new = [l for l in lines[1:] if l.startswith("??")]
        self.j.chat("sys", "git status %s\n%s" % (os.path.basename(path), res.stdout[-2000:]))
        self.j.show({"title": "git status: %s" % os.path.basename(path), "text": "```\n%s\n```" % res.stdout[-3000:]})
        if not changed and not new:
            self.say("%s is clean, on branch %s." % (os.path.basename(path), branch))
        else:
            self.say("%s on branch %s: %d changed file%s and %d new." % (
                os.path.basename(path), branch, len(changed), "" if len(changed) == 1 else "s", len(new)))

    def open_project(self, m):
        name = m.group(1) or m.group(2)
        path = self._project_path(name)
        if not path:
            return False
        self.j.executor.run("code %s" % shlex.quote(path), reply="Opening %s." % os.path.basename(path), window=True)

    def last_project(self, m):
        when = next((g for g in m.groups() if g), None)
        p, minutes = self._worked_on(when)
        if not p:
            self.say("I don't see a project you worked on %s." % when if when else "I don't know your projects yet.")
            return
        self.projects.current = p
        how = " You spent about %d minutes on it %s." % (minutes, when) if minutes >= 2 and when else ""
        self.j.executor.run("code %s" % shlex.quote(p.path), reply="Opening %s.%s" % (p.spoken, how), window=True)

    def _worked_on(self, when):
        """(project, minutes): the one with the most VS Code time then, from the Day Tracker; with no time
        given, the one you were in last. Without the tracker, the one whose files changed last."""
        start, end = _since(when) if when else (0, float("inf"))
        totals, last = {}, (0, None)
        try:
            with urllib.request.urlopen(HUB + "/api/state", timeout=2) as r:
                entries = json.load(r).get("autoEntries") or []
        except (OSError, ValueError):
            entries = []
        for e in entries:
            if not (start <= e.get("start", 0) / 1000 <= end):
                continue
            parts = e.get("parts") or {e.get("label") or "": e.get("end", 0) - e.get("start", 0)}
            for label, ms in parts.items():
                if not str(label).startswith("VS Code \u00b7 "):
                    continue
                p = self.projects.get(label[len("VS Code \u00b7 "):]) or self.projects.find(
                    label[len("VS Code \u00b7 "):], strict=True)
                if not p:
                    continue  # "temp", a folder that isn't one of your projects
                totals[p.name] = totals.get(p.name, 0) + ms
                if e["start"] >= last[0]:
                    last = (e["start"], p)
        if totals:
            if not when:
                return last[1], 0
            name = max(totals, key=totals.get)
            return self.projects.get(name), totals[name] / 60000
        recent = self.projects.recent(1)
        return (recent[0], 0) if recent and (not when or not entries) else (None, 0)

    def run_tests(self, m):
        path = self._project_path(m.group(1)) if m.group(1) else None
        if m.group(1) and not path:
            return False
        path = path or os.path.join(PROJECTS, "J.A.R.V.I.S")
        self.j.claude.open_terminal(cwd=path, prompt="Run this project's tests and tell me what passed and failed.")
        self.say("I've asked Claude to run the tests for %s. It's in the terminal." % os.path.basename(path))

    def claude_project(self, m):
        path = self._project_path(m.group(2))
        if not path:
            return False
        self.j.claude.open_terminal(cwd=path, prompt=m.group(1))
        self.say("Okay, Claude's on it in %s." % os.path.basename(path))
