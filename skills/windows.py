"""Windows and workspaces: switch workspace, move, maximise, snap left/right, close, focus an app."""
import re

from skills.base import Skill
from skills.screen import front_window
from system import desktop

IT = re.compile(r"(?:it|this|that|this one|(?:the |this )?(?:video|page|tab|app))", re.I)
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


class Windows(Skill):
    group = "windows"
    does = "Windows and workspaces: switch, move, maximise, snap, close"
    phrases = ("switch to <app>", "go to workspace <n>", "move this to workspace <n>", "maximize <app>",
               "minimize <app>", "put <app> on the left", "close this window")
    gives = "the window changed"
    permission = "auto"
    asks = "before closing a window"
    checks = "not checked"

    def patterns(self):
        return [
            (r"(?:switch|go) to workspace (\w+)", self.workspace),
            (r"move (?:this|the|this window|it|(.+?)) to workspace (\w+)", self.move_window),
            (r"(maximi[sz]e|minimi[sz]e|full ?screen|restore)(?: the| this)?(?: window)?(?: (.+))?", self.window_state),
            (r"close (?:this|the) window", lambda m: self.close_window() or self.say("I don't see a window to close.")),
            (r"(?:put|move|snap) (?:this|it|the window|(.+?)) (?:to |on )?(?:the )?(left|right)(?: side| half)?",
             self.snap),
            (r"(?:switch to|focus|bring up|show me|go to) (.+)", self.focus),
        ]

    def workspace(self, m):
        n = _number(m.group(1))
        if not n:
            return False
        desktop.switch_to(n)

    def move_window(self, m):
        n = _number(m.group(2))
        win = desktop.find_window(m.group(1)) if m.group(1) else desktop.active_window()
        if not (n and win):
            return False
        desktop.move_to_workspace(win, n)
        self.say("Moved it to workspace %d." % n)

    def window_state(self, m):
        action = m.group(1).lower().replace(" ", "")
        target = (m.group(2) or "").strip()
        # "maximise it", "minimise this": the window you're looking at, not one with "it" in its title
        win = front_window() if not target or IT.fullmatch(target) else desktop.find_window(target)
        if not win:
            return False
        if action.startswith("minimi"):
            desktop.minimize(win)
        elif action == "fullscreen":
            desktop.set_state(win, 2, "_NET_WM_STATE_FULLSCREEN")
        elif action == "restore":
            desktop.set_state(win, 0, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
            desktop.set_state(win, 0, "_NET_WM_STATE_FULLSCREEN")
        else:
            desktop.set_state(win, 1, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")

    def close_window(self):
        """The window you're looking at, not JARVIS's own. False when there's none."""
        win = front_window()
        if not win:
            return False
        if self.j.ask("Close %s?" % (win.title.split(" - ")[-1].strip() or "this window")):
            desktop.close_window(win)
        else:
            self.say("Okay, leaving it.")
        return True

    def snap(self, m):
        win = desktop.find_window(m.group(1)) if m.group(1) else desktop.active_window()
        if not win:
            return False
        desktop.place(win, m.group(2).lower())

    def focus(self, m):
        target = m.group(1).strip()
        if re.search(r"\b(workspace|tab|website|site|page)\b", target, re.IGNORECASE):
            return False
        win = desktop.find_window(target)
        if not win:
            return False
        desktop.focus(win)


def _number(word):
    word = (word or "").lower()
    return int(word) if word.isdigit() else NUMBERS.get(word)
