"""Coding on your projects: git status, open in VS Code, run the tests, "ask Claude to ... in X project"."""
import os
import shlex
import subprocess

from skills.base import Skill
from skills.helpers import PROJECTS


class Dev(Skill):
    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.projects = jarvis.projects

    def patterns(self):
        return [
            (r"git status(?: (?:of|for|in))?(?: (?:the )?(.+?)(?: project)?)?|(?:any )?changes in (?:the )?(.+?)(?: project)?|"
             r"what changed in (?:the )?(.+?)(?: project)?", self.git_status),
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
