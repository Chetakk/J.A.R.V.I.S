""""paste the thing I copied before", "paste number 3", "clipboard history".
From CopyQ when it's running (so images and files too); otherwise JARVIS's own list, in memory only, never saved."""
import collections
import json
import os
import subprocess
import time
from urllib.parse import unquote, urlparse

from skills import helpers
from skills.base import Skill
from skills.helpers import set_clipboard, type_into_window
from system import desktop

ORDINAL = {"last": 1, "previous": 2, "one before": 2, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
           "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}
SHOWN = 10
# One CopyQ call for the whole card: a JSON line per item, newest first.
LIST_SCRIPT = ("for (var i = 0; i < Math.min(size(), %d); ++i) print(JSON.stringify({text: str(read('text/plain', i)),"
               " formats: str(read('?', i)), uris: str(read('text/uri-list', i))}) + '\\n')" % SHOWN)


def copyq(*args):
    """CopyQ's answer, or None when it isn't installed or running."""
    try:
        res = subprocess.run(["copyq"] + list(args), capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def label(item):
    """What a CopyQ item is, in a few words: its text, "an image", or the file names."""
    if item.get("text", "").strip():
        return item["text"]
    files = [os.path.basename(unquote(urlparse(u).path.rstrip("/"))) for u in item.get("uris", "").split()
             if u.startswith("file://")]
    if files:
        return "file%s: %s" % ("s" if len(files) > 1 else "", ", ".join(files))
    return "an image" if "image/" in item.get("formats", "") else "(something without text)"


class ClipboardHistory(Skill):
    group = "clipboard history"
    does = "The last few things you copied: CopyQ's history when it runs (images and files too), else in memory"
    phrases = ("paste the thing I copied before", "paste number 3", "clipboard history")
    gives = "text pasted, or a card"
    permission = "auto"
    checks = "not checked; JARVIS never writes it to disk (CopyQ keeps its own)"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.copies = collections.deque(maxlen=10)  # newest last

    def patterns(self):
        return [
            (r"paste (?:the )?(?:thing|text|one) i copied (?:before|earlier|before that)|paste (?:the )?previous "
             r"(?:copy|clipboard|thing)|paste (?:the )?(last|previous|second|2nd|third|3rd|fourth|4th|fifth|5th)"
             r"(?: last)? (?:copy|thing i copied|one)|paste (?:number |copy )(\d+)", self.paste_copy),
            (r"(?:show |read |what'?s in )?(?:my |the )?clipboard history|what did i copy before|"
             r"what (?:else )?have i copied", lambda m: self.copy_history()),
        ]

    def watch_clipboard(self):
        """Call from the GTK thread (needs the UI): remember each copy, newest last."""
        from gi.repository import Gdk, Gtk
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        def got(_clip, text):
            if text and text.strip() and time.time() > helpers.typed_until:
                if text in self.copies:
                    self.copies.remove(text)
                self.copies.append(text)
        clip.connect("owner-change", lambda c, _e: c.request_text(got))
        clip.request_text(got)
        return False  # for GLib.idle_add: run once

    def _copyq_items(self):
        """CopyQ's newest items as {"text", "formats", "uris"}, or None when CopyQ isn't running."""
        out = copyq("eval", "--", LIST_SCRIPT)
        if out is None:
            return None
        try:
            items = [json.loads(line) for line in out.splitlines() if line.strip()]
        except ValueError:
            return None
        return items or None  # an empty CopyQ (just started) knows less than our own list

    def paste_copy(self, m):
        which = m.group(2) and int(m.group(2)) or ORDINAL.get((m.group(1) or "previous").lower(), 2)
        count = copyq("count")
        if count is not None and count.strip().isdigit() and int(count) > 0:
            if int(count) < which:
                self.say("CopyQ only has %d thing%s you copied." % (int(count), "" if int(count) == 1 else "s"))
                return
            key = helpers.paste_key(self.say)
            if key and copyq("select", str(which - 1)) is not None:  # puts it on the clipboard, any kind
                helpers.typed_until = time.time() + 1.5  # our watcher shouldn't count it as a new copy
                desktop.keys(key)
            return
        if len(self.copies) < which:
            self.say("I only remember %d thing%s you copied." % (len(self.copies), "" if len(self.copies) == 1 else "s")
                     if self.copies else "I haven't seen you copy anything yet.")
            return
        text = self.copies[-which]
        type_into_window(self.say, text)
        self.copies.remove(text)
        self.copies.append(text)  # pasting it makes it the newest again, like copying it
        set_clipboard(text)

    def copy_history(self):
        items = self._copyq_items()
        labels = [label(i) for i in items] if items else list(self.copies)[::-1]
        if not labels:
            self.say("I haven't seen you copy anything yet.")
            return
        self.j.show({"title": "Clipboard history",
                     "text": "\n".join("%d. %s" % (i + 1, (t.strip()[:120] + ("…" if len(t.strip()) > 120 else ""))
                                                   .replace("\n", " ")) for i, t in enumerate(labels)),
                     "choices": [{"label": " ".join(t.split())[:40] or "(blank)", "do": "paste number %d" % (i + 1)}
                                 for i, t in enumerate(labels)]})
        self.say(["Here's what you copied, newest first. Say a number to paste it.",
                  "Your recent copies, newest first. Say a number to paste one."])
