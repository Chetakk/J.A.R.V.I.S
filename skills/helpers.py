"""Small things several skills use: folders, spoken lists, the clipboard, typing into a window."""
import os
import re
import subprocess
import time

from system import desktop

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, "Projects")
# "the second one", "the first folder": which of the things on the card, or in the list JARVIS just read out.
ORDINALS = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4, "fifth": 5, "5th": 5, "five": 5, "sixth": 6, "6th": 6, "six": 6,
            "last": -1}
for _n, (_word, _nth) in enumerate(zip(
        "seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(),
        "seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth "
        "eighteenth nineteenth twentieth".split()), 7):
    ORDINALS.update({_word: _n, _nth: _n, "%dth" % _n: _n})
# Longest first, so "seventeen" isn't read as "seven"; up to two digits, for long lists ("number 12").
NTH = r"(%s|\d\d?)" % "|".join(sorted(ORDINALS, key=len, reverse=True))
TERMINALS = ("gnome-terminal", "xterm", "terminator", "tilix", "kitty", "alacritty", "konsole")
typed_until = 0.0  # JARVIS's own clipboard use (typing) isn't a copy of yours


def and_list(items):
    items = list(items)
    return " and ".join(items) if len(items) < 3 else "%s and %s" % (", ".join(items[:-1]), items[-1])


def or_list(items):
    items = list(items)
    return " or ".join(items) if len(items) < 3 else "%s or %s" % (", ".join(items[:-1]), items[-1])


def words(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in ("the", "a", "my", "an", "of", "file")]


def clipboard():
    """The clipboard's text, or "" when it holds none (an image is not text: decoding it crashed JARVIS)."""
    try:
        return subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True,
                              timeout=2).stdout.decode()
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return ""


def _clipboard_raw():
    """(bytes, type) of what's on the clipboard, an image kept as an image, so it can be put back as it was."""
    try:
        targets = subprocess.run(["xclip", "-o", "-selection", "clipboard", "-t", "TARGETS"], capture_output=True,
                                 timeout=2).stdout.decode(errors="replace").split()
        kind = next((t for t in targets if t.startswith("image/")), None)
        return subprocess.run(["xclip", "-o", "-selection", "clipboard"] + (["-t", kind] if kind else []),
                              capture_output=True, timeout=2).stdout, kind
    except (OSError, subprocess.TimeoutExpired):
        return b"", None


def set_clipboard(text, kind=None):
    subprocess.run(["xclip", "-selection", "clipboard"] + (["-t", kind] if kind else []),
                   input=text if isinstance(text, bytes) else text.encode(), timeout=2,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def type_into_window(say, text):
    """Type `text` into the focused window: put it on the clipboard, paste, then put yours back."""
    global typed_until
    win = desktop.active_window()
    if win and "jarvis" in win.wm_class:
        say("Click where you want it typed first.")
        return
    old, kind = _clipboard_raw()
    typed_until = time.time() + 1.5
    set_clipboard(text)
    terminal = win and any(t in win.wm_class for t in TERMINALS)
    desktop.keys("ctrl+shift+v" if terminal else "ctrl+v")
    time.sleep(0.25)  # let the app read the clipboard before it changes back
    set_clipboard(old, kind)


def file_badge(path):
    """A file-type badge for a card ("{file:pdf} ", drawn by ui/blocks.py), or "" for a folder or no extension."""
    ext = os.path.splitext(path)[1][1:]
    return "{file:%s} " % ext.lower() if ext.isalnum() and len(ext) <= 5 and not os.path.isdir(path) else ""
