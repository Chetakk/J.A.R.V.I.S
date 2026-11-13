"""The desktop notifications that arrive while JARVIS runs, read off the session bus with dbus-monitor (both the
freedesktop Notify call most apps use, and GNOME's own org.gtk.Notifications). Kept in memory only, never written
to disk; the ones shown before JARVIS started can't be read. JARVIS's own notify-send lines are skipped.

The phone's (GSConnect) come as the phone app, sender and text, with what's needed to answer or clear them on the
phone (item["phone"]); on_add(item) hears each new one as it comes (skills/phone.py speaks messages and calls)."""
import os
import re
import select
import subprocess
import threading
import time
from collections import deque

from system import calls

MATCHES = ["type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
           "type='method_call',interface='org.gtk.Notifications',member='AddNotification'",
           "type='method_call',interface='org.gtk.Notifications',member='RemoveNotification'"]
# A string value runs to the quote that ends its line before the next value (bodies can have newlines).
STRING = re.compile(r'^\s*(?:variant\s+)?string "(.*?)"$(?=\n(?: {3,}(?:\)|\]|(?:string|u?int\d+|byte|boolean|double|'
                    r'array|dict entry|variant|object path|signature)\b)|\Z))', re.M | re.S)
MESSAGE = re.compile(rb"^(?:method call|method return|signal|error) time=")


class Watcher:
    def __init__(self, keep=100):
        self.items = deque(maxlen=keep)  # {"at", "app", "title", "body"}, oldest first
        self.lock = threading.Lock()
        self.proc = None
        self.read_at = time.time()  # when you last heard them: newer ones are "new"
        self.on_add = None

    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def start(self):
        """Start (or restart, if it died) the dbus-monitor reader. Cheap to call often."""
        if self.running():
            return
        try:
            self.proc = subprocess.Popen(["dbus-monitor", "--session", *MATCHES], stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL)
        except OSError as e:
            print("[notifications] %r" % e)
            return
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        """Messages come whole; one is finished when the next starts or the pipe goes quiet for a moment."""
        fd, rest, block = proc.stdout.fileno(), b"", []
        while True:
            if not select.select([fd], [], [], 0.3)[0]:
                if block:
                    self._parse(b"".join(block).decode("utf-8", "replace"))
                    block = []
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            *lines, rest = (rest + chunk).split(b"\n")
            for line in lines:
                if MESSAGE.match(line) and block:
                    self._parse(b"".join(block).decode("utf-8", "replace"))
                    block = []
                block.append(line + b"\n")

    def _parse(self, text):
        header, _, args = text.partition("\n")
        values = STRING.findall(args.rstrip("\n") + "\n")  # (so a last value, as in RemoveNotification, ends at \Z)
        if "member=Notify" in header and len(values) >= 4:
            self.add(values[0], values[2], values[3])
        elif "member=AddNotification" in header and values:
            fields = dict(zip(values[2::2], values[3::2]))  # app id, notification id, then key/value pairs
            if values[0].endswith(".GSConnect") and len(values) > 1 and "|" in values[1]:
                call = values[1].split("|")
                if call[1:2] == ["talking"]:  # answered: JARVIS stays asleep until it's withdrawn
                    calls.phone_talking("|".join(call[2:]))
                self.add(*from_phone(values[1], fields.get("title", ""), fields.get("body", "")))
            else:
                self.add(values[0].rsplit(".", 1)[-1], fields.get("title", ""), fields.get("body", ""))
        elif "member=RemoveNotification" in header and len(values) > 1 and values[0].endswith(".GSConnect"):
            call = values[1].split("|")
            if call[1:2] == ["talking"]:  # the call ended
                calls.phone_ended("|".join(call[2:]))

    def add(self, app, title, body, phone=None):
        app, title, body = app.strip(), title.strip(), re.sub(r"<[^>]+>", "", body).strip()  # bodies may be markup
        if not (title or body) or (title == "JARVIS" and app in ("notify-send", "")) or "jarvis" in app.lower():
            return
        now = time.time()
        with self.lock:
            last = self.items[-1] if self.items else None
            if last and (last["app"], last["title"], last["body"]) == (app, title, body) and now - last["at"] < (30 if phone else 3):
                return  # GNOME passes each one on once more; the phone re-posts a message ~5 s later (shortcut: a real repeat within 30 s is dropped too)
            item = {"at": now, "app": app or "an app", "title": title, "body": body}
            if phone:
                item["phone"] = phone
            self.items.append(item)
        if self.on_add:
            self.on_add(item)

    def recent(self, since=0.0, app=None):
        with self.lock:
            return [n for n in self.items if n["at"] > since and (not app or app.lower() in n["app"].lower())]

    def seen(self):
        self.read_at = time.time()

    def clear(self):
        with self.lock:
            self.items.clear()
        self.seen()

    def stop(self):
        if self.running():
            self.proc.terminate()


def from_phone(nid, title, body):
    """(app, sender, text, phone) from a GSConnect notification: its id is "<device>|<Android key>[|<reply id>]",
    the Android key "0|<package>|<n>|<tag>|<uid>", or "<device>|ringing|<caller>" for a call. GSConnect shows
    the phone app as the title and "Sender: text" as the body, except for texts (sender as the title)."""
    parts = nid.split("|")
    if parts[1] in ("ringing", "talking"):
        return "Phone", title, body, {"call": parts[1]}
    phone = {"key": "|".join(parts[1:6]), "package": parts[2] if len(parts) > 2 else "",
             "reply": parts[6] if len(parts) > 6 else None}
    sender, sep, text = body.partition(": ")
    if "messaging" in phone["package"] or "mms" in phone["package"]:  # an SMS: GSConnect titles it with the sender
        return "Messages", title, body, phone
    if sep and sender != title:
        return title, sender, text, phone
    return title, "", body, phone
