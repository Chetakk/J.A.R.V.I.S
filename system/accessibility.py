"""The text an app shows and the buttons it has, through the accessibility tree (AT-SPI): quick and local,
no screenshot.

GTK and GNOME apps (the terminal, Files, Settings, dialogs) fill it in. Chrome and Electron apps like VS Code
leave it empty unless accessibility is switched on, so callers fall back to a screenshot.
"""
import difflib
import re
import time

WINDOW_BUTTONS = {"minimize", "maximize", "close", "restore"}
CLICKABLE = {"push button", "toggle button", "menu item", "check menu item", "radio menu item", "check box",
             "radio button", "link", "page tab", "menu", "combo box", "list item", "tree item", "icon"}


def _atspi():
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        return Atspi
    except (ImportError, ValueError):
        return None


def _frame(Atspi, pid, title):
    """The window of process `pid` whose title is `title` (its first one if none is), or None."""
    try:
        Atspi.set_timeout(800, 3000)  # a stuck app mustn't hang JARVIS
        desk = Atspi.get_desktop(0)
        app = next((a for a in (desk.get_child_at_index(i) for i in range(desk.get_child_count()))
                    if a and a.get_process_id() == pid), None)
        if not app:
            return None
        frames = [f for f in (app.get_child_at_index(i) for i in range(app.get_child_count())) if f]
        return next((f for f in frames if title and (f.get_name() or "") == title), frames[0] if frames else None)
    except Exception:
        return None


def window_text(pid, title="", seconds=2.0, limit=20000):
    """(text, is_terminal) for the window of process `pid` whose title is `title` ("" if there's none)."""
    Atspi = _atspi()
    frame = Atspi and _frame(Atspi, pid, title)
    if not frame:
        return "", False
    frame_name = frame.get_name() or ""
    deadline = time.time() + seconds
    pieces, terminal = [], [False]

    def walk(acc, depth):
        if depth > 40 or time.time() > deadline or sum(len(p) for p in pieces) > limit:
            return
        try:
            if not acc.get_state_set().contains(Atspi.StateType.SHOWING):
                return
            role = acc.get_role_name()
            if "Text" in (acc.get_interfaces() or []):
                n = Atspi.Text.get_character_count(acc)
                s = Atspi.Text.get_text(acc, max(0, n - limit) if role == "terminal" else 0, n)
                if s.strip():
                    pieces.append(s.strip())
                    terminal[0] = terminal[0] or role == "terminal"
            elif role in ("label", "heading", "push button", "menu item", "page tab", "list item", "check box",
                          "radio button", "link", "alert", "notification"):
                name = (acc.get_name() or "").strip()
                if name and name.lower() not in WINDOW_BUTTONS and name not in (title, frame_name):
                    pieces.append(name)
            for i in range(acc.get_child_count()):
                walk(acc.get_child_at_index(i), depth + 1)
        except Exception:
            pass

    walk(frame, 0)
    return "\n".join(pieces), terminal[0]


class Control:
    """Something in a window that can be pressed: a button, a menu item, a tab, a link."""

    def __init__(self, name, role, menu, showing, acc):
        self.name, self.role, self.menu, self.showing, self.acc = name, role, menu, showing, acc

    def __repr__(self):
        return "<Control %s %r%s>" % (self.role, self.name, " in %s" % self.menu if self.menu else "")

    def press(self):
        """True if the app took the click."""
        try:
            return bool(self.acc.do_action(0))
        except Exception:
            return False


def controls(pid, title="", seconds=2.0, limit=300):
    """The enabled controls of that window ([Control], in tree order). Menu items count while their menu is
    closed (pressing one works without opening it); anything else only when it's on screen."""
    Atspi = _atspi()
    frame = Atspi and _frame(Atspi, pid, title)
    if not frame:
        return []
    deadline = time.time() + seconds
    out = []

    def walk(acc, depth, menu, in_menu):
        if depth > 40 or time.time() > deadline or len(out) >= limit:
            return
        try:
            role = acc.get_role_name()
            states = acc.get_state_set()
            showing = states.contains(Atspi.StateType.SHOWING)
            if not (showing or in_menu or role == "menu bar"):
                return
            name = re.sub(r"\s+", " ", acc.get_name() or "").strip()
            if (role in CLICKABLE and name and name.lower() not in WINDOW_BUTTONS
                    and states.contains(Atspi.StateType.SENSITIVE)
                    and "Action" in (acc.get_interfaces() or []) and acc.get_n_actions() > 0):
                out.append(Control(name, role, menu, showing, acc))
            inside = role in ("menu bar", "menu") or in_menu
            for i in range(acc.get_child_count()):
                walk(acc.get_child_at_index(i), depth + 1, name if role == "menu" and name else menu, inside)
        except Exception:
            pass

    walk(frame, 0, "", False)
    return out


def norm(words):
    """"Find…" → "find", "_File" → "file": how a control's name is said."""
    words = re.sub(r"\.\.\.|…|[_&]", "", (words or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%]", " ", words)).strip()


def find(items, words, menu=None):
    """The control `words` names ("reset", "zoom in", "new tab in the file menu"), or None. Showing ones win ties."""
    want = norm(words)
    if menu:
        items = [c for c in items if norm(c.menu) == norm(menu)] or items
    best, best_score = None, 0
    for c in items:
        name, full = norm(c.name), norm("%s %s" % (c.menu, c.name))
        if want in (name, full):
            score = 5
        elif re.match(r"%s\b" % re.escape(want), name):
            score = 4
        elif set(want.split()) <= set(full.split()):
            score = 3
        elif difflib.SequenceMatcher(None, want, name).ratio() >= 0.8:
            score = 2
        else:
            continue
        score += 0.5 if c.showing else 0
        if score > best_score:
            best, best_score = c, score
    return best
