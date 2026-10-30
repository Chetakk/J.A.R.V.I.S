""""What's on my screen": a screenshot for Claude to look at. "Read this window aloud": the window's text from
the accessibility tree when the app gives it (terminal, GNOME apps), else a screenshot Claude reads out."""
import os
import re
import subprocess

from claude.bridge import WORKSPACE
from skills.base import Skill
from system import accessibility, desktop

SPEAK_WORDS = 70  # longer than this: a terminal's end is spoken, all of it goes on a card


class Screen(Skill):
    group = "screen"
    does = "Reads the front window's text, or has Claude look at the screen"
    phrases = ("what's on my screen", "read this window aloud")
    gives = "speech (long text on a card)"
    permission = "auto"
    checks = "the window's own text when the app shares it; else a screenshot for Claude"

    def patterns(self):
        return [
            (r"read (?:out |aloud )?(?:this|the|my|that) (window|page|terminal|dialog|box|popup|error|message|app)"
             r"(?: (?:aloud|out loud|out|to me|for me))*|read (?:this|that|it) (?:aloud|out loud|out)",
             self.read_window),
            (r"(?:what'?s|what is) on (?:my|the) screen.*|(?:look at|check|read) (?:my|the) screen.*|"
             r"what am i looking at|what do you see|describe (?:my|the) screen|(?:what'?s|what is|explain) "
             r"(?:this|that) (?:error|page|window|code|message|thing).*|read (?:this|the) page.*", self.screen),
        ]

    def screen(self, m):
        path = os.path.join(WORKSPACE, "screen.png")
        self.say(["Let me take a look.", "One sec, looking at your screen."])
        screenshot(path)
        self.j._ask_claude(m.group(0), image=path)


    def read_window(self, m):
        win = front_window()
        if not win:
            self.say("I can't see a window to read.")
            return
        text, terminal = accessibility.window_text(desktop.pid(win), win.title)
        text = _speakable(text)
        words = text.split()
        if len(words) < 8:  # the app doesn't share its text (Chrome, VS Code): read it off a screenshot
            path = os.path.join(WORKSPACE, "screen.png")
            self.say(["One sec, reading it.", "Let me read that."])
            screenshot(path)
            self.j._ask_claude("Read out the main text of the front window (\"%s\"), word for word if it's short, "
                               "else the important part. No description of the screen." % win.title, image=path)
            return
        if not terminal:  # the start, all of it on a card; "read the rest" / "summarise it"
            self.j.skills.reading.long(text, win.title or "This window")
            return
        if len(words) <= SPEAK_WORDS:
            self.say(text)
            return
        self.j.show({"title": win.title or "This window", "text": "```\n%s\n```" % text[-6000:]})
        self.say("The last bit says: %s. The rest is on the card." % " ".join(words[-SPEAK_WORDS:]))


def front_window():
    """The window you're looking at: the active one, or, when that's JARVIS's own, the top one under it."""
    win = desktop.active_window()
    if win and "jarvis" not in win.wm_class:
        return win
    ws = desktop.current_workspace()
    return next((w for w in reversed(desktop.windows())
                 if "jarvis" not in w.wm_class and w.title and w.workspace in (ws, None)), None)


def _speakable(text):
    """Without icon-font symbols, prompt arrows and box lines, and with blank lines squeezed."""
    text = "".join(c for c in text if not (0xE000 <= ord(c) <= 0xF8FF or 0x2500 <= ord(c) <= 0x259F
                                           or c in "❯❮➜›‹▶◀"))
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def screenshot(path):
    """Whole screen, scaled down so it's quick to send."""
    from PIL import Image
    tmp = path + ".full.png"
    subprocess.run(["gnome-screenshot", "-f", tmp], timeout=10, stderr=subprocess.DEVNULL)
    img = Image.open(tmp)
    img.thumbnail((1600, 1600))
    img.save(path)
    os.remove(tmp)
