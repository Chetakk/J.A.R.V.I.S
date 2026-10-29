"""Window layouts: "arrange my coding windows", "coding layout", "save this layout as reading", "list my layouts".

Layouts are in config/layouts.yaml; the ones you save go to ~/.local/share/jarvis/layouts.json (same names win).
Each window is found (or opened), moved to the layout's workspace and put left, right or maximised, then
checked: JARVIS says which ones didn't end up where they should.
"""
import difflib
import json
import os
import time

import yaml

from skills.base import Skill
from skills.helpers import HOME, and_list, or_list
from system import desktop

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "layouts.yaml")
SAVED = os.path.join(HOME, ".local/share/jarvis/layouts.json")
NOUN = r"(?:windows?|layout|setup|set up|workspace)"
# How to open a saved window's app again, by its app name (only used if commands.yaml has the phrase).
OPENERS = {"vscode": "open vs code", "gnome-terminal": "open terminal", "chrome": "open chrome",
           "firefox": "open firefox", "nautilus": "open files"}


class Layouts(Skill):
    def patterns(self):
        return [
            (r"save (?:this|the|my|the current)(?: window)? (?:layout|arrangement|setup|windows)"
             r"(?: as (?:my |the |a )?(.+?)(?: layout)?)?", self.save),
            (r"(?:what|which) layouts? (?:do i have|are there|have i (?:got|saved))|(?:list|show)(?: me)? (?:my |the )?layouts",
             lambda m: self.list_layouts()),
            (r"(?:delete|forget|remove) (?:my |the )?(.+?) layout", self.delete),
            (r"(?:arrange|set up|setup|lay out|layout|organi[sz]e|tidy(?: up)?|sort out|line up) (?:all )?(?:my |the )?"
             r"(?:(.+?) )?%s(?: for me)?|(?:switch to |load |use |open |apply )?(?:my |the )?(.+?) layout" % NOUN, self.apply),
        ]

    # --- the layouts ---

    def _all(self):
        try:
            with open(CONFIG) as f:
                layouts = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            layouts = {}
        try:
            with open(SAVED) as f:
                layouts.update(json.load(f))
        except (OSError, ValueError):
            pass
        return {k.lower(): v for k, v in layouts.items() if isinstance(v, dict) and v.get("windows")}

    def _named(self, said, layouts):
        """The layout `said` means ("coding", "code", "my coding"), or None."""
        said = (said or "").lower().strip()
        names = {}
        for name, lay in layouts.items():
            for n in [name] + [str(a).lower() for a in lay.get("aliases") or []]:
                names.setdefault(n, name)
        if said in names:
            return names[said]
        close = difflib.get_close_matches(said, names.keys(), n=1, cutoff=0.75)
        return names[close[0]] if close else None

    # --- arranging ---

    def apply(self, m):
        said = m.group(1) or m.group(2)
        layouts = self._all()
        if not layouts:
            return False
        if not said:
            if len(layouts) > 1:
                self.say("Which layout: %s?" % or_list(sorted(layouts)))
                return
            name = next(iter(layouts))
        else:
            name = self._named(said, layouts)
            if not name:
                if m.group(2):
                    return False  # "the bottom layout"? not one of ours
                self.say("I don't have a %s layout. I know %s. Arrange the windows and say: save this layout as %s."
                         % (said, and_list(sorted(layouts)), said))
                return
        self.arrange(name, layouts[name])

    def arrange(self, name, layout):
        ws = int(layout.get("workspace") or desktop.current_workspace())
        jarvis_ws = int((self.j.settings.get("workspace") or {}).get("number", 5))
        used, placed, failed = set(), [], []
        for item in layout["windows"]:
            app = str(item.get("app") or "").lower()
            win = _find(app, ws, jarvis_ws, used)
            if not win and item.get("open"):
                win = self._open(item["open"], app, used)
            if not win:  # last resort, even one on JARVIS's workspace (e.g. its own terminal)
                win = _find(app, ws, jarvis_ws, used, anywhere=True)
            if not win:
                failed.append("I couldn't find %s" % app)
                continue
            used.add(win.id)
            if win.workspace != ws:
                desktop.move_to_workspace(win, ws)
            placed.append((win, item.get("place")))
        if not placed:
            self.say(" and ".join(failed).capitalize() + ", so there was nothing to arrange.")
            return
        desktop.switch_to(ws)
        time.sleep(0.5)  # let the tiler take the moved windows in
        for win, where in placed:
            if where in ("left", "right", "max"):
                desktop.place(win, where)
                time.sleep(0.3)
        desktop.focus(placed[0][0])
        time.sleep(0.4)
        # Check it worked, not just that it was asked for.
        for win, where in placed:
            label = _label(win)
            got = next((w for w in desktop.windows() if w.id == win.id), None)
            if not got:
                failed.append("%s closed" % label)
            elif got.workspace not in (ws, None):
                failed.append("%s stayed on workspace %d" % (label, got.workspace))
            elif where in ("left", "right") and desktop.side(win) != where:
                failed.append("%s isn't on the %s" % (label, where))
        self.j.chat("sys", "layout %s: %s" % (name, ", ".join("%s → %s" % (_label(w), p or "as is") for w, p in placed)))
        if failed:
            self.say("I arranged your %s windows, but %s." % (name, and_list(failed)))
        else:
            self.say(["Your %s windows are set." % name, "Done, %s layout's ready." % name])

    def _open(self, phrase, app, used, seconds=10):
        """Open the app with a commands.yaml phrase and wait for its window. None if it never showed up."""
        match = self.j.registry.match_exact(phrase)
        if not match:
            return None
        before = {w.id for w in desktop.windows()}
        self.j.executor.run(match.shell(), reply="", declared=match.command.get("risk"))
        if not self.j.executor.ok:
            return None
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.4)
            new = [w for w in desktop.windows() if w.id not in before and w.id not in used and _matches(w, app)]
            if new:
                time.sleep(0.5)  # give it a moment to settle before moving it
                return new[-1]
        return None

    # --- saving ---

    def save(self, m):
        name = (m.group(1) or "").lower().strip()
        if not name:
            self.say("What should I call it? Say: save this layout as, and a name.")
            return
        ws = desktop.current_workspace()
        wins = [w for w in desktop.windows() if w.workspace == ws and w.title and "jarvis" not in w.wm_class]
        if not wins:
            self.say("There are no windows on this workspace to save.")
            return
        items = []
        for w in sorted(wins, key=lambda w: (desktop.geometry(w) or (0,))[0]):
            app = (w.wm_class.split() or [""])[0]
            item = {"app": app, "place": desktop.side(w) or "max"}
            opener = next((p for k, p in OPENERS.items() if k in app), None)
            if opener and self.j.registry.match_exact(opener):
                item["open"] = opener
            items.append(item)
        try:
            with open(SAVED) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        if name in saved and not self.j.ask("You already have a %s layout. Replace it?" % name):
            return
        saved[name] = {"workspace": ws, "windows": items}
        os.makedirs(os.path.dirname(SAVED), exist_ok=True)
        with open(SAVED, "w") as f:
            json.dump(saved, f, indent=1)
        self.j.chat("sys", "saved layout %s: %s" % (name, json.dumps(items)))
        self.say("Saved %d window%s as your %s layout. Say %s layout to bring it back."
                 % (len(items), "" if len(items) == 1 else "s", name, name))

    def list_layouts(self):
        layouts = self._all()
        if not layouts:
            self.say("You don't have any layouts yet.")
            return
        self.j.show({"title": "Window layouts", "text": "\n".join(
            "- **%s**: %s" % (n, ", ".join("%s %s" % (w.get("app"), w.get("place") or "") for w in l["windows"]))
            for n, l in sorted(layouts.items()))})
        self.say("You have %s." % and_list("%s" % n for n in sorted(layouts)))

    def delete(self, m):
        try:
            with open(SAVED) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        name = self._named(m.group(1), {k: v for k, v in saved.items() if isinstance(v, dict)})
        if not name:
            if not self._named(m.group(1), self._all()):
                return False
            self.say("That one's in config/layouts.yaml, so it stays. You can edit it there.")
            return
        if self.j.ask("Delete your %s layout?" % name):
            del saved[name]
            with open(SAVED, "w") as f:
                json.dump(saved, f, indent=1)
            self.say("Deleted.")


def _matches(win, app):
    app = app.replace("vs code", "code").replace("vscode", "code")
    return "jarvis" not in win.wm_class and (app in win.wm_class or app in win.title.lower())


def _find(app, ws, jarvis_ws, used, anywhere=False):
    """The window for `app`: one already on the layout's workspace, else one not on JARVIS's own (anywhere: any)."""
    found = [w for w in reversed(desktop.windows()) if w.id not in used and _matches(w, app)]
    tries = [lambda w: w.workspace == ws, lambda w: w.workspace != jarvis_ws] + ([lambda w: True] if anywhere else [])
    for ok in tries:
        win = next((w for w in found if ok(w)), None)
        if win:
            return win
    return None


def _label(win):
    cls = (win.wm_class.split() or ["window"])[-1]
    return {"com.microsoft.vscode": "VS Code", "gnome-terminal": "the terminal",
            "google-chrome": "Chrome"}.get(cls, cls.split(".")[-1])
