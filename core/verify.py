"""Did it work? Cheap local checks after JARVIS does something: a new window or a changed title after a
launch, the program failing, the window's pixels changing after a click. What can't be seen is kept as
"couldn't tell", never as done; JARVIS only speaks up when something surely failed."""
import threading
import time

from system import desktop

LAUNCH_SECONDS = 10  # a window can take this long to show up
QUICK_SECONDS = 3  # a command without a window: a failure shows by then


class Result:
    def __init__(self, action, ok, what):
        self.action, self.ok, self.what, self.at = action, ok, what, time.time()

    def __repr__(self):
        return "<Result %r ok=%s %r>" % (self.action, self.ok, self.what)


last = None  # the latest Result, for "did that work?"


def record(action, ok, what):
    global last
    last = Result(action, ok, what)
    return last


def windows_now():
    """{window id: title} of the app windows (not JARVIS's own)."""
    return {w.id: w.title for w in desktop.windows() if "jarvis" not in w.wm_class}


def launched(action, proc, before, window, on_fail, errors=None):
    """In a thread: what came of `proc` (a Popen started just after `before` = windows_now(); None when the
    app was started some other way). window: it should open or change a window. on_fail(line) is called only for a sure failure;
    errors() gives its stderr for the message."""
    def watch():
        deadline = time.time() + (LAUNCH_SECONDS if window else QUICK_SECONDS)
        while time.time() < deadline:
            time.sleep(0.4)
            code = proc.poll() if proc else 0
            if code not in (None, 0):
                err = _last_line(errors() if errors else "")
                record(action, False, "it failed (exit %d)%s" % (code, ": " + err if err else ""))
                on_fail("Hmm, that didn't work%s." % (": " + err.rstrip(".") if err else ""))
                return
            if not window:
                if code == 0:
                    record(action, True, "it ran without errors")
                    return
                continue
            now = windows_now()
            new = [t for i, t in now.items() if i not in before]
            if new:
                record(action, True, "a window opened: %s" % (new[-1] or "untitled"))
                return
            changed = [t for i, t in now.items() if i in before and t != before[i]]
            if changed and code == 0:  # handed over to an app that was already open (a new tab, a file)
                record(action, True, "a window changed to %s" % changed[-1])
                return
        if proc and proc.poll() is None and not window:
            record(action, None, "it's still running")
        else:
            record(action, None, "nothing new showed up on screen" if window else "I couldn't tell")
    threading.Thread(target=watch, daemon=True).start()


class Pixels:
    """What a window looks like now, to tell afterwards whether a click changed anything. Local and quick:
    the window's area read straight from the X server, no screenshot file."""

    def __init__(self, window):
        self.window = window
        self.geometry = desktop.geometry(window)
        self.image = _grab(self.geometry)

    def moved(self):
        """True if the window moved or changed size since (then a picked spot is in the wrong place)."""
        return desktop.geometry(self.window) != self.geometry

    def changed(self, share=0.002):
        """True if more than `share` of the window's pixels changed (a blinking cursor doesn't count),
        None if it couldn't be read."""
        after = _grab(desktop.geometry(self.window) or self.geometry)
        if self.image is None or after is None:
            return None
        if after.size != self.image.size:
            return True
        from PIL import ImageChops
        diff = ImageChops.difference(self.image, after).convert("L").point(lambda v: 255 if v > 24 else 0)
        return diff.histogram()[255] > share * after.size[0] * after.size[1]


def _grab(geometry):
    if not geometry:
        return None
    try:
        from PIL import Image
        from Xlib import X, display
        d = display.Display()
        root = d.screen().root
        sw, sh = root.get_geometry().width, root.get_geometry().height
        x, y, w, h = geometry
        x, y = max(0, x), max(0, y)
        w, h = min(w, sw - x), min(h, sh - y)
        if w <= 0 or h <= 0:
            d.close()
            return None
        raw = root.get_image(x, y, w, h, X.ZPixmap, 0xFFFFFFFF)
        d.close()
        return Image.frombytes("RGB", (w, h), raw.data, "raw", "BGRX").resize((max(1, w // 2), max(1, h // 2)))
    except Exception:
        return None


def _last_line(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1][:160] if lines else ""
