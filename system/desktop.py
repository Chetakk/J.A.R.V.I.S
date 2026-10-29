"""Workspace helpers (X11 / EWMH): JARVIS opens its windows on its own workspace and takes you there."""
import os

import yaml
from Xlib import X, display, protocol

SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "settings.yaml")
_NET = {}


def go_to_jarvis_workspace():
    """Switch to JARVIS's workspace (settings: workspace.number) before it opens a new window."""
    try:
        with open(SETTINGS) as f:
            cfg = (yaml.safe_load(f) or {}).get("workspace") or {}
    except OSError:
        cfg = {}
    if cfg.get("enabled", True):
        switch_to(int(cfg.get("number", 5)))


def bring_up(title):
    """Show an existing window (e.g. the manual terminal) wherever it is; else go to JARVIS's workspace."""
    if not activate(title):
        go_to_jarvis_workspace()


def _atom(d, name):
    if name not in _NET:
        _NET[name] = d.intern_atom(name)
    return _NET[name]


def _send(d, win, name, data):
    root = d.screen().root
    ev = protocol.event.ClientMessage(window=win, client_type=_atom(d, name),
                                      data=(32, (data + [0] * 5)[:5]))
    root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.flush()


def switch_to(number):
    """Switch to workspace `number` (1-based)."""
    try:
        d = display.Display()
        _send(d, d.screen().root, "_NET_CURRENT_DESKTOP", [number - 1, X.CurrentTime])
        d.close()
    except Exception:
        pass


class Window:
    def __init__(self, wid, title, wm_class, workspace):
        self.id, self.title, self.wm_class, self.workspace = wid, title, wm_class, workspace

    def __repr__(self):
        return "<Window %s %r on %s>" % (self.wm_class, self.title, self.workspace)


def _prop(d, win, name):
    p = win.get_full_property(_atom(d, name), X.AnyPropertyType)
    return p.value if p else None


def windows():
    """All app windows, bottom to top (the last one is the front-most)."""
    out = []
    try:
        d = display.Display()
        for wid in _prop(d, d.screen().root, "_NET_CLIENT_LIST_STACKING") or []:
            win = d.create_resource_object("window", wid)
            name = _prop(d, win, "_NET_WM_NAME")
            title = name.decode("utf-8", "replace") if isinstance(name, bytes) else ""
            cls = " ".join(win.get_wm_class() or ()).lower()
            ws = _prop(d, win, "_NET_WM_DESKTOP")
            out.append(Window(wid, title, cls, (ws[0] + 1) if ws is not None and ws[0] < 1000 else None))
        d.close()
    except Exception:
        pass
    return out


def active_window():
    try:
        d = display.Display()
        wid = (_prop(d, d.screen().root, "_NET_ACTIVE_WINDOW") or [0])[0]
        d.close()
    except Exception:
        return None
    return next((w for w in windows() if w.id == wid), None)


def find_window(words):
    """Front-most window whose app name or title contains `words` (e.g. "chrome", "vs code", "youtube")."""
    words = words.lower().replace("vs code", "code").replace("vscode", "code").strip()
    for w in reversed(windows()):
        if words in w.wm_class or words in w.title.lower():
            return w
    return None


def _act(name, wid, data):
    try:
        d = display.Display()
        _send(d, d.create_resource_object("window", wid), name, data)
        d.close()
    except Exception:
        pass


def focus(window):
    _act("_NET_ACTIVE_WINDOW", window.id, [2, X.CurrentTime, 0])


def move_to_workspace(window, number):
    _act("_NET_WM_DESKTOP", window.id, [number - 1, 2])


def set_state(window, on, *props):
    """on: 1 add, 0 remove, 2 toggle; props like "_NET_WM_STATE_MAXIMIZED_VERT"."""
    try:
        d = display.Display()
        atoms = [_atom(d, p) for p in props] + [0]
        _send(d, d.create_resource_object("window", window.id), "_NET_WM_STATE", [on, atoms[0], atoms[1], 2])
        d.close()
    except Exception:
        pass


def minimize(window):
    try:
        d = display.Display()
        win = d.create_resource_object("window", window.id)
        ev = protocol.event.ClientMessage(window=win, client_type=d.intern_atom("WM_CHANGE_STATE"),
                                          data=(32, [3, 0, 0, 0, 0]))  # 3 = IconicState
        d.screen().root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        d.flush()
        d.close()
    except Exception:
        pass


def close_window(window):
    _act("_NET_CLOSE_WINDOW", window.id, [X.CurrentTime, 2])


def current_workspace():
    """The workspace you're on (1-based)."""
    try:
        d = display.Display()
        n = (_prop(d, d.screen().root, "_NET_CURRENT_DESKTOP") or [0])[0]
        d.close()
        return n + 1
    except Exception:
        return 1


def pid(window):
    try:
        d = display.Display()
        p = _prop(d, d.create_resource_object("window", window.id), "_NET_WM_PID")
        d.close()
        return p[0] if p else None
    except Exception:
        return None


def geometry(window):
    """(x, y, width, height) on screen, or None if the window is gone."""
    try:
        d = display.Display()
        win = d.create_resource_object("window", window.id)
        g = win.get_geometry()
        pos = win.translate_coords(d.screen().root, 0, 0)
        d.close()
        return -pos.x, -pos.y, g.width, g.height
    except Exception:
        return None


