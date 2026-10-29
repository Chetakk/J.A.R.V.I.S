""""What can I click here" and "click Save" / "press the back button" / "press control shift t" in the window
you're looking at. A click is tried, surest first: the app's keyboard shortcut (config/app_keys.yaml), its
accessibility tree (GTK/GNOME apps), then a spot Claude finds on a screenshot, which is shown marked and only
clicked once you say yes. Afterwards JARVIS checks whether anything changed and says so if nothing did."""
import os
import re
import time

import yaml

from claude.bridge import WORKSPACE, ClaudeError
from core import verify
from skills.base import Skill
from skills.helpers import TERMINALS, and_list
from skills.screen import front_window, screenshot
from system import accessibility, desktop

APP_KEYS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "app_keys.yaml")
# Buttons that lose or send something: asked first even when you named them.
RISKY = re.compile(r"\b(delete|remove|discard|send|submit|pay|buy|purchase|order|close|quit|exit|erase|format|"
                   r"uninstall|reset|clear|sign out|log ?out|overwrite|replace|don'?t save|empty|publish|post|"
                   r"merge|push|deploy|install|restart|shut ?down|reboot|revert)\b", re.IGNORECASE)
KEY_NAMES = {"enter": "Return", "return": "Return", "escape": "Escape", "esc": "Escape", "tab": "Tab",
             "space": "space", "spacebar": "space", "backspace": "BackSpace", "back space": "BackSpace",
             "delete": "Delete", "insert": "Insert", "home": "Home", "end": "End", "page up": "Prior",
             "page down": "Next", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
             "up arrow": "Up", "down arrow": "Down", "left arrow": "Left", "right arrow": "Right",
             "plus": "plus", "minus": "minus"}
KEY_NAMES.update({"f%d" % i: "F%d" % i for i in range(1, 13)})
MODIFIERS = {"control": "ctrl", "ctrl": "ctrl", "shift": "shift", "alt": "alt", "super": "super",
             "windows": "super"}
PRONOUNS = {"it", "that", "this", "here", "there", "something"}
MAX_EDGE, MAX_PIXELS = 1568, 1150000  # what Claude sees without scaling it down, so its spot is exact


