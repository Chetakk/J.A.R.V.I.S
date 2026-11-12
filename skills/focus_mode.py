""""focus for 45 minutes" / "an hour and a half" / "do not disturb" / "stop focusing" / "pause focus" /
"add 10 minutes to my focus" / "start a pomodoro": no heads-ups, offers or notification banners meanwhile.
At the end it says how many notifications came in. Survives a restart."""
import json
import os
import re
import subprocess
import threading
import time

from skills.base import Skill
from skills.helpers import HOME
from skills.maths import NUM_WORDS, _number, _spoken

FOCUS_FILE = os.path.join(HOME, ".local/share/jarvis/focus.json")
POMODORO, BREAK = 25, 5  # minutes
FOCUS = r"(?:my |the )?(?:focus(?: mode| session| time)?|do not disturb|pomodoro)"


class FocusMode(Skill):
    group = "focus"
    does = "Focus mode: no heads-ups, offers or notification banners for a while"
    phrases = ("focus for 45 minutes (no heads-ups or notifications)", "focus for an hour and a half", "stop focusing",
               "pause focus", "resume focus", "add 10 minutes to my focus", "start a pomodoro",
               "how long is left on my focus", "what did i miss during focus")
    gives = "speech; banners held"
    permission = "auto"
    checks = "says when the notification banners couldn't be held; survives a restart"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self._focus_timer = None
        self.focus_until = 0.0
        self.paused = 0.0  # seconds that were left when focus was paused
        self.since = 0.0  # when the session started (its notifications are counted at the end)
        self.pomodoro = False
        self.ending = None  # what to say when it ends, for a session started for you (meeting mode)
        self._restore_focus()

    def patterns(self):
        return [
            (r"(?:start |begin )?(?:a )?(?:pomodoro|focus sprint)(?: timer| session)?", lambda m: self.focus_for(
                POMODORO, pomodoro=True)),
            (r"pause (?:my |the )?focus(?:ing| mode| session)?|(?:put )?focus(?: mode)? on hold", lambda m: self.pause()),
            (r"(?:resume|continue|unpause|back to) (?:my |the )?focus(?:ing| mode| session)?", lambda m: self.resume()),
            (r"(?:add|give me|put) (.+?)(?: more| extra)? (?:to|on|of) %s|extend %s(?: by| for)? (.+)|"
             r"(.+?) more (?:of )?(?:focus|focusing)" % (FOCUS, FOCUS), self.extend),
            (r"what did i miss (?:during|while|in) (?:my |the )?(?:focus(?:ing| mode| session| time)?|i was focusing)",
             lambda m: self.missed()),
            (r"(?:start )?(?:focus(?: mode)?|do not disturb|don'?t disturb me|quiet mode)(?: for (.+))?|"
             r"let me focus(?: for (.+))?|focus for (.+)", self.focus),
            (r"(?:stop|end|cancel|turn off|exit|finish|quit) (?:my |the )?(?:focus(?:ing| mode| session)?|do not disturb|"
             r"quiet mode|pomodoro)|i'?m done focusing", lambda m: self.end_focus()),
            (r"how (?:long|much time) (?:is )?left (?:on|in) (?:my |the )?(?:focus(?: mode| session)?|pomodoro)",
             lambda m: self.focus_left()),
        ]

    def focusing(self):
        return time.time() < self.focus_until

    def focus(self, m):
        said = next((g for g in m.groups() if g), None)
        minutes = minutes_in(said) if said else 45
        if not minutes:
            return False  # "focus for the exam": not a length
        self.focus_for(minutes)

    def focus_for(self, minutes, pomodoro=False):
        self.pomodoro, self.paused, self.since, self.ending = pomodoro, 0.0, time.time(), None
        held = self._start_focus(time.time() + minutes * 60)
        self.say("%s for %s. I'll keep quiet%s." % ("Pomodoro" if pomodoro else "Focus mode", _length(minutes),
            " and hold notifications" if held else ", but I couldn't hold your notification banners"))

    def pause(self):
        if not self.focusing():
            self.say("You're not in focus mode.")
            return
        left = self.focus_until - time.time()
        self._clear_focus()
        self.paused = left
        self.say(["Focus paused with %s left. Say resume focus when you're back." % _length(round(left / 60)),
                  "Paused, with %s to go. Say resume focus when you're ready." % _length(round(left / 60))])

    def resume(self):
        if not self.paused:
            self.say("There's no paused focus session." if not self.focusing() else "You're already focusing.")
            return
        left, self.paused = self.paused, 0.0
        self._start_focus(time.time() + left)
        self.say(["Back to focus, %s left." % _length(round(left / 60)),
                  "Focus resumed. %s to go." % _length(round(left / 60)).capitalize()])

    def extend(self, m):
        said = next((g for g in m.groups() if g), "")
        minutes = minutes_in(said)
        if not minutes:
            return False
        if not self.focusing():
            if self.paused:
                self.paused += minutes * 60
                self.say("Okay, %s more when you resume." % _length(minutes))
                return
            self.say("You're not in focus mode. Say focus for %s to start." % _length(minutes))
            return
        self._start_focus(self.focus_until + minutes * 60)
        self.say("Okay, %s more. %s left." % (_length(minutes), _length(round((self.focus_until - time.time()) / 60))))

    def missed(self):
        """What came in while you focused: the notifications, and JARVIS's own held heads-ups."""
        self.j.skills.notifications.read()

    def _start_focus(self, until):
        """Start the session. True if the notification banners are held too (False: only JARVIS keeps quiet)."""
        self.focus_until = until
        held = _banners(False)
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
        return held

    def _focus_over(self):
        if self.focus_until and time.time() >= self.focus_until - 1:
            ending = self.ending
            self._clear_focus()
            line = ending or ("Pomodoro done. Take a %d minute break." % BREAK if self.pomodoro else
                              "Focus time's over. Nice work.")
            self.j.skills.proactive.note("focus", line, "The focus session you started ended at %s."
                                         % time.strftime("%H:%M"))
            n = self._came_in()
            if n:
                line += " %d notification%s came in, say read my notifications." % (n, "" if n == 1 else "s")
            self.j.inbox.put(("say", line))
            if self.pomodoro:
                self.pomodoro = False
                t = threading.Timer(BREAK * 60, lambda: self.focusing() or self.j.inbox.put(
                    ("say", "Break's over. Say start a pomodoro for the next one.")))
                t.daemon = True
                t.start()

    def _came_in(self):
        w = getattr(self.j.skills.notifications, "watcher", None)
        try:
            return len(w.recent(self.since)) if w and self.since and w.running() else 0
        except Exception:  # the count is a nicety; the end of focus must still be said
            return 0

    def end_focus(self):
        if not self.focusing():
            self.say("You weren't in focus mode.")
            return
        self._clear_focus()
        self.say(["Focus mode off.", "Focus is over. Nicely done.", "Focus mode off. Back to normal."])

    def _clear_focus(self):
        self.focus_until, self.ending = 0.0, None
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
            self.say("Focus is paused, %s left." % _length(round(self.paused / 60)) if self.paused else
                     "You're not in focus mode.")
        else:
            self.say("%s left." % _length(max(1, round((self.focus_until - time.time()) / 60))).capitalize())


