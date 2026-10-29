"""The desktop notifications that arrive while JARVIS runs, read off the session bus with dbus-monitor (both the
freedesktop Notify call most apps use, and GNOME's own org.gtk.Notifications). Kept in memory only, never written
to disk; the ones shown before JARVIS started can't be read. JARVIS's own notify-send lines are skipped."""
import os
import re
import select
import subprocess
import threading
import time
from collections import deque

MATCHES = ["type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
           "type='method_call',interface='org.gtk.Notifications',member='AddNotification'"]
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
        values = STRING.findall(args + "\n")
        if "member=Notify" in header and len(values) >= 4:
            self.add(values[0], values[2], values[3])
        elif "member=AddNotification" in header and values:
            fields = dict(zip(values[2::2], values[3::2]))  # app id, notification id, then key/value pairs
            self.add(values[0].rsplit(".", 1)[-1], fields.get("title", ""), fields.get("body", ""))

    def add(self, app, title, body):
        app, title, body = app.strip(), title.strip(), re.sub(r"<[^>]+>", "", body).strip()  # bodies may be markup
        if not (title or body) or (title == "JARVIS" and app in ("notify-send", "")) or "jarvis" in app.lower():
            return
        now = time.time()
        with self.lock:
            last = self.items[-1] if self.items else None
            if last and (last["app"], last["title"], last["body"]) == (app, title, body) and now - last["at"] < 3:
                return  # GNOME passes each one on once more
            self.items.append({"at": now, "app": app or "an app", "title": title, "body": body})

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
