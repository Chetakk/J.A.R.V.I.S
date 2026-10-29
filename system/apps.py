"""Every app in the menu: "open rhythmbox", "close vs code", and (via the meaning matcher) "open the music player".

Read from the .desktop files at startup, so newly installed apps are known after a restart.
Saved commands in commands.yaml still come first.
"""
import configparser
import difflib
import glob
import os
import re

DIRS = ["/usr/share/applications", "~/.local/share/applications", "/var/lib/snapd/desktop/applications",
        "/var/lib/flatpak/exports/share/applications", "~/.local/share/flatpak/exports/share/applications"]
# What people call an app, beyond its menu name.
ALIASES = {"visual studio code": ["vs code", "vscode", "code"], "google chrome": ["chrome"],
           "gnome system monitor": [], "thunderbird mail": ["thunderbird", "email", "mail"],
           "vlc media player": ["vlc"], "mpv media player": ["mpv"], "aisleriot solitaire": ["solitaire"],
           "pulseaudio volume control": ["pavucontrol", "volume control", "sound mixer"],
           "libreoffice writer": ["writer", "word"], "libreoffice calc": ["calc", "excel"],
           "libreoffice impress": ["impress", "powerpoint"], "files": ["nautilus", "file manager"],
           "document viewer": ["pdf viewer", "evince"], "image viewer": ["photo viewer"],
           "text editor": ["gedit"], "terminal": ["gnome terminal"]}
# Apps that are really a browser window (web apps): closing their process would close the whole browser.
BROWSERS = {"google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser", "firefox"}
# Launcher -> the process it really runs as, where they differ.
PROCESS = {"libreoffice": "soffice.bin", "remmina-file-wrapper": "remmina", "google-chrome-stable": "chrome",
           "google-chrome": "chrome", "nautilus": "nautilus", "gnome-control-center": "gnome-control-c"}
OPEN = re.compile(r"^(?:open|launch|start|run|play|bring up|fire up|show me) (?:up )?(?:the |my )?(.+?)(?: app)?$",
                  re.IGNORECASE)
CLOSE = re.compile(r"^(?:close|quit|exit|kill|shut(?: down)?) (?:the |my )?(.+?)(?: app)?(?: down)?$", re.IGNORECASE)


def _read(path):
    c = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        c.read(path, encoding="utf-8")
        e = c["Desktop Entry"]
    except Exception:
        return None
    if (e.get("Type") != "Application" or e.get("NoDisplay", "").lower() == "true"
            or e.get("Hidden", "").lower() == "true" or not e.get("Name")):
        return None
    exe = os.path.basename((e.get("Exec") or "").split()[0]) if e.get("Exec") else ""
    if exe == "env":  # "env FOO=1 prog ..."
        exe = next((os.path.basename(w) for w in e.get("Exec").split()[1:] if "=" not in w), "")
    return {"path": path, "name": e.get("Name").strip(), "generic": (e.get("GenericName") or "").strip(),
            "keywords": [k.strip() for k in (e.get("Keywords") or "").split(";") if k.strip()],
            "process": "" if exe in BROWSERS and "chrome" not in e.get("Name").lower()
            and "firefox" not in e.get("Name").lower() else PROCESS.get(exe, exe)[:15]}


def _squash(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


class Apps:
    def __init__(self):
        self.apps, self.by_name = [], {}
        for d in DIRS:
            for path in sorted(glob.glob(os.path.join(os.path.expanduser(d), "*.desktop"))):
                app = _read(path)
                if not app or app["name"].lower() in self.by_name:
                    continue  # first one wins (a user's own copy is read after the system one only by name)
                self.apps.append(app)
                names = [app["name"].lower(), re.sub(r"^gnome ", "", app["name"].lower())]
                names += ALIASES.get(app["name"].lower(), [])
                for n in names:
                    self.by_name.setdefault(n, app)
        # "the music player": an app's description, when only one app has it.
        generic = {}
        for app in self.apps:
            if app["generic"]:
                generic.setdefault(app["generic"].lower(), []).append(app)
        for g, same in generic.items():
            if len(same) == 1:
                self.by_name.setdefault(g, same[0])

    def find(self, name):
        name = name.lower().strip()
        if name in self.by_name:
            return self.by_name[name]
        close = difflib.get_close_matches(name, self.by_name.keys(), n=1, cutoff=0.85)
        if close:
            return self.by_name[close[0]]
        # Misheard names, compared without spaces: "rhythm box", "rustisk", "shut will", "thunder bird".
        # On the installed apps, real misses scored 0.75+ and unrelated names 0.67 or less.
        said = _squash(name)
        if len(said) < 5:
            return None
        squashed = {_squash(n): app for n, app in self.by_name.items() if len(_squash(n)) >= 5}
        close = difflib.get_close_matches(said, squashed.keys(), n=1, cutoff=0.75)
        return squashed[close[0]] if close else None

    def intents(self):
        """{"open <app>": [other ways of asking for it]} for the meaning matcher."""
        out = {}
        for app in self.apps:
            says = []
            if app["generic"] and app["generic"].lower() != app["name"].lower():
                says += ["open the %s" % app["generic"].lower(), "open %s" % app["generic"].lower()]
            if app["keywords"]:
                says.append("open the %s app" % ", ".join(k.lower() for k in app["keywords"][:3]))
            out["open %s" % app["name"].lower()] = says
        return out

    def launch(self, app):
        from gi.repository import Gio
        info = Gio.DesktopAppInfo.new_from_filename(app["path"])
        return bool(info and info.launch([], None))
