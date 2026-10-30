"""Browser tabs and pages: close, reopen, next, back, reload, scroll, zoom, go to a tab by name."""
import time

from skills.base import Skill
from system import desktop

BROWSERS = ("google-chrome", "chromium", "firefox")


class BrowserTabs(Skill):
    group = "browser tabs"
    does = "Your browser's tabs and pages, by key presses"
    phrases = ("switch to the <name> tab", "close this tab", "new tab", "reopen the last tab", "next tab", "go back",
               "refresh the page", "scroll down", "zoom in")
    gives = "the key pressed in the browser"
    permission = "auto"
    checks = "a tab by name is checked against the window title; other keys aren't checked"

    def patterns(self):
        return [
            (r"close (?:this|the|that) tab", lambda m: self.tab("ctrl+w", "Closed.")),
            (r"(?:open )?(?:a )?new tab", lambda m: self.tab("ctrl+t", "")),
            (r"(?:re-?open|bring back|undo close) (?:the )?(?:last |closed )*tab", lambda m: self.tab("ctrl+shift+t", "")),
            (r"next tab", lambda m: self.tab("ctrl+Tab", "")),
            (r"previous tab|last tab", lambda m: self.tab("ctrl+shift+Tab", "")),
            (r"go back|back a page|previous page", lambda m: self.tab("alt+Left", "")),
            (r"go forward|forward a page|next page", lambda m: self.tab("alt+Right", "")),
            (r"(?:reload|refresh)(?: the| this)?(?: page| tab)?", lambda m: self.tab("F5", "")),
            (r"scroll (down|up)(?: a bit)?", lambda m: self.tab("Page_Down" if m.group(1) == "down" else "Page_Up", "")),
            (r"scroll to (?:the )?(top|bottom)", lambda m: self.tab("Home" if m.group(1) == "top" else "End", "")),
            (r"zoom (in|out)", lambda m: self.tab("ctrl+plus" if m.group(1) == "in" else "ctrl+minus", "")),
            (r"(?:reset|normal) zoom|zoom (?:to )?normal", lambda m: self.tab("ctrl+0", "")),
            (r"(?:go|switch|move) to the (next|previous|last) tab",
             lambda m: self.tab("ctrl+Tab" if m.group(1) == "next" else "ctrl+shift+Tab", "")),
            (r"(?:switch|go) to (?:the |my )?(.+?) tab", self.find_tab),
        ]

    def _browser(self):
        win = desktop.active_window()
        if win and any(b in win.wm_class for b in BROWSERS):
            return win
        for b in BROWSERS:
            win = desktop.find_window(b)
            if win:
                desktop.focus(win)
                time.sleep(0.4)
                return win
        return None

    def tab(self, combo, reply):
        if not self._browser():
            self.say("I don't see a browser open.")
            return
        desktop.keys(combo)
        if reply:
            self.say(reply)

    def find_tab(self, m):
        target = m.group(1).lower()
        win = self._browser()
        if not win:
            self.say("I don't see a browser open.")
            return
        first = None
        for _ in range(40):  # cycle through the tabs, reading the window title
            title = (desktop.find_window(win.wm_class.split()[0]) or win).title
            if target in title.lower():
                self.say(["Here it is.", "Switched."])
                return
            if title == first:
                break
            first = first or title
            desktop.keys("ctrl+Tab")
            time.sleep(0.2)
        self.say("I couldn't find a %s tab." % target)