class Controls(Skill):
    def patterns(self):
        return [
            (r"what (?:can|should) i (?:click|press)(?: on)?(?: here| in this window| on (?:this|the|my) screen)?|"
             r"what (?:buttons|options|menus) (?:are|do i have|can i (?:click|press))"
             r"(?: (?:there|here|on (?:the |my )?screen|in this window))?|"
             r"(?:list|show me|read(?: out)?|tell me) (?:the |all the |all )?(?:buttons|menus|options|controls)"
             r"(?: (?:here|in this window|on (?:the |my )?screen))?|"
             r"what'?s in the (.+?) menu|(?:show me|list|read(?: out)?) the (.+?) menu", self.list_controls),
            (r"(?:choose|select|pick) (.+?) (?:from|in|under) the (.+?) menu", self.choose),
            (r"(?:click(?: on)?|press|tap) (.+?)(?: (?:in|from|under|on) the (.+?) menu)?", self.click),
            (r"hit (.+)", self.hit),  # "hit enter"; "hit me with a joke" isn't a click
        ]

    def choose(self, m):
        return self._click(m.group(1), m.group(2))

    def click(self, m):
        combo = _spoken_keys(m.group(1)) if not m.group(2) else None
        if combo:
            return self._keys(combo)
        return self._click(m.group(1), m.group(2))

    def hit(self, m):
        combo = _spoken_keys(m.group(1))
        return self._keys(combo) if combo else False

    def _keys(self, combo):
        win = front_window()
        if not win:
            self.say("I can't see a window to press that in.")
            return
        _focus(win)
        desktop.keys(combo)
        verify.record("press %s" % combo, None, "I pressed it, but keys don't show whether they worked")

    def _click(self, target, menu=None):
        name = _name(target)
        if not name:
            return False  # "click it": nothing to go on
        win = front_window()
        if not win:
            self.say("I can't see a window to click in.")
            return
        combo = None if menu else self._shortcut(win, name)
        if combo:
            if RISKY.search(name) and not self.j.ask("%s? Are you sure?" % name.capitalize()):
                self.say(["Okay, I won't.", "Alright, left it."])
                return
            before = verify.Pixels(win)
            _focus(win)
            desktop.keys(combo)
            self._report(name, before)
            return
        control = accessibility.find(accessibility.controls(desktop.pid(win), win.title), name, menu)
        if control:
            label = control.name.rstrip(".\u2026")
            if RISKY.search(label) and not self.j.ask("Click %s?" % label):
                self.say(["Okay, I won't.", "Alright, left it."])
                return
            before = verify.Pixels(win)
            if control.press():
                self._report(label, before)
                return
        if self.j.mode == "sys":
            self.say("I can't find %s here, and looking for it on screen needs Claude, which is off in system mode."
                     % name)
            return
        self._click_on_screen(win, name)

    def _shortcut(self, win, name):
        """The app's key for `name`, from config/app_keys.yaml (a terminal only uses its own section)."""
        try:
            with open(APP_KEYS) as f:
                cfg = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return None
        terminal = any(t in win.wm_class for t in TERMINALS)
        keys = {} if terminal else dict(cfg.get("any") or {})
        for app, table in cfg.items():
            if app != "any" and app in win.wm_class:
                keys.update(table or {})
        want = accessibility.norm(name)
        return next((combo for label, combo in keys.items() if accessibility.norm(str(label)) == want), None)

    def _click_on_screen(self, win, name):
        """Claude finds it on a screenshot of the window; it's shown marked, and clicked only after a yes."""
        self.say(["One sec, finding it.", "Let me find it on screen."])
        geometry = desktop.geometry(win)
        shot = verify.grab(geometry)
        if not shot:
            self.say("I couldn't take a picture of that window.")
            return
        img, (ox, oy) = shot
        scale = min(1.0, MAX_EDGE / max(img.size), (MAX_PIXELS / float(img.width * img.height)) ** 0.5)
        small = img.resize((int(img.width * scale), int(img.height * scale))) if scale < 1 else img
        path = os.path.join(WORKSPACE, "click.png")
        small.save(path)
        self.j.state("claude")
        try:
            spot, what = self.j.claude.locate(path, name, small.size)
        except ClaudeError as e:
            self.say("Sorry, I couldn't ask Claude: %s." % str(e).rstrip("."))
            return
        finally:
            self.j.state("thinking")
        if not spot:
            self.say("I can't find %s in this window%s." % (name, ": " + what.rstrip(".") if what else ""))
            return
        marked = os.path.join(WORKSPACE, "click_here.png")
        _mark(small, spot).save(marked)
        self.j.show({"title": "Click here?", "text": what, "image": marked})
        if not self.j.ask("I think it's the %s, marked on the card. Shall I click it?" % what.rstrip(".")):
            self.say(["Okay, I won't click.", "Alright, not clicking."])
            return
        if self.j.ui:
            self.j.ui.close_card()  # it mustn't be in the way
        if desktop.geometry(win) != geometry:
            self.say("The window moved, so that spot's wrong now. Ask me again.")
            return
        before = verify.Pixels(win)
        _focus(win)
        time.sleep(0.3)  # the card closing
        desktop.click(int(ox + spot[0] / scale), int(oy + spot[1] / scale))
        self._report(what, before)

    def _report(self, label, before):
        """Say what came of it: nothing when it visibly worked, the truth when nothing changed."""
        ok, what = before.report()
        verify.record("click %s" % label, ok, what)
        if ok:
            self.say(["Done.", "Clicked it."] if "window opened" not in what else "Done, %s." % what)
        else:
            self.say("I clicked %s, but nothing changed that I can see." % label)

    def list_controls(self, m):
        win = front_window()
        if not win:
            self.say("I can't see a window to look at.")
            return
        menu = m.group(1) or m.group(2)
        items = accessibility.controls(desktop.pid(win), win.title)
        if not items:  # the app doesn't share its tree: Claude reads the buttons off a screenshot
            path = os.path.join(WORKSPACE, "screen.png")
            self.say(["One sec, looking at it.", "Let me have a look."])
            screenshot(path)
            self.j._ask_claude("List what I can click in the front window (\"%s\")%s: the main buttons, tabs and "
                               "menus, most useful first, briefly." % (win.title, " in its %s menu" % menu if menu
                                                                      else ""), image=path)
            return
        app = win.title.split(" - ")[-1] or "This window"
        if menu:
            entries = [c.name for c in items if c.menu and accessibility.norm(c.menu) == accessibility.norm(menu)]
            if not entries:
                self.say("I don't see a %s menu here." % menu)
                return
            self.j.show({"title": "%s menu" % menu.title(), "text": "\n".join("- %s" % e for e in entries)})
            self.say("The %s menu has %s." % (menu, and_list(entries[:8]) + (", and more" if len(entries) > 8 else "")))
            return
        buttons = _unique(c.name for c in items if c.showing and not c.menu)
        menus = _unique(c.menu for c in items if c.menu)
        lines = (["**Buttons and tabs:** %s" % ", ".join(buttons)] if buttons else []) + [
            "**%s:** %s" % (name, ", ".join(_unique(c.name for c in items if c.menu == name))) for name in menus]
        self.j.show({"title": "What you can click in %s" % app, "text": "\n\n".join(lines)})
        parts = (["%d button%s like %s" % (len(buttons), "" if len(buttons) == 1 else "s", and_list(buttons[:3]))]
                 if buttons else []) + (["the %s menu%s" % (and_list(menus[:5]), "" if len(menus) == 1 else "s")]
                                        if menus else [])
        self.say("There's %s. It's all on the card; say click and the name." % " and ".join(parts))