def workarea():
    """(x, y, width, height) of the screen without panels and docks."""
    try:
        d = display.Display()
        root = d.screen().root
        area = _prop(d, root, "_NET_WORKAREA")
        size = (0, 0, root.get_geometry().width, root.get_geometry().height)
        d.close()
        return tuple(area[:4]) if area is not None and len(area) >= 4 else size
    except Exception:
        return 0, 0, 1920, 1080


def tiling():
    """Pop Shell is tiling windows itself: placing them means swapping tiles, not setting their size."""
    import subprocess
    try:
        on = subprocess.run(["dconf", "read", "/org/gnome/shell/extensions/pop-shell/tile-by-default"],
                            capture_output=True, text=True, timeout=2).stdout.strip()
        exts = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                              capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return on == "true" and "pop-shell" in exts


def move_resize(window, x, y, w, h):
    _act("_NET_MOVERESIZE_WINDOW", window.id, [(0xF << 8) | (2 << 12), x, y, w, h])


def place(window, where):
    """Put `window` on the left or right half, or maximise it ("left", "right", "max")."""
    import time
    if where == "max":
        set_state(window, 1, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
        return
    set_state(window, 0, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
    if tiling():
        # Pop Shell: Super+Arrow only moves the focus. Swapping a tile is its adjust mode:
        # Super+Enter, Ctrl+Arrow, Enter.
        focus(window)
        time.sleep(0.3)
        for combo in ("super+Return", "ctrl+%s" % ("Left" if where == "left" else "Right"), "Return"):
            keys(combo)
            time.sleep(0.15)
        return
    x, y, w, h = workarea()
    half = w // 2
    move_resize(window, x if where == "left" else x + half, y, half, h)


def side(window):
    """Where `window` is now: "left", "right" or "max" (None if it's gone)."""
    g = geometry(window)
    if not g:
        return None
    ax, _, aw, _ = workarea()
    if g[2] >= aw * 0.8:
        return "max"
    return "left" if g[0] + g[2] / 2 < ax + aw / 2 else "right"


_MODS = {"ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "super": "Super_L"}


def keys(combo):
    """Press a key combination in the focused window, e.g. "ctrl+w", "ctrl+shift+Tab", "Page_Down", "k"."""
    from Xlib import XK
    from Xlib.ext import xtest
    d = display.Display()
    codes = []
    for part in combo.split("+"):
        sym = XK.string_to_keysym(_MODS.get(part.lower(), part))
        code = d.keysym_to_keycode(sym)
        if not code:
            d.close()
            raise ValueError("unknown key %r" % part)
        codes.append(code)
    for code in codes:
        xtest.fake_input(d, X.KeyPress, code)
    for code in reversed(codes):
        xtest.fake_input(d, X.KeyRelease, code)
    d.sync()
    d.close()


def click(x, y, button=1):
    """Click at (x, y) on the screen, then put the pointer back where it was."""
    import time
    from Xlib.ext import xtest
    d = display.Display()
    was = d.screen().root.query_pointer()
    xtest.fake_input(d, X.MotionNotify, x=x, y=y)
    d.sync()
    time.sleep(0.05)
    xtest.fake_input(d, X.ButtonPress, button)
    xtest.fake_input(d, X.ButtonRelease, button)
    d.sync()
    time.sleep(0.05)
    xtest.fake_input(d, X.MotionNotify, x=was.root_x, y=was.root_y)
    d.sync()
    d.close()


def window_with_role(role):
    """The window with that role (gnome-terminal --role), or None."""
    found = windows()
    try:
        d = display.Display()
        for w in found:
            prop = d.create_resource_object("window", w.id).get_full_property(_atom(d, "WM_WINDOW_ROLE"),
                                                                               X.AnyPropertyType)
            if prop and prop.value.decode("utf-8", "replace") == role:
                d.close()
                return w
        d.close()
    except Exception:
        pass
    return None


def activate_role(role):
    """Like activate(), but by window role (gnome-terminal --role), which apps can't rename. True if found."""
    try:
        d = display.Display()
        root = d.screen().root
        clients = root.get_full_property(_atom(d, "_NET_CLIENT_LIST"), X.AnyPropertyType)
        for wid in (clients.value if clients else []):
            win = d.create_resource_object("window", wid)
            prop = win.get_full_property(_atom(d, "WM_WINDOW_ROLE"), X.AnyPropertyType)
            if prop and prop.value.decode("utf-8", "replace") == role:
                _send(d, win, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0])
                d.close()
                return True
        d.close()
    except Exception:
        pass
    return False


def activate(title):
    """Bring the first window whose title contains `title` to the front (switching workspace). True if found."""
    try:
        d = display.Display()
        root = d.screen().root
        clients = root.get_full_property(_atom(d, "_NET_CLIENT_LIST"), X.AnyPropertyType)
        for wid in (clients.value if clients else []):
            win = d.create_resource_object("window", wid)
            name = win.get_full_property(_atom(d, "_NET_WM_NAME"), 0)
            if name and title in name.value.decode("utf-8", "replace"):
                _send(d, win, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0])
                d.close()
                return True
        d.close()
    except Exception:
        pass
    return False
