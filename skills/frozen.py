""""Is anything frozen", "Chrome isn't responding", "force quit this": each window is pinged (_NET_WM_PING, what
the desktop's own "not responding" dialog uses); one that doesn't answer is frozen. Its process says why (busy,
waiting on the disk, paused), and JARVIS offers to close it, or to resume a paused one. Nothing without a yes."""
import os
import re
import signal
import time

from core import verify
from skills.base import Skill
from skills.screen import front_window
from skills.slow_computer import PROTECTED
from system import desktop

PING_SECONDS = 2.0  # a working app answers in milliseconds; the desktop itself waits 5 s
KEEP = PROTECTED - {"python3"}  # your python3 apps may be closed; JARVIS itself is skipped by its pid
STUCK = (r"(?:frozen|stuck|hung|hanging|unresponsive|locked up|not responding|stopped responding|"
         r"isn'?t responding|is not responding|won'?t respond|doesn'?t respond|not doing anything)")
THIS = {"this", "it", "that", "this app", "this window", "this program", "the app", "the window", "this thing"}
ALL = "all"  # target() for "my computer": every window
EVERYTHING = {"computer", "everything", "screen", "desktop", "system", "jetson", "pc", "laptop"}


class Frozen(Skill):
    def patterns(self):
        return [
            (r"(?:is|are) (?:any(?:thing| apps?| windows?| programs?)?|something) %s|what'?s %s|"
             r"(?:which|what) (?:apps?|windows?|programs?) (?:is|are) %s|(?:check for|find|any) "
             r"(?:frozen|stuck|hung) (?:apps|windows|programs)|close (?:the |that )?(?:frozen|stuck|hung) "
             r"(?:app|window|program|one)" % (STUCK, STUCK, STUCK), lambda m: self.check(None)),
            (r"(?:is|has) (?:the |my )?(.+?) %s" % STUCK, self.about),
            (r"(?:why (?:is|has) |why'?s )?(?:the |my )?(.+?)(?: is|'s| has)? %s" % STUCK, self.about),
            (r"force (?:quit|close|kill|stop) (?:the |my )?(.+)|kill (this (?:app|window|program))", self.force),
        ]

    def about(self, m):
        found = target(m.group(1))
        if found is None:
            return False  # "the build is stuck": not a window
        self.check(None if found == ALL else found)

    def check(self, wins):
        """Ping `wins` (None: every window) and deal with the ones that don't answer."""
        everything = wins is None
        wins = [w for w in (desktop.windows() if everything else wins) if "jarvis" not in w.wm_class]
        if not wins:
            self.say("I can't see any windows to check.")
            return
        stuck = desktop.unresponsive(wins, PING_SECONDS)
        if not stuck:
            verify.record("check for frozen apps", True, "every window answered")
            self.say("Everything's responding." if everything else "%s is responding fine." % app_name(wins[0]))
            return
        for pid, group in _by_process(stuck).items():
            self._deal(pid, group)

    def force(self, m):
        found = target(m.group(1) or m.group(2))
        if not found or found == ALL:
            return False
        if desktop.unresponsive(found, PING_SECONDS):
            for pid, group in _by_process(found).items():
                self._deal(pid, group)
            return
        name = app_name(found[0])
        if self.j.ask("%s is still responding, so closing it the normal way is safer. Close it? Anything unsaved "
                      "may be lost." % name):
            for w in found:
                desktop.close_window(w)
            self._closed(name, found)
        else:
            self.say(["Okay, leaving it.", "Alright, I won't."])

    def _deal(self, pid, wins):
        name = app_name(wins[0])
        if not pid:
            self.say("%s isn't responding, and I can't tell which program it is, so I can't close it." % name)
            return
        process = _process(pid)
        if pid in (os.getpid(), os.getppid()) or (process and process[0].lower() in KEEP):
            self.say("%s isn't responding, but it's part of the desktop, so I'll leave it." % name)
            return
        paused, why = _why(pid)
        if not why:
            self.say("%s has just closed." % name)
            return
        if paused:
            if self.j.ask("%s isn't responding: %s. Want me to resume it?" % (name, why)):
                os.kill(pid, signal.SIGCONT)
                time.sleep(0.5)
                ok = not desktop.unresponsive(wins, PING_SECONDS)
                verify.record("resume %s" % name, ok, "it answers again" if ok else "it still doesn't answer")
                self.say("It's running again." if ok else "I resumed it, but it still isn't answering.")
            else:
                self.say(["Okay, leaving it paused.", "Alright, it stays paused."])
            return
        if not self.j.ask("%s isn't responding: %s. Want me to close it? Anything unsaved in it will be lost."
                          % (name, why)):
            self.say(["Okay, leaving it.", "Alright, I'll leave it be."])
            return
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 3
        while _alive(pid) and time.time() < deadline:
            time.sleep(0.2)
        if _alive(pid):  # a frozen app often can't act on the polite request
            os.kill(pid, signal.SIGKILL)
        self._closed(name, wins)

    def _closed(self, name, wins):
        """Say whether its windows really went."""
        for _ in range(10):
            time.sleep(0.3)
            left = {w.id for w in desktop.windows()} & {w.id for w in wins}
            if not left:
                break
        verify.record("close %s" % name, not left, "its window is gone" if not left else "its window is still there")
        self.say("Closed %s." % name if not left else "I tried, but %s's window is still there." % name)


def target(words):
    """The windows `words` means: ALL for "my computer", the front one for "this app", the app's windows for
    "chrome"; None when it's no window at all."""
    words = re.sub(r"^(?:the|my) ", "", (words or "").lower().strip())
    if words in EVERYTHING:
        return ALL
    if words in THIS or "the " + words in THIS:
        win = front_window()
        return [win] if win else None
    win = desktop.find_window(words)
    if not win or "jarvis" in win.wm_class:
        return None
    return [w for w in desktop.windows() if w.wm_class == win.wm_class] or [win]


def app_name(win):
    """"Google Chrome" for a Chrome window, else its title's last part, else its app's name."""
    title = win.title.split(" - ")[-1].strip()
    return title if 0 < len(title) <= 40 else (win.wm_class.split() or ["it"])[-1].capitalize()


def _by_process(wins):
    out = {}
    for w in wins:
        out.setdefault(desktop.pid(w), []).append(w)
    return out


def _process(pid):
    """(name, state letter, cpu ticks) for `pid`, or None if it's gone."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            stat = f.read()
        fields = stat[stat.rindex(")") + 2:].split()
        return stat[stat.index("(") + 1:stat.rindex(")")], fields[0], int(fields[11]) + int(fields[12])
    except (OSError, ValueError, IndexError, TypeError):
        return None


def _alive(pid):
    p = _process(pid)
    return bool(p) and p[1] != "Z"


def _why(pid, seconds=0.5):
    """(paused, why it's stuck in a few words) from its process state and CPU use; why is "" if it's gone."""
    before = _process(pid)
    time.sleep(seconds)
    after = _process(pid)
    if not (before and after) or after[1] == "Z":
        return False, ""
    if after[1] in "Tt":
        return True, "it's paused, stopped from a terminal or a debugger"
    if after[1] == "D":
        return False, "it's waiting on the disk or a device, so it may come back by itself"
    cpu = 100.0 * (after[2] - before[2]) / os.sysconf("SC_CLK_TCK") / seconds
    if cpu >= 50:
        return False, "it's busy, using %d percent CPU, so it may come back by itself" % cpu
    return False, "it's stuck waiting, not working on anything, so it probably won't come back"
