"""Google Drive (the work account, system/google_account.py): "find the budget sheet in my drive", "search drive for
onboarding", "what did I edit yesterday", "read the design doc" / "summarise the launch plan doc" (Google Docs,
Sheets and Slides read as text; "summarise it" and "read the rest" carry on as for any long text).

A name that isn't in Drive falls through to your own files (skills/documents.py).

"What's been shared with me" lists files people shared with you lately, "open what Rahul shared" / "open the
latest shared file" opens one, and "read" / "summarise what Priya shared" reads a shared Doc, Sheet or Slides; and by itself, every 2 minutes, a newly
shared Doc, Sheet or file is said ("Rahul shared Budget 2026, a sheet, with you") with a card to open it (settings
google share_alerts: false turns it off). Shared links pasted into Chat or mail come with those messages instead.

"Download the second one" (or "save it to my documents") off the last Drive card saves it to Downloads, a Doc, Sheet
or Slides as .docx / .xlsx / .pptx, never over a file there; then the files skill takes it as "it"."""
import os
import re
import time

from skills.files import FRESH, remember
from skills.google_common import GoogleSkill, cell, clock, day_word, first_name, parse_time, person, rfc
from skills.helpers import HOME, NTH, ORDINALS
from system import fileops
from system import google_account as google

KINDS = {"document": "doc", "spreadsheet": "sheet", "presentation": "slides", "folder": "folder", "pdf": "PDF",
         "form": "form"}
EXPORT = {"application/vnd.google-apps.document": "text/plain", "application/vnd.google-apps.spreadsheet": "text/csv",
          "application/vnd.google-apps.presentation": "text/plain"}
# Google's own files download as Office files; the rest as they are.
OFFICE = {"application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml"
                                                   ".document", ".docx"),
          "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml"
                                                      ".sheet", ".xlsx"),
          "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument."
                                                       "presentationml.presentation", ".pptx"),
          "application/vnd.google-apps.drawing": ("application/pdf", ".pdf")}
FOLDER = r"(?: (?:to|into|in) (?:my |the )?(downloads|documents|desktop)(?: folder)?)?"
DOC = r"(?:doc|docs|document|sheet|spreadsheet|slides|deck|presentation|file)"
FIELDS = "files(id,name,mimeType,webViewLink,modifiedTime,modifiedByMeTime,owners(displayName))"
SHARED = "files(id,name,mimeType,webViewLink,modifiedTime,sharedWithMeTime,owners(displayName),sharingUser(displayName,emailAddress))"