def minutes_in(text):
    """"an hour and a half", "90 minutes", "1.5 hours", "two hours", "half an hour", "an hour and 10 minutes"
    -> minutes, or None."""
    text = (text or "").lower().replace("-", " ").strip(" .")
    if text in ("half an hour", "half hour", "a half hour"):
        return 30
    num = r"([\d.]+|%s)" % "|".join(sorted(NUM_WORDS, key=len, reverse=True))
    m = re.fullmatch(r"(?:another |an? extra |extra )?%s ?(hours?|hrs?|minutes?|mins?)(?: and (?:a half|%s ?(minutes?|mins?)))?"
                     r"|(?:another |an? extra |extra )?%s and a half (hours?|hrs?)" % (num, num, num), text)
    if not m:
        return None
    try:
        if m.group(5):  # "one and a half hours"
            return (_number(m.group(5)) + 0.5) * 60
        n = _number(m.group(1)) * (60 if m.group(2).startswith("h") else 1)
        if "and a half" in text:
            n += 30 if m.group(2).startswith("h") else 0.5
        elif m.group(3):
            n += _number(m.group(3))
        return n or None
    except ValueError:
        return None


def _length(minutes):
    minutes = round(minutes)
    if minutes < 120 and minutes % 60:
        return "%d minute%s" % (minutes, "" if minutes == 1 else "s")
    h = minutes / 60
    return "%s hour%s" % (_spoken(h), "" if h == 1 else "s")


def _banners(on):
    """GNOME's notification pop-ups (the urgent ones still show during focus). True if the setting changed."""
    try:
        return subprocess.run(["gsettings", "set", "org.gnome.desktop.notifications", "show-banners",
                               "true" if on else "false"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
