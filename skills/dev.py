"""Coding on your projects: git status, open in VS Code, run the tests, "ask Claude to ... in X project",
"open the project I was working on yesterday"; and any folder or file in any app ("open the projects folder in
vs code", "open resume.pdf in writer", "open a terminal in downloads", "open jarvis in claude",
"open the first folder in vs code" after JARVIS has listed a folder)."""
import json
import os
import shlex
import subprocess
import re
import urllib.request

from core import verify
from skills.base import Skill
from skills.daytracker import HUB
from skills.files import THIS, WHEN, PLACES, _since, place, spoken_place
from skills.helpers import PROJECTS
from system import desktop

# "open / reopen / bring up the project I worked on (yesterday, last time)"
_BACK_TO = r"open|continue|resume|reopen|go back to|back to|pick up|get back to|bring up|load"
_WORKED = (r" (?:that |which )?i (?:(?:was|were|have been|'?ve been|had been) (?:working on|on|doing|coding(?: on)?)|"
           r"worked on)")
_AFTER = r"(?: (?:last time|before|earlier))?(?: in vs ?code)?"
# "open <folder or file> in <app>" / "open <app> in <folder>"
_IN = re.compile(r" (?:in|with|using|inside|at|from) (?:a |an |the |my )?", re.IGNORECASE)
_CLAUDE = re.compile(r"claude(?: code)?", re.IGNORECASE)
_TERMINAL = re.compile(r"(?:gnome )?terminal|shell|console|command line", re.IGNORECASE)
_BROWSER = re.compile(r"(?:default |web )?browser", re.IGNORECASE)
_DEFAULT = re.compile(r"default(?: application| program| viewer| editor)?|usual (?:one|program)", re.IGNORECASE)
_PAGE = re.compile(r"\.(?:pdf|html?|svg|png|jpe?g|gif|webp|txt|json|md|xml|mp3|mp4)$", re.IGNORECASE)  # a browser shows


class Dev(Skill):
    group = "coding"
    does = "git status, projects in VS Code, the project you worked on, tests and jobs via Claude Code"
    phrases = ("git status of <project>", "open <project> project", "open the project I was working on yesterday",
               "open <folder, file or project> in <app>", "open <folder, file or project> in the default app",
               "run the tests in <project>",
               "ask claude to <task> in <project> project")
    gives = "speech, VS Code or a terminal opened"
    permission = "auto"
    checks = "the launch is watched for a window or an error"

    group = "coding"
    about = "git status, projects in VS Code, the project you worked on, tests and jobs via Claude Code"
    phrases = ("git status of <project>", "open <project> project", "open the project I was working on yesterday",
               "open <folder, file or project> in <app>", "run the tests in <project>",
               "ask claude to <task> in <project> project")
    output = "speech, VS Code or a terminal opened"
    permission = "auto"
    verify = "the launch is watched for a window or an error"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.projects = jarvis.projects

    def patterns(self):
        return [
            (r"(?:git )?(push|pull|fetch)(?: (?:this|it|the code|my changes|the project|this project))?(?: to github)?",
             self.git_sync),
            (r"(?:which|what) project (?:is this|am i (?:in|on))|what am i (?:working on|looking at)",
             lambda m: self.which_project()),
            (r"git status(?: (?:of|for|in))?(?: (?:the )?(.+?)(?: project)?)?|(?:any )?changes in (?:the )?(.+?)(?: project)?|"
             r"what changed in (?:the )?(.+?)(?: project)?", self.git_status),
            (r"(?:%s)(?: with| on)? (?:the |my |that )?(?:(?:last|latest|previous|most recent|recent) )?project(?:%s)?"
             r"(?: (%s))?%s|(?:%s) (?:the |my |that )?(?:thing|stuff|work|code)%s(?: (%s))?%s|"
             r"(?:open|continue|resume|reopen|load) (yesterday|today|last night|this morning)'?s project"
             % (_BACK_TO, _WORKED, WHEN, _AFTER, _BACK_TO, _WORKED, WHEN, _AFTER), self.last_project),
            (r"open (?:the )?(.+?) project(?: in vs ?code)?|open (?:the )?(.+?) in vs ?code", self.open_project),
            (r"open (?:up )?(.+?%s.+?)(?: app)?" % _IN.pattern, self.open_in),
            (r"run (?:the )?tests(?: (?:in|for|of) (?:the )?(.+?)(?: project)?)?", self.run_tests),
            (r"(?:ask|tell|have) claude (?:to )?(.+?) in (?:the )?(.+?) project", self.claude_project),
        ]

    def _project_path(self, name):
        p = self.projects.find(name)
        return p.path if p else None

    def git_status(self, m):
        name = next((g for g in m.groups() if g), None)
        focused = None if name else self.projects.focused()
        path = self._project_path(name) if name else (focused.path if focused else os.path.join(PROJECTS, "J.A.R.V.I.S"))
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

    def git_sync(self, m):
        """push / pull / fetch in the project of the window you're looking at (git asks you first)."""
        p = self.projects.focused()
        if not (p and os.path.isdir(os.path.join(p.path, ".git"))):
            self.say("I can't tell which project you mean. Click its VS Code window, or say its name.")
            return
        verb = m.group(1).lower()
        self.j.executor.run("git -C %s %s" % (shlex.quote(p.path), verb), output=True, timeout=60,
                            reply="%s %s." % (verb.capitalize(), p.spoken),
                            question="%s %s?" % (verb.capitalize(), p.spoken))

    def which_project(self):
        win = desktop.active_window()
        p = self.projects.focused()
        if p:
            self.say("You're in %s." % p.spoken)
        elif win and win.title:
            self.say("You're in %s, and I don't see a project in it." % win.title.split(" \u2014 ")[-1])
        else:
            self.say("I can't tell which window you're in.")

    def open_project(self, m):
        name = m.group(1) or m.group(2)
        path = self._project_path(name)
        if not path:
            return False
        self.j.executor.run("code %s" % shlex.quote(path), reply="Opening %s." % os.path.basename(path), window=True)

    def open_in(self, m):
        plan = open_in_plan(self.j, m.group(1))
        if not plan:
            return False  # "open youtube in chrome" is the browser's, "open it in a new window" isn't an app
        path, kind, app = plan
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if kind == "claude":
            self.j.claude.open_terminal(cwd=folder)
            self.say("Opening Claude in %s." % spoken_place(folder))
        elif kind == "terminal":
            self.j.executor.run("gnome-terminal --working-directory=%s" % shlex.quote(folder),
                                reply="Opening a terminal in %s." % spoken_place(folder), window=True)
        elif kind == "default":
            self.j.executor.run("xdg-open %s" % shlex.quote(path), reply="Opening %s." % spoken_place(path),
                                window=True)
        elif not self.j.apps.opens_files(app):
            self.say("%s can't open files, so I can't open %s in it." % (app["name"], spoken_place(path)))
        else:
            desktop.go_to_jarvis_workspace()
            before = verify.windows_now()
            if not self.j.apps.launch(app, [path]):
                verify.record("open %s in %s" % (path, app["name"]), False, "it didn't start")
                self.say("Hmm, %s didn't start." % app["name"])
                return
            verify.launched("open %s in %s" % (path, app["name"]), None, before, True, self.j.executor.notify)
            self.j.last_app = app["process"] or None
            self.say("Opening %s in %s." % (spoken_place(path), app["name"]))

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


