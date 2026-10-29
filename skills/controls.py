""""What can I click here": the front window's buttons, tabs and menus, from the accessibility tree when the
app shares it (GTK/GNOME apps), else from a screenshot Claude looks at (Chrome, VS Code, Electron)."""
import os

from claude.bridge import WORKSPACE
from skills.base import Skill
from skills.helpers import and_list
from skills.screen import front_window, screenshot
from system import accessibility, desktop


class Controls(Skill):
    def patterns(self):
        return [
            (r"what (?:can|should) i (?:click|press)(?: on)?(?: here| in this window| on (?:this|the|my) screen)?|"
             r"what (?:buttons|options|menus) (?:are|do i have|can i (?:click|press))"
             r"(?: (?:there|here|on (?:the |my )?screen|in this window))?|"
             r"(?:list|show me|read(?: out)?|tell me) (?:the |all the |all )?(?:buttons|menus|options|controls)"
             r"(?: (?:here|in this window|on (?:the |my )?screen))?|"
             r"what'?s in the (.+?) menu|(?:show me|list|read(?: out)?) the (.+?) menu", self.list_controls),
        ]

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
