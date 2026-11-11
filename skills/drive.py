"""Google Drive (the work account, system/google_account.py): "find the budget sheet in my drive", "search drive for
onboarding", "what did I edit yesterday", "read the design doc" / "summarise the launch plan doc" (Google Docs,
Sheets and Slides read as text; "summarise it" and "read the rest" carry on as for any long text).

A name that isn't in Drive falls through to your own files (skills/documents.py)."""
import re
import time

from skills.google_common import GoogleSkill, clock, day_word, parse_time, rfc
from system import google_account as google

KINDS = {"document": "doc", "spreadsheet": "sheet", "presentation": "slides", "folder": "folder", "pdf": "PDF",
         "form": "form"}
EXPORT = {"application/vnd.google-apps.document": "text/plain", "application/vnd.google-apps.spreadsheet": "text/csv",
          "application/vnd.google-apps.presentation": "text/plain"}
DOC = r"(?:doc|docs|document|sheet|spreadsheet|slides|deck|presentation|file)"
FIELDS = "files(id,name,mimeType,webViewLink,modifiedTime,modifiedByMeTime,owners(displayName))"


class Drive(GoogleSkill):
    group = "drive"
    does = "Google Drive: finding files, what you edited lately, reading or summing up a Doc, Sheet or Slides"
    phrases = ("find <name> in my drive", "what did I edit yesterday", "read the <name> doc",
               "summarise the <name> doc")
    gives = "a card of files (open one with open the second link), or the text read aloud"
    permission = "auto"
    checks = "searches Drive itself; a name it can't find there goes on to your own files"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.found = []  # the files on the last card

    def patterns(self):
        return [
            (r"(?:find|search for|look for|look up|get|where(?:'s| is)) (?:the |my )?(.+?) (?:in|on|from) (?:my |the |"
             r"google )?drive|search (?:my |the |google )?drive for (?:the |my )?(.+)", self.find),
            (r"what (?:did|have) i (?:edit|edited|work on|worked on|change|changed|open|opened)(?: in (?:google )?drive|"
             r" in (?:my )?docs)? ?(yesterday|today|this week|lately|recently)?|(?:show |list )?(?:me )?(?:my )?"
             r"recent (?:google )?(?:docs|documents|drive files|files in drive|sheets)", self.recent),
            (r"(read|summari[sz]e|sum up)(?: me)? (?:the |my |our )?(.+?) %s(?: (?:from|in|on) (?:my |google )?drive)?"
             % DOC, self.read),
        ]

    def find(self, m):
        if not google.connected():
            return False  # the Drive website's search, as before Google was connected
        name = next(g for g in m.groups() if g).strip()
        name = re.sub(r"\s+%s$" % DOC, "", name, flags=re.I) or name
        files = self.search(name)
        if not files:
            self.say("Nothing in your Drive called %s." % name)
            return
        self._card(files, "Drive: %s" % name)
        top = files[0]
        self.say("%s. The top one is %s, a %s%s." % (
            "Found one" if len(files) == 1 else "Found %d" % len(files), top["name"], _kind(top),
            ", changed %s" % day_word(parse_time(top["modifiedTime"])) if top.get("modifiedTime") else ""))

    def recent(self, m):
        said = (m.group(1) or "lately").lower()
        now = time.time()
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        start, end = {"today": (midnight, now), "yesterday": (midnight - 86400, midnight),
                      "this week": (midnight - time.localtime(now).tm_wday * 86400, now)}.get(said, (now - 7 * 86400,
                                                                                                    now))
        drive = self.api("drive", "v3")
        got = google.run(drive.files().list(q="trashed=false and modifiedTime > '%s'" % rfc(start),
                                            orderBy="modifiedByMeTime desc", pageSize=25, fields=FIELDS))
        files = [f for f in got.get("files") or [] if f.get("modifiedByMeTime")
                 and start <= parse_time(f["modifiedByMeTime"]) < end]
        if not files:
            self.say("You didn't change anything in Drive %s." % ("lately" if said in ("lately", "recently") else said))
            return
        self._card(files, "Edited %s" % said)
        self.say("%d file%s %s. The latest: %s." % (len(files), "" if len(files) == 1 else "s",
                                                    "yesterday" if said == "yesterday" else "lately" if said in (
                                                        "lately", "recently") else said, files[0]["name"]))

    def read(self, m):
        verb, name = m.group(1).lower(), m.group(2).strip()
        if not google.connected() or re.search(r"\.\w{2,4}$", name) or name.lower() in ("this", "that", "it", "a"):
            return False  # "summarise resume.pdf", "summarise this document": not a Drive file by name
        files = [f for f in self.search(name, text=False) if f["mimeType"] in EXPORT]
        if not files:
            return False  # not in Drive: your own documents (skills/documents.py)
        f = files[0]
        drive = self.api("drive", "v3")
        data = google.run(drive.files().export(fileId=f["id"], mimeType=EXPORT[f["mimeType"]]))
        text = (data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data or "")).strip()
        if not text:
            self.say("%s is empty." % f["name"])
            return
        reading = self.j.skills.reading
        if verb == "read":
            reading.long(text, title=f["name"])
            return
        reading.text, reading.parts, reading.at = text, [], time.time()  # "read the rest" / "summarise it" next
        reading.summarise()

    def search(self, name, text=True):
        """Files named like `name` (newest first), else (text=True) ones with it in their text."""
        drive = self.api("drive", "v3")
        safe = name.replace("\\", "").replace("'", "\\'")
        files = google.run(drive.files().list(q="trashed=false and name contains '%s'" % safe, pageSize=10,
                                              orderBy="modifiedTime desc", fields=FIELDS)).get("files") or []
        if not files and text:
            files = google.run(drive.files().list(q="trashed=false and fullText contains '%s'" % safe, pageSize=10,
                                                  fields=FIELDS)).get("files") or []
        return files

    def _card(self, files, title):
        self.found = files
        self.j.show({"id": "drive", "title": title, "text": "\n".join(
            "%d. [%s](%s) · %s%s" % (i, f["name"].replace("]", ")"), f.get("webViewLink") or "", _kind(f), (
                " · %s %s" % (day_word(parse_time(f["modifiedTime"])), clock(parse_time(f["modifiedTime"])))
                if f.get("modifiedTime") else "")) for i, f in enumerate(files[:10], 1))})


def _kind(f):
    mime = f.get("mimeType", "")
    return next((v for k, v in KINDS.items() if k in mime), "file")