def open_in_plan(j, said):
    """(path, kind, app) for "<folder or file> in <app>" or "<app> in <folder>": kind is "claude", "terminal" or
    "app" (app: its menu entry). None when one side isn't a folder or file here, or the other isn't an app."""
    said = re.sub(r"^(?:a|an|the|my) ", "", said.strip(), flags=re.IGNORECASE)
    for cut in reversed(list(_IN.finditer(said))):
        left, right = said[:cut.start()], said[cut.end():]
        for what, where in ((left, right), (right, left)):
            kind, app = _app(j, where)
            if not kind:
                continue
            if what.lower() in j.registry.sites:
                return None  # "open youtube in chrome": the website
            if app and app["process"] in ("chrome", "firefox") and not _local(what):
                return None  # "open google.com in firefox"
            path = place(j, what)
            if path:
                return path, kind, app
    return None


def _app(j, said):
    """(kind, menu entry) for the app `said` names: ("claude", None), ("terminal", None), ("default", None),
    ("app", entry); "the browser" is your default one, "the default app" whatever opens that kind of file.
    (None, None) when it isn't an app."""
    said = re.sub(r"^(?:a|an|the|my) |(?: app| window)$", "", said.strip(), flags=re.IGNORECASE)
    if _CLAUDE.fullmatch(said):
        return "claude", None
    if _TERMINAL.fullmatch(said):
        return "terminal", None
    if _BROWSER.fullmatch(said):
        from gi.repository import Gio
        info = Gio.AppInfo.get_default_for_uri_scheme("https")
        path = info and info.get_filename() if isinstance(info, Gio.DesktopAppInfo) else None
        app = next((a for a in j.apps.apps if a["path"] == path), None)
        return ("app", app) if app else (None, None)
    if _DEFAULT.fullmatch(said):
        return "default", None
    app = j.apps.find(said)
    if not app:
        return None, None
    return ("terminal", None) if app["process"].startswith("gnome-terminal") else ("app", app)


def _local(said):
    """Sounds like something on this computer, not a website: a folder, a path, "it", "report.pdf"."""
    said = re.sub(r"^(?:the|my) ", "", said.strip(), flags=re.IGNORECASE)
    return bool(re.search(r" (?:folder|directory)$", said) or said.lower() in PLACES or said.startswith(("~", "/"))
                or re.fullmatch(THIS, said, re.IGNORECASE) or _PAGE.search(said))
