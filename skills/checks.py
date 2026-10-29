""""Did that work?": what JARVIS saw after its last action (a window opening, an error, the screen changing),
said honestly; when it couldn't tell, it offers to look at the screen (a screenshot Claude checks)."""
import os
import time

from claude.bridge import WORKSPACE
from core import verify
from skills.base import Skill
from skills.screen import screenshot

RECENT = 5 * 60  # older than this, "that" isn't the last thing JARVIS did any more


class Checks(Skill):
    def patterns(self):
        return [
            (r"did (?:that|it|this) (?:work|open|go through|happen|run|start|load|succeed)(?: properly| okay| ok)?|"
             r"(?:has|is) (?:that|it) (?:worked|opened|done|open|running)(?: yet)?|was (?:that|it) successful|"
             r"(?:check|see) (?:if|whether) (?:that|it) worked", self.did_it_work),
        ]

    def did_it_work(self, m):
        r = verify.last
        if not r or time.time() - r.at > RECENT:
            self.say("I haven't done anything lately that I could check.")
            return
        if r.ok:
            self.say("Yes, %s." % r.what)
            return
        if r.ok is False:
            self.say("No, %s." % r.what)
            return
        if not self.j.ask("I couldn't tell: %s. Want me to look at the screen?" % r.what):
            self.say(["Okay.", "Alright, I'll leave it."])
            return
        path = os.path.join(WORKSPACE, "screen.png")
        screenshot(path)
        self.j._ask_claude("I just tried to do this: %s. From the screenshot, did it work? Say yes or no and what "
                           "you see that shows it, in one sentence." % r.action, image=path)
