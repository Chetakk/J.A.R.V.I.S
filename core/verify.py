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
_watching = 0  # launches still being watched (their Result isn't in yet)
_changed = threading.Condition()


def record(action, ok, what):
    global last
    with _changed:
        last = Result(action, ok, what)
        _changed.notify_all()
    return last


def settled(since, timeout=LAUNCH_SECONDS + 1):
    """The Result of what was done after `since` (time.time()), waiting while a launch is still being watched;
    None when nothing was checked."""
    deadline = time.time() + timeout
    with _changed:
        while _watching and time.time() < deadline and not (last and last.at >= since):
            _changed.wait(max(0.05, deadline - time.time()))
        return last if last and last.at >= since else None


def windows_now():
    """{window id: title} of the app windows (not JARVIS's own)."""
    return {w.id: w.title for w in desktop.windows() if "jarvis" not in w.wm_class}


def launched(action, proc, before, window, on_fail, errors=None):
    """In a thread: what came of `proc` (a Popen started just after `before` = windows_now(); None when the
    app was started some other way). window: it should open or change a window. on_fail(line) is called only for a sure failure;
    errors() gives its stderr for the message."""
    global _watching

    def watch():
        global _watching
        try:
            _watch()
        finally:
            with _changed:
                _watching -= 1
                _changed.notify_all()

    def _watch():
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
    with _changed:
        _watching += 1
    threading.Thread(target=watch, daemon=True).start()


class Pixels:
    """What a window looks like now, to tell afterwards whether a click changed anything. Local and quick:
    the window's area read straight from the X server, no screenshot file."""

    def __init__(self, window):
        self.window = window
        self.geometry = desktop.geometry(window)
        self.image = _grab(self.geometry)
        self.windows = windows_now()

    def report(self, least=None):
        """(ok, what) a moment after an action in the window: True when something on screen changed,
        None when nothing did (it may still have worked: "copy" shows nothing). least: see changed()."""
        time.sleep(0.6)
        now = windows_now()
        new = [t for i, t in now.items() if i not in self.windows]
        if new:
            return True, "a window opened: %s" % (new[-1] or "untitled")
        title = now.get(self.window.id)
        if title is not None and title != self.windows.get(self.window.id):
            return True, "the window changed to %s" % title
        if self.changed(least=least):
            return True, "the window changed"
        return None, "nothing changed on screen"

    def moved(self):
        """True if the window moved or changed size since (then a picked spot is in the wrong place)."""
        return desktop.geometry(self.window) != self.geometry

    def changed(self, share=0.002, least=None):
        """True if more than `share` of the window's pixels changed (a blinking cursor doesn't count), or more
        than `least` pixels (of the half-size image: a few pasted words in a big window), None if it couldn't be
        read."""
        after = _grab(desktop.geometry(self.window) or self.geometry)
        if self.image is None or after is None:
            return None
        if after.size != self.image.size:
            return True
        from PIL import ImageChops
        diff = ImageChops.difference(self.image, after).convert("L").point(lambda v: 255 if v > 24 else 0)
        limit = share * after.size[0] * after.size[1]
        return diff.histogram()[255] > (min(limit, least) if least else limit)


def _grab(geometry):
    """Half size, with JARVIS's own windows (the orb, cards) blacked out: they change on their own."""
    shot = grab(geometry)
    if not shot:
        return None
    img, (ox, oy) = shot
    for w in desktop.windows():
        g = "jarvis" in w.wm_class and desktop.geometry(w)
        box = g and (max(0, g[0] - ox), max(0, g[1] - oy), min(img.width, g[0] - ox + g[2]),
                     min(img.height, g[1] - oy + g[3]))
        if box and box[2] > box[0] and box[3] > box[1]:
            img.paste((0, 0, 0), box)
    return img.resize((max(1, img.width // 2), max(1, img.height // 2)))


def grab(geometry):
    """(image, (x, y)) of that part of the screen as it looks now (its top left corner, kept on screen),
    or None."""
    if not geometry:
        return None
    try:
        from PIL import Image
        from Xlib import X, display
        d = display.Display()
        root = d.screen().root
        sw, sh = root.get_geometry().width, root.get_geometry().height
        x, y, w, h = geometry
        if x < 0:
            w, x = w + x, 0
        if y < 0:
            h, y = h + y, 0
        w, h = min(w, sw - x), min(h, sh - y)
        if w <= 0 or h <= 0:
            d.close()
            return None
        raw = root.get_image(x, y, w, h, X.ZPixmap, 0xFFFFFFFF)
        d.close()
        return Image.frombytes("RGB", (w, h), raw.data, "raw", "BGRX"), (x, y)
    except Exception:
        return None


def _last_line(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1][:160] if lines else ""
