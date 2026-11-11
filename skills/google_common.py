"""What the Google skills (calendar, Gmail, Chat, Drive, the end-of-day wrap) share: Google's failures said
plainly, times and names said the way people say them, links opened, and a background poll that never blocks
jarvis.watch."""
import datetime
import functools
import re
import shlex
import threading
import time

from skills.base import Skill
from system import google_account as google
from system.google_account import GoogleError

ALIASES = {}  # "rahul" -> the address you picked for that name (skills/people.py keeps it in a pref)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class GoogleSkill(Skill):
    every = 60  # seconds between background polls (poll() in a thread of its own)

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self._polled = 0.0
        self._polling = False
        self.rules = [(p, self._guarded(fn)) for p, fn in self.rules]

    def _guarded(self, fn):
        """Google's failures said as they are ("Google isn't connected yet..."), not "Sorry, that didn't work"."""
        @functools.wraps(fn)
        def run(m):
            try:
                return fn(m)
            except GoogleError as e:
                self.fail("%s." % str(e).rstrip("."))
        return run

    def api(self, name, version, *needs):
        return google.api(name, version, *needs)

    def cfg(self, key, default):
        return (self.j.settings.get("google") or {}).get(key, default)

    def watch(self):
        """Every 10 s from jarvis.watch: poll() once a minute (`every`), on a thread, when signed in."""
        if self._polling or time.time() - self._polled < self.every:
            return
        self._polled = time.time()
        if not google.connected():
            return
        self._polling = True
        threading.Thread(target=self._poll_safely, daemon=True).start()

    def _poll_safely(self):
        try:
            self.poll()
        except GoogleError:
            pass  # offline, or signed out: the next poll tries again
        except Exception as e:
            print("[%s] poll failed: %r" % (type(self).__name__, e))
        finally:
            self._polling = False

    def poll(self):
        pass

    def open_url(self, url, reply="Opening it."):
        self.j.executor.run("xdg-open %s" % shlex.quote(url), reply=reply, window=True)


def parse_time(stamp):
    """Google's "2026-11-11T15:00:00+05:30" / "...Z" / "2026-11-11" (all day) -> a timestamp."""
    if not stamp:
        return None
    if len(stamp) == 10:
        return time.mktime(time.strptime(stamp, "%Y-%m-%d"))
    stamp = re.sub(r"\.\d+", "", stamp).replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(stamp).timestamp()


def rfc(ts):
    """A timestamp -> "2026-11-11T09:30:00Z" for Google's queries."""
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock(ts):
    """"3 pm", "10:30 am"."""
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0").replace(":00", "").lower()


def day_word(ts, now=None):
    """"today", "tomorrow", "Thursday", or "Thursday 26 November" more than six days off."""
    day = datetime.date.fromtimestamp(ts)
    today = datetime.date.fromtimestamp(now or time.time())
    if day == today:
        return "today"
    if day == today + datetime.timedelta(days=1):
        return "tomorrow"
    if day == today - datetime.timedelta(days=1):
        return "yesterday"
    if 0 < (day - today).days <= 6:
        return day.strftime("%A")
    return day.strftime("%A %-d %B")


def when(ts, now=None):
    """"today at 3 pm", "Thursday at 10:30 am"."""
    return "%s at %s" % (day_word(ts, now), clock(ts))


def person(name="", email=""):
    """A display name, or one made from the address ("rahul.kumar@x.com" -> "Rahul Kumar")."""
    if name and "@" not in name:
        return name.strip()
    local = (email or name or "someone").split("@")[0]
    return " ".join(w.capitalize() for w in re.split(r"[._-]+", local) if w) or "someone"


def first_name(name):
    return (name or "").split(" ")[0]


def sender(header):
    """A From header -> (name, address): '"Rahul K" <rahul@x.com>' -> ("Rahul K", "rahul@x.com")."""
    m = re.match(r'\s*"?([^"<]*?)"?\s*<([^>]+)>', header or "")
    if m:
        return person(m.group(1), m.group(2)), m.group(2).lower()
    addr = (header or "").strip()
    return person("", addr), addr.lower()


def cell(text):
    """Text safe inside a ```rows cell or a [link](...) label: no "|" (the cell divider), "]" or line breaks."""
    return " ".join((text or "").replace("|", "/").replace("]", ")").split())


def short(text, words=18):
    """The start of a message, to be said aloud."""
    text = " ".join((text or "").split())
    cut = text.split(" ")
    return text if len(cut) <= words else " ".join(cut[:words]) + "..."


def find_email(api_for, name):
    """The address of `name` ("Rahul"): Workspace directory, your contacts, then the From of mail they sent you.
    api_for(service, version) gives a client; None if nobody matches."""
    name = ALIASES.get(name.strip().lower(), name.strip())
    if "@" in name:
        return name
    try:
        found = google.run(api_for("people", "v1").people().searchDirectoryPeople(
            query=name, readMask="emailAddresses", sources=["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"], pageSize=1))
        for p in found.get("people") or []:
            for e in p.get("emailAddresses") or []:
                return e["value"]
    except GoogleError:
        pass  # no directory permission yet: the next way
    try:
        found = google.run(api_for("people", "v1").people().searchContacts(
            query=name, readMask="emailAddresses", pageSize=1))
        for r in found.get("results") or []:
            for e in r.get("person", {}).get("emailAddresses") or []:
                return e["value"]
    except GoogleError:
        pass
    gm = api_for("gmail", "v1")
    hits = google.run(gm.users().messages().list(userId="me", q='from:"%s"' % name, maxResults=1)).get("messages")
    if hits:
        msg = google.run(gm.users().messages().get(userId="me", id=hits[0]["id"], format="metadata",
                                                   metadataHeaders=["From"]))
        return sender(header(msg, "From"))[1]
    return None


def header(msg, name):
    return next((h["value"] for h in msg.get("payload", {}).get("headers") or [] if h["name"].lower() == name.lower()),
                "")
