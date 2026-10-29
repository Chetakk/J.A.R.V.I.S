"""Screens through xrandr: what's connected, at what resolution and refresh rate, and changing them. A wrong mode
can leave the screen black, so try() puts the old one back by itself unless keep() is called in time."""
import re
import subprocess
import threading
from collections import namedtuple

Screen = namedtuple("Screen", "name primary mode rate modes preferred")  # modes: {"1920x1080": ["60.00", ...]}
NAMED = {"4k": "3840x2160", "2160p": "3840x2160", "1440p": "2560x1440", "1080p": "1920x1080", "full hd": "1920x1080",
         "900p": "1600x900", "720p": "1280x720", "hd": "1280x720"}


def screens():
    """The connected screens, primary first."""
    try:
        out = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found, cur = [], None
    for line in out.splitlines():
        head = re.match(r"(\S+) connected( primary)?", line)
        if head:
            cur = {"name": head.group(1), "primary": bool(head.group(2)), "mode": None, "rate": None, "modes": {},
                   "preferred": None}
            found.append(cur)
            continue
        if not line.startswith(" "):
            cur = None  # a disconnected output, or the screen line
            continue
        m = re.match(r"\s+(\d+x\d+)\S*\s+(.*)", line)
        if not m or not cur:
            continue
        rates = cur["modes"].setdefault(m.group(1), [])
        for rate, star, plus in re.findall(r"(\d+\.\d+)(\*?)\s?(\+?)", m.group(2)):
            rates.append(rate)
            if star:
                cur["mode"], cur["rate"] = m.group(1), rate
            if plus:
                cur["preferred"] = (m.group(1), rate)
    return sorted((Screen(**s) for s in found), key=lambda s: not s.primary)


def resolution(words):
    """"1280x720", "1280 by 720", "720p", "full hd" -> "1280x720"; None if it isn't one."""
    words = words.lower().strip()
    m = re.search(r"(\d{3,4})\s*(?:x|by|\*)\s*(\d{3,4})", words)
    if m:
        return "%sx%s" % m.groups()
    return next((v for k, v in NAMED.items() if re.search(r"\b%s\b" % k, words)), None)


def best_rate(screen, mode, rate=None):
    """`rate` (nearest one the mode has), else the screen's current rate if the mode has it, else the nearest to 60."""
    rates = screen.modes.get(mode) or []
    if not rates:
        return None
    want = float(rate) if rate else float(screen.rate) if screen.rate in rates else 60.0
    return min(rates, key=lambda r: abs(float(r) - want))


def apply(name, mode, rate):
    try:
        return subprocess.run(["xrandr", "--output", name, "--mode", mode, "--rate", rate], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Trial:
    """A change that goes back by itself after `seconds` unless keep() comes first."""

    def __init__(self, screen, mode, rate, seconds):
        self.old = (screen.name, screen.mode, screen.rate)
        self.lock = threading.Lock()
        self.done = False
        self.ok = apply(screen.name, mode, rate)
        self.timer = threading.Timer(seconds, self.revert)
        self.timer.daemon = True
        if self.ok:
            self.timer.start()

    def keep(self):
        """True if it was kept; False if it had already gone back."""
        with self.lock:
            if self.done:
                return False
            self.done = True
            self.timer.cancel()
            return True

    def revert(self):
        with self.lock:
            if self.done:
                return False
            self.done = True
            self.timer.cancel()
        return apply(*self.old)