class Drive(GoogleSkill):
    group = "drive"
    does = ("Google Drive: finding files, what you edited lately, reading or summing up a Doc, Sheet or Slides, what "
            "people shared with you, downloading one off a Drive card; says newly shared files")
    every = 120
    phrases = ("find <name> in my drive", "what did I edit yesterday", "read the <name> doc",
               "summarise the <name> doc", "what's been shared with me", "open what <name> shared",
               "open the latest shared file", "summarise what <name> shared", "download the second one")
    gives = "a card of files (open one with open the second link), or the text read aloud"
    permission = "auto"
    checks = "searches Drive itself; a name it can't find there goes on to your own files"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.found = []  # the files on the last card
        self.found_at = 0.0
        self._shared_since = self.last_seen()  # the newest sharedWithMeTime at the last poll (None until the first one)

    def patterns(self):
        return [
            (r"(?:find|search for|look for|look up|get|where(?:'s| is)) (?:the |my )?(.+?) (?:in|on|from) (?:my |the |"
             r"google )?drive|search (?:my |the |google )?drive for (?:the |my )?(.+)", self.find),
            (r"what (?:did|have) i (?:edit|edited|work on|worked on|change|changed|open|opened)(?: in (?:google )?drive|"
             r" in (?:my )?docs)? ?(yesterday|today|this week|lately|recently)?|(?:show |list )?(?:me )?(?:my )?"
             r"recent (?:google )?(?:docs|documents|drive files|files (?:in|on|from) (?:my |the |google )?drive|sheets)", self.recent),
            (r"what (?:docs|documents|sheets|google docs) (?:did|have) i (?:open|opened|edit|edited|work on|worked on|"
             r"change|changed) ?(yesterday|today|this week|lately|recently)?(?: in (?:google )?drive)?", self.recent),
            (r"what(?:'s| has| have| is) (?:been |people |anyone |someone )?shared with me(?: lately| recently| today| this "
             r"week)?|(?:show |list )?(?:me )?(?:the )?(?:files|docs|documents|sheets|things) (?:people |anyone )?shared "
             r"with me(?: lately| recently| today| this week)?|(?:did|has) (?:anyone|anybody|someone) shared? (?:anything|"
             r"a doc|a file|a sheet|a document)(?: with me)?(?: lately| recently| today)?|(?:show |list )?(?:me )?(?:my |the )?"
             r"(?:recent|recently|latest|new|newly) shared (?:files|docs|documents|sheets|things)|(?:what(?:'s| is)|(?:is there )?"
             r"anything) new (?:on|in) (?:my |the |google )?drive(?: lately| recently| today| this week)?", self.shared),
            (r"open (?:what|whatever|the (?:doc|file|sheet|document|thing|one)(?: that)?) (.+?) (?:just )?shared(?: with "
             r"me)?|open (?:the )?(?:last |latest |newest |most recent |new )?(?:doc |file |sheet |document |thing |one )?"
             r"(?:that was |that's been |that got |i got |was |just )?shared with me(?: by (.+))?|open (?:the )?(?:last|"
             r"latest|newest|most recent|new) shared (?:doc|file|sheet|document|spreadsheet|thing|one)(?: from (.+))?",
             self.open_shared),
            (r"(read|summari[sz]e|sum up)(?: me)? (?:what|whatever|the (?:doc|file|sheet|document|thing|one)(?: that)?) "
             r"(.+?) (?:just )?shared(?: with me)?|(read|summari[sz]e|sum up)(?: me)? (?:the )?(?:last |latest |newest )?"
             r"(?:shared )?(?:doc|file|sheet|document|thing) (?:that was |that's been )?shared with me", self.read_shared),
            (r"(read|summari[sz]e|sum up)(?: me)? (?:the |my |our )?(.+?) %s(?: (?:from|in|on) (?:my |google )?drive)?"
             % DOC, self.read),
            (r"(?:download|save|get) (?:the |number )?%s(?: one| file| doc| sheet| link| result)?%s" % (NTH, FOLDER),
             self.download),
            (r"(?:download|save) (it|that|this)(?: one| file| doc| sheet)?%s" % FOLDER, self.download),
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
        self._read_file(verb, files[0])

    def _read_file(self, verb, f):
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

    def open_shared(self, m):
        name = next((g for g in m.groups() if g), None)
        f = self._shared_pick(name)
        if not f:
            self.say("Nothing's been shared with you%s this month." % (" by %s" % name if name else ""))
            return
        self.open_url(f.get("webViewLink") or "https://drive.google.com/drive/shared-with-me",
                      reply="Opening %s, from %s." % (f["name"], _who(f)))

    def read_shared(self, m):
        g = m.groups()
        verb, name = (g[0] or g[2]).lower(), g[1]
        f = self._shared_pick(name)
        if not f:
            self.say("Nothing's been shared with you%s this month." % (" by %s" % name if name else ""))
            return
        if f["mimeType"] not in EXPORT:
            self.say("%s is a %s, and I can only read Docs, Sheets and Slides. Say open what %s shared." % (
                f["name"], _kind(f), _who(f)))
            return
        self._read_file(verb, f)

    def _shared_pick(self, name=None):
        """The newest file shared with you in the last month, by `name` ("Rahul") when given."""
        files = self._shared_lately(time.time() - 30 * 86400)
        if name:
            want = name.lower().strip()
            files = [f for f in files if want in ("%s %s" % ((f.get("sharingUser") or {}).get("displayName") or "", (
                f.get("sharingUser") or {}).get("emailAddress") or "")).lower()]
        return files[0] if files else None

    def shared(self, m):
        files = self._shared_lately(time.time() - 7 * 86400)
        if not files:
            self.say("Nobody's shared anything with you this week.")
            return
        self._card(files, "Shared with you")
        f = files[0]
        self.say("%s this week. The latest: %s shared %s, %s." % (
            "One file" if len(files) == 1 else "%d files" % len(files), _who(f), f["name"],
            day_word(parse_time(f["sharedWithMeTime"]))))

    def poll(self):
        if not self.cfg("share_alerts", True):
            return
        files = self._shared_lately(None)
        newest = max((f["sharedWithMeTime"] for f in files), key=parse_time, default=None)
        if self._shared_since is None:
            self._shared_since = newest or rfc(time.time())
            self.keep_seen(self._shared_since)
            return
        new = [f for f in files if parse_time(f["sharedWithMeTime"]) > parse_time(self._shared_since)]
        if not new:
            return
        self._shared_since = newest
        self.keep_seen(newest)
        for f in reversed(new[:3]):  # oldest first
            line = "%s shared %s, a %s, with you." % (_who(f), f["name"], _kind(f))
            self.j.skills.proactive.tell("share", line, "%s shared it on Google Drive (%s). Say what's been shared "
                                         "with me for the week's." % (_who(f), (f.get("sharingUser") or {}).get(
                                             "emailAddress") or "someone"), card={
                "id": "drive-share-" + f["id"], "title": "Shared with you",
                "text": "```rows\nfile:%s | [%s](%s) | %s | %s\n```" % (
                    _badge(f), cell(f["name"]), f.get("webViewLink") or "", cell(_who(f)), _kind(f)),
                "choices": [{"label": "Open it", "do": "open the first link"}]})

    def _shared_lately(self, since):
        drive = self.api("drive", "v3")
        files = google.run(drive.files().list(q="sharedWithMe and trashed=false", orderBy="sharedWithMeTime desc",
                                              pageSize=20, fields=SHARED)).get("files") or []
        return [f for f in files if f.get("sharedWithMeTime") and (since is None or
                                                                   parse_time(f["sharedWithMeTime"]) >= since)]

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

    def download(self, m):
        """"download the second one" from the last Drive card, into Downloads (or Documents / Desktop), never over a
        file there. Then it's "it" for the files skill: "rename it to budget", "move it to documents"."""
        if not self.found or time.time() - self.found_at > FRESH:
            return False  # no Drive card just now: someone else's "save the second one"
        word = m.group(1).lower()
        n = ORDINALS.get(word) or (int(word) if word.isdigit() else 1 if len(self.found) == 1 else 0)
        if not n or abs(n) > len(self.found) and n != -1:
            if not n:
                return False  # "download it" with several on the card: which one?
            self.say("There are only %d on the card." % len(self.found))
            return
        f = self.found[n - 1 if n > 0 else -1]
        if f["mimeType"] == "application/vnd.google-apps.folder" or (
                f["mimeType"].startswith("application/vnd.google-apps.") and f["mimeType"] not in OFFICE):
            self.say("%s is a %s, I can't download that. Say open link number %d." % (f["name"], _kind(f),
                                                                                       self.found.index(f) + 1))
            return
        drive = self.api("drive", "v3")
        mime, ext = OFFICE.get(f["mimeType"], (None, ""))
        # shortcut: the whole file in memory, fine for documents; stream with MediaIoBaseDownload if big files matter
        data = google.run(drive.files().export(fileId=f["id"], mimeType=mime) if mime else
                          drive.files().get_media(fileId=f["id"]))
        folder = os.path.join(HOME, {"documents": "Documents", "desktop": "Desktop"}.get(
            (m.group(2) or "").lower(), "Downloads"))
        os.makedirs(folder, exist_ok=True)
        name = os.path.basename(f["name"].replace("/", "-")).lstrip(".") or "drive file"
        path = fileops.free_name(os.path.join(folder, name if name.lower().endswith(ext) else name + ext))
        with open(path, "wb") as out:
            out.write(data if isinstance(data, bytes) else str(data or "").encode())
        remember([path])  # "open it", "rename it to ...", "move it to documents"
        shown = path.replace(HOME, "~")
        self.j.show({"id": "drive-saved", "title": "Saved to %s" % os.path.basename(folder), "text": "- %s" % shown,
                     "choices": [{"label": "Open it", "do": "open file %s" % shown}]})
        self.say(["Saved %s to %s." % (os.path.basename(path), os.path.basename(folder)),
                  "Done, %s is in %s." % (os.path.basename(path), os.path.basename(folder))])

    def _card(self, files, title):
        self.found, self.found_at = files, time.time()
        rows = []
        for f in files[:10]:
            at = parse_time(f["modifiedTime"]) if f.get("modifiedTime") else None
            owner = ((f.get("owners") or [{}])[0]).get("displayName")
            rows.append("file:%s | [%s](%s) | %s | %s" % (
                _badge(f), cell(f["name"]), f.get("webViewLink") or "", cell(" · ".join(x for x in (_kind(f), owner)
                                                                                         if x)),
                "" if at is None else clock(at) if day_word(at) == "today" else day_word(at)))
        self.j.show({"id": "drive", "title": title, "text": "```rows\n%s\n```" % "\n".join(rows),
                     "items": ["open link number %d" % i for i in range(1, len(rows) + 1)]})  # a link per row


def _badge(f):
    """The file-type badge: doc, sheet, slides, pdf, folder, else the name's own extension."""
    kind = _kind(f).lower()
    ext = os.path.splitext(f.get("name", ""))[1][1:].lower()
    return kind if kind != "file" else ext if ext.isalnum() and len(ext) <= 5 else "file"


def _kind(f):
    mime = f.get("mimeType", "")
    return next((v for k, v in KINDS.items() if k in mime), "file")


def _who(f):
    u = f.get("sharingUser") or {}
    return first_name(person(u.get("displayName"), u.get("emailAddress"))) if u else "Someone"
