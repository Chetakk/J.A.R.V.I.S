""""type: see you at five", and "start dictation" ... "stop dictation" (typed as you speak;
"new line" presses Enter)."""
import re

from skills.base import Skill
from skills.helpers import type_into_window
from system import desktop


class Dictation(Skill):
    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.dictating = False

    def patterns(self):
        return [
            (r"(?:start|begin) (?:dictation|dictating|typing)|dictation mode|take dictation", lambda m: self.start_dictation()),
            (r"type(?: this| that| out)?[:,]? (.+)", lambda m: self.type_text(m.group(1))),
        ]

    def start_dictation(self):
        self.dictating = True
        self.say("Dictation on. Say stop dictation when you're done.")

    def type_text(self, text):
        type_into_window(self.say, text.strip())

    def typing(self, text):
        """While dictating: types what was said (or obeys "new line", "stop dictation"). True if it did."""
        if not self.dictating:
            return False
        low = text.lower().strip(" .!?")
        if re.fullmatch(r"(?:stop|end|finish|cancel|exit) (?:dictation|dictating|typing)|that'?s all|i'?m done", low):
            self.dictating = False
            self.say("Dictation off.")
        elif low in ("new line", "next line", "press enter", "enter"):
            desktop.keys("Return")
        elif low in ("new paragraph",):
            desktop.keys("Return")
            desktop.keys("Return")
        elif low in ("undo", "undo that", "delete that", "scratch that"):
            desktop.keys("ctrl+z")
        else:
            type_into_window(self.say, text.strip() + " ")
        return True
