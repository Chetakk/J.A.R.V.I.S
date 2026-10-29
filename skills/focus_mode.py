""""focus for 45 minutes" / "do not disturb" / "stop focusing": no heads-ups, offers or
notification banners meanwhile. Survives a restart."""
import json
import os
import subprocess
import threading
import time

from skills.base import Skill
from skills.helpers import HOME
from skills.maths import NUM_WORDS, _number, _spoken

FOCUS_FILE = os.path.join(HOME, ".local/share/jarvis/focus.json")


class FocusMode(Skill):
    def __init__(self, jarvis):
        super().__init__(jarvis)
        self._focus_timer = None
        self.focus_until = 0.0
        self._restore_focus()

    def patterns(self):
        n = r"([\d.,]+|%s)" % "|".join(sorted(NUM_WORDS, key=len, reverse=True))
        return [
            (r"(?:start )?(?:focus(?: mode)?|do not disturb|don'?t disturb me|quiet mode)"
             r"(?: for %s ?(minutes?|mins?|hours?))?|let me focus(?: for %s ?(minutes?|mins?|hours?))?" % (n, n),
             self.focus),
            (r"(?:stop|end|cancel|turn off|exit) (?:focus(?:ing| mode)?|do not disturb|quiet mode)|i'?m done focusing",
             lambda m: self.end_focus()),
            (r"how (?:long|much time) (?:is )?left (?:on|in) (?:my )?focus(?: mode)?", lambda m: self.focus_left()),
        ]

    def focusing(self):
        return time.time() < self.focus_until

    def focus(self, m):
        groups = [g for g in m.groups() if g]
        minutes = 45
        if len(groups) == 2:
            minutes = _number(groups[0]) * (60 if groups[1].startswith("h") else 1)
        self._start_focus(time.time() + minutes * 60)
        self.say("Focus mode for %s. I'll keep quiet and hold notifications." % (
            "%d minutes" % minutes if minutes < 120 else "%s hours" % _spoken(minutes / 60)))

    def _start_focus(self, until):
        self.focus_until = until
        _banners(False)
        try:
            with open(FOCUS_FILE, "w") as f:
                json.dump({"until": until}, f)
        except OSError:
            pass
        if self._focus_timer:
            self._focus_timer.cancel()
        self._focus_timer = threading.Timer(max(0, until - time.time()), self._focus_over)
        self._focus_timer.daemon = True
        self._focus_timer.start()

    def _focus_over(self):
        if self.focus_until and time.time() >= self.focus_until - 1:
            self._clear_focus()
            self.j.inbox.put(("say", "Focus time's over. Nice work."))

    def end_focus(self):
        if not self.focusing():
            self.say("You weren't in focus mode.")
            return
        self._clear_focus()
        self.say("Focus mode off.")

    def _clear_focus(self):
        self.focus_until = 0.0
        if self._focus_timer:
            self._focus_timer.cancel()
            self._focus_timer = None
        _banners(True)
        try:
            os.remove(FOCUS_FILE)
        except OSError:
            pass

    def _restore_focus(self):
        """After a restart: carry on a focus session, or turn notifications back on if it ran out meanwhile."""
        try:
            with open(FOCUS_FILE) as f:
                until = json.load(f)["until"]
        except (OSError, ValueError, KeyError):
            return
        if until > time.time():
            self._start_focus(until)
        else:
            self._clear_focus()

    def focus_left(self):
        if not self.focusing():
            self.say("You're not in focus mode.")
        else:
            self.say("%d minutes left." % max(1, round((self.focus_until - time.time()) / 60)))


def _banners(on):
    """GNOME's notification pop-ups (the urgent ones still show during focus)."""
    subprocess.run(["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "true" if on else "false"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
