""""Where was I": your last requests, what's still running, JARVIS's open windows."""
import json
import os
import re
import time

from skills.base import Skill
from skills.helpers import HOME
from system import desktop

WHERE_FILE = os.path.join(HOME, ".local/share/jarvis/where.json")


class WhereWasI(Skill):
    def patterns(self):
        return [
            (r"where was i|where did i (?:leave|stop) off|catch me up|what did i leave (?:open|running)|"
             r"(?:continue|carry on|pick up|start again) (?:from )?where i (?:left|stopped)(?: off)?",
             lambda m: self.where_was_i()),
        ]

    def log(self, text):
        """Every request (except asking this) is kept for "where was I"."""
        if re.match(r"^(?:where was i|where did i (?:leave|stop) off|catch me up|(?:continue|carry on|pick up) "
                    r"(?:from )?where i)\b", text, re.IGNORECASE):
            return
        try:
            with open(WHERE_FILE) as f:
                log = json.load(f)
        except (OSError, ValueError):
            log = []
        log = (log + [[time.time(), text]])[-20:]
        try:
            with open(WHERE_FILE, "w") as f:
                json.dump(log, f)
        except OSError:
            pass

    def where_was_i(self):
        try:
            with open(WHERE_FILE) as f:
                log = json.load(f)
        except (OSError, ValueError):
            log = []
        focus = self.j.skills.focus_mode
        parts, card = [], []
        if log:
            when, last = log[-1]
            parts.append("The last thing you asked me, %s, was: %s." % (_ago(time.time() - when), last))
            card += ["**Your last requests**"] + ["- %s — %s" % (t, _ago(time.time() - w)) for w, t in log[-5:][::-1]]
        running = list(self.j.projects.running())
        if running:
            parts.append("Still running: %s." % ", ".join(running))
            card += ["", "**Running**"] + ["- " + r for r in running]
        ws = int((self.j.settings.get("workspace") or {}).get("number", 5))
        open_here = [w.title for w in desktop.windows() if w.workspace == ws and w.title and "jarvis" not in w.wm_class]
        if open_here:
            card += ["", "**Open on workspace %d**" % ws] + ["- " + t for t in open_here]
        if focus.focusing():
            parts.append("You're in focus mode for %d more minutes." % round((focus.focus_until - time.time()) / 60))
        if not parts and not open_here:
            self.say("I don't have anything from before, we're starting fresh.")
            return
        self.j.show({"title": "Where you left off", "text": "\n".join(card)})
        if open_here and self.j.ask(" ".join(parts) + " You've got %d window%s open on my workspace. Take you there?"
                                    % (len(open_here), "" if len(open_here) == 1 else "s")):
            desktop.switch_to(ws)
        elif not open_here:
            self.say(" ".join(parts))


def _ago(secs):
    if secs < 90:
        return "just now"
    if secs < 3600:
        return "%d minutes ago" % (secs // 60)
    if secs < 7200:
        return "about an hour ago"
    if secs < 86400:
        return "about %d hours ago" % (secs // 3600)
    return "%d days ago" % (secs // 86400)
