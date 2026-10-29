"""Small things several skills use: folders, spoken lists, the clipboard, typing into a window."""
import os
import re
import subprocess
import time

from system import desktop

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, "Projects")
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
    try:
        return subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, text=True,
                              timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def set_clipboard(text):
    subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), timeout=2,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def type_into_window(say, text):
    """Type `text` into the focused window: put it on the clipboard, paste, then put yours back."""
    global typed_until
    win = desktop.active_window()
    if win and "jarvis" in win.wm_class:
        say("Click where you want it typed first.")
        return
    old = clipboard()
    typed_until = time.time() + 1.5
    set_clipboard(text)
    terminal = win and any(t in win.wm_class for t in TERMINALS)
    desktop.keys("ctrl+shift+v" if terminal else "ctrl+v")
    time.sleep(0.25)  # let the app read the clipboard before it changes back
    set_clipboard(old)
