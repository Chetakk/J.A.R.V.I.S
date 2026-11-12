"""Windows and workspaces: switch workspace, move, maximise, snap left/right, close, focus an app."""
import re

from skills.base import Skill
from skills.helpers import NTH, ORDINALS
from skills.screen import front_window
from system import desktop

IT = re.compile(r"(?:it|this|that|this one|(?:the |this )?(?:video|page|tab|app))", re.I)
NUMBERS = {w: n for w, n in ORDINALS.items() if 0 < n <= 6}  # "three", "third", "3rd"
NUMBERS["forth"] = 4  # how "fourth" often comes out of speech
# "move 2nd to next workspace", "move the 3rd window ...": a window on this workspace, oldest opened first
NTH_WINDOW = re.compile(r"(?:%s|forth)(?: window| one| app)?" % NTH[1:-1], re.I)


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
            # "go to workspace 3", "take it to the next workspace", "previous workspace", "the third one workspace"
            (r"(?:(?:switch|go|jump|take|bring|show)(?: me| it| us| you)?(?: over)? to )?(?:the )?"
             r"(?:workspace (?:number )?(\w+)|(\w+)(?: one)? workspace)", self.workspace),
            (r"(?:move|put|send|shift) (?:(?:this|it|that)(?: window| one)?|(?:the )?(.+?))?\s*(?:to|on|onto) "
             r"(?:the )?(?:workspace (\w+)|(\w+) workspace)", self.move_window),
            (r"(maximi[sz]e|minimi[sz]e|full ?screen|restore)(?: the| this)?(?: window)?(?: (.+))?", self.window_state),
            (r"close (?:this|the) window", lambda m: self.close_window() or self.say("I don't see a window to close.")),
            (r"(?:put|move|snap) (?:this|it|the window|(.+?)) (?:to |on )?(?:the )?(left|right)(?: side| half)?",
             self.snap),
            (r"(?:switch to|focus|bring up|show me|go to) (.+)", self.focus),
        ]

    def workspace(self, m):
        n = _number(m.group(1) or m.group(2))
        if not n:
            return False
        desktop.switch_to(n)

    def move_window(self, m):
        target, word = m.group(1), (m.group(2) or m.group(3)).lower()
        if word not in ("next", "previous") and not _number(word):  # not a workspace: someone else's request
            return False
        if target and NTH_WINDOW.fullmatch(target):
            here = desktop.current_workspace()
            wins = [w for w in desktop.windows(stacking=False) if w.workspace == here and "jarvis" not in w.wm_class]
            nth = target.split()[0].lower()
            i = 4 if nth == "forth" else ORDINALS.get(nth) or int(nth)
            if not wins or i == 0 or i > len(wins):
                return self.fail("There %s only %d window%s on this workspace." % (
                    "is" if len(wins) == 1 else "are", len(wins), "" if len(wins) == 1 else "s"))
            win = wins[i - 1 if i > 0 else -1]
        elif target:
            win = desktop.find_window(target)
            if not win:
                return self.fail("I don't see %s open." % target)
        else:
            win = desktop.active_window()
        n = _number(word, win.workspace) if win else None
        if not n:
            return False
        desktop.move_to_workspace(win, n)
        self.say(["Moved it to workspace %d." % n, "It's on workspace %d now." % n])

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


def _number(word, base=None):
    """A workspace by its number or word; "next" / "previous" counted from `base` (else the one you're on)."""
    word = (word or "").lower()
    if word in ("next", "previous"):
        n = (base or desktop.current_workspace()) + (1 if word == "next" else -1)
        return n if n > 0 else None
    return int(word) if word.isdigit() else NUMBERS.get(word)
