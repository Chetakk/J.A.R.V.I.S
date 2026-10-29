""""What's on my screen": a screenshot for Claude to look at."""
import os
import subprocess

from claude.bridge import WORKSPACE
from skills.base import Skill


class Screen(Skill):
    def patterns(self):
        return [
            (r"(?:what'?s|what is) on (?:my|the) screen.*|(?:look at|check|read) (?:my|the) screen.*|"
             r"what am i looking at|what do you see|describe (?:my|the) screen|(?:what'?s|what is|explain) "
             r"(?:this|that) (?:error|page|window|code|message|thing).*|read (?:this|the) page.*", self.screen),
        ]

    def screen(self, m):
        path = os.path.join(WORKSPACE, "screen.png")
        self.say(["Let me take a look.", "One sec, looking at your screen."])
        _screenshot(path)
        self.j._ask_claude(m.group(0), image=path)


def _screenshot(path):
    """Whole screen, scaled down so it's quick to send."""
    from PIL import Image
    tmp = path + ".full.png"
    subprocess.run(["gnome-screenshot", "-f", tmp], timeout=10, stderr=subprocess.DEVNULL)
    img = Image.open(tmp)
    img.thumbnail((1600, 1600))
    img.save(path)
    os.remove(tmp)