def _unique(names):
    seen, out = set(), []
    for n in names:
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _name(target):
    """"the sign in link" → "sign in"; "" for "it" / "that"."""
    name = re.sub(r"^(?:on )?(?:the |a |that |this )", "", target.strip(), flags=re.IGNORECASE)
    name = re.sub(r" (?:button|link|tab|option|menu item|item|icon|check ?box|box|menu|entry)$", "", name,
                  flags=re.IGNORECASE).strip()
    return "" if name.lower() in PRONOUNS else name


def _spoken_keys(words):
    """"control shift t" → "ctrl+shift+t", "enter" → "Return"; None unless it's only keys."""
    parts = [p for p in re.split(r"[\s+-]+", words.lower().strip()) if p]
    mods = []
    while parts and parts[0] in MODIFIERS:
        mods.append(MODIFIERS[parts.pop(0)])
    rest = " ".join(parts)
    if rest in KEY_NAMES:
        key = KEY_NAMES[rest]
    elif re.match(r"^[a-z0-9]$", rest) and mods:
        key = rest
    else:
        return None
    return "+".join(mods + [key])


def _focus(win):
    active = desktop.active_window()
    if not active or active.id != win.id:
        desktop.focus(win)
        time.sleep(0.2)


def _mark(img, spot):
    from PIL import ImageDraw
    img = img.copy()
    draw = ImageDraw.Draw(img)
    x, y = spot
    size = max(16, img.width // 45)  # still easy to see on a small card
    for r, colour in ((size + 2, (255, 255, 255)), (size, (230, 30, 30)), (size - 2, (230, 30, 30))):
        draw.ellipse((x - r, y - r, x + r, y + r), outline=colour, width=3)
    draw.line((x - size // 3, y, x + size // 3, y), fill=(230, 30, 30), width=2)
    draw.line((x, y - size // 3, x, y + size // 3), fill=(230, 30, 30), width=2)
    return img
