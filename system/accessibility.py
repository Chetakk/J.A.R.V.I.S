"""The text an app shows, through the accessibility tree (AT-SPI): quick and local, no screenshot.

GTK and GNOME apps (the terminal, Files, Settings, dialogs) fill it in. Chrome and Electron apps like VS Code
leave it empty unless accessibility is switched on, so callers fall back to a screenshot.
"""
import time

WINDOW_BUTTONS = {"minimize", "maximize", "close", "restore"}


def window_text(pid, title="", seconds=2.0, limit=20000):
    """(text, is_terminal) for the window of process `pid` whose title is `title` ("" if there's none)."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return "", False
    try:
        Atspi.set_timeout(800, 3000)  # a stuck app mustn't hang JARVIS
        desk = Atspi.get_desktop(0)
        app = next((a for a in (desk.get_child_at_index(i) for i in range(desk.get_child_count()))
                    if a and a.get_process_id() == pid), None)
        if not app:
            return "", False
        frames = [app.get_child_at_index(i) for i in range(app.get_child_count())]
        frames = [f for f in frames if f] or []
        frame = next((f for f in frames if title and (f.get_name() or "") == title), frames[0] if frames else None)
    except Exception:
        return "", False
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
