""""type: see you at five", and "start dictation" ... "stop dictation" (typed as you speak;
"new line" presses Enter). "Type in Discord" switches to Discord and dictates there. Each one first puts the
cursor in the window's main text box (a chat's message box, not its search box) when the accessibility tree
shows it; otherwise it types where the cursor is."""
import re
import time

from skills import copy_between
from skills.base import Skill
from skills.helpers import type_into_window
from system import accessibility, desktop


class Dictation(Skill):
    group = "typing"
    does = "Types text into the focused window, or everything you say while dictating"
    phrases = ("type <text> (into the focused window)", "type in <app>", "type", "start dictation",
               "stop dictation")
    gives = "text typed where your cursor is"
    permission = "auto"
    checks = "not checked; your clipboard is put back afterwards, and it never types into JARVIS's own window"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.dictating = False

    def patterns(self):
        return [
            (r"(?:start|begin) (?:dictation|dictating|typing)|dictation mode|take dictation|type|type here",
             lambda m: self.start_dictation()),
            (r"(?:start )?typ(?:e|ing) (?:in|into|on) (?:the |my )?((?:\S+ ?){1,3}?)(?: app| window)?(?: for me)?",
             self.type_in),
            (r"type(?: this| that| out)?[:,]? (.+)", lambda m: self.type_text(m.group(1))),
        ]

    def start_dictation(self):
        main_box(desktop.active_window())
        self.dictating = True
        self.say("Dictation on. Say stop dictation when you're done.")

    def type_in(self, m):
        win, app = copy_between.target(self.j, m.group(1))
        if not win and not app:
            return False  # "type in lowercase please" is just text to type
        if not win:  # installed but not open, or only in the tray (Vesktop): open it, never type "in discord"
            win = copy_between.open_window(self.j, app)
            if not win:
                self.say("I opened %s, but no window showed up to type in." % app["name"])
                return
        here = desktop.active_window()
        if not here or here.id != win.id:
            desktop.focus(win)
            time.sleep(0.3)  # let it come to the front before looking for its box
        self.start_dictation()

    def type_text(self, text):
        main_box(desktop.active_window())
        text, send = with_send(text)
        type_into_window(self.say, text)
        if send:
            desktop.keys("Return")

    def at_cursor(self, text):
        """The transcribe key: what you said, typed exactly where the cursor is. Never read as a command."""
        type_into_window(self.say, text + " ")

    def typing(self, text):
        """While dictating: types what was said (or obeys "new line", "stop dictation"). True if it did."""
        if not self.dictating:
            return False
        # "Stop uh typing" is still "stop typing": hesitations don't count when matching what follows.
        low = " ".join(w for w in re.sub(r"[^\w' ]", "", text.lower()).split()
                       if w not in ("uh", "um", "uhm", "umm", "hmm", "er"))
        # Whisper hears "stop transcribing" as "stop transcripting"; "I said stop typing" is said when it didn't stop.
        if re.fullmatch(r"(?:(?:i said|please|ok|okay|now|just) )*(?:(?:stop|end|finish|cancel|exit|quit|turn off)"
                        r" (?:the )?(?:dictation|dictating|typing|transcri\w*|listening)(?: now| please)?"
                        r"|stop|that'?s all|i'?m done)", low):
            self.dictating = False
            self.say("Dictation off.")
        elif low in ("type", "type here", "start dictation", "start typing", "dictation mode"):
            self.say("Still dictating.")  # "Jarvis, type" again mustn't type the word "Type."
        elif low in ("new line", "next line", "press enter", "enter", "send", "send it", "send that"):
            desktop.keys("Return")
        elif low in ("new paragraph",):
            desktop.keys("Return")
            desktop.keys("Return")
        elif low in ("undo", "undo that", "delete that", "scratch that"):
            desktop.keys("ctrl+z")
        else:
            text, send = with_send(text)
            type_into_window(self.say, text if send else text + " ")
            if send:
                desktop.keys("Return")
        return True


# "fix the bug, send it" / "fix the bug and send it": needs the comma or "and", so "can you send it" is just typed.
SEND = re.compile(r"(.+?)(?:\s*[,.!?]\s*|\s+and\s+)(?:send(?: it| that)?(?: now)?|press enter)\W*", re.I)


def with_send(text):
    """(text to type, whether to press Enter after it)."""
    m = SEND.fullmatch(text.strip())
    return (m.group(1), True) if m else (text.strip(), False)


def main_box(win):
    """Put the cursor in `win`'s main text box, unless it's already in one; clicks the box if the app won't
    take focus. Does nothing when the window's accessibility tree is empty (Chrome and Electron, until
    accessibility is on)."""
    pid = win and "jarvis" not in win.wm_class and desktop.pid(win)
    box = pid and accessibility.main_text_box(accessibility.text_boxes(pid, win.title))
    if not box or box.focused:
        return
    if not box.focus():
        x, y, w, h = box.box
        desktop.click(x + w // 2, y + h // 2)
    time.sleep(0.1)
