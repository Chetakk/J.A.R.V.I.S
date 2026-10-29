""""paste the thing I copied before", "paste number 3", "clipboard history".
Kept in memory only, never saved."""
import collections
import time

from skills import helpers
from skills.base import Skill
from skills.helpers import set_clipboard, type_into_window

ORDINAL = {"last": 1, "previous": 2, "one before": 2, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
           "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}


class ClipboardHistory(Skill):
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

    def paste_copy(self, m):
        which = m.group(2) and int(m.group(2)) or ORDINAL.get((m.group(1) or "previous").lower(), 2)
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
        if not self.copies:
            self.say("I haven't seen you copy anything yet.")
            return
        items = list(self.copies)[::-1]
        self.j.show({"title": "Clipboard history",
                     "text": "\n".join("%d. %s" % (i + 1, (t.strip()[:120] + ("…" if len(t.strip()) > 120 else ""))
                                                   .replace("\n", " ")) for i, t in enumerate(items))})
        self.say("Here's what you copied, newest first. Say paste number 2 to use one.")
