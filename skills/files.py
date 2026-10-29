"""Files: "open my latest download", "find my resume", "what did I download today", "open the pdf I downloaded
this morning", "open file ~/Downloads/x.pdf"."""
import datetime
import os
import re
import shlex
import time

from skills.base import Skill
from skills.helpers import HOME, and_list, words

WHEN = (r"today|this morning|this afternoon|this evening|tonight|earlier(?: today)?|yesterday(?: morning| afternoon|"
        r" evening)?|last night|this week|recently|just now|(?:an?|one|two|three|a few|a couple of|\d+) (?:minutes?|hours?) ago")
KINDS = {"pdf": (".pdf",), "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".heic"),
         "video": (".mp4", ".mkv", ".webm", ".mov", ".avi"), "audio": (".mp3", ".wav", ".ogg", ".flac", ".m4a"),
         "zip": (".zip", ".tar", ".gz", ".tgz", ".xz", ".7z", ".rar", ".deb"),
         "document": (".pdf", ".doc", ".docx", ".odt", ".txt", ".md", ".rtf"),
         "spreadsheet": (".xls", ".xlsx", ".ods", ".csv"), "presentation": (".ppt", ".pptx", ".odp")}
KIND_WORDS = {"pdf": "pdf", "pdfs": "pdf", "image": "image", "images": "image", "picture": "image", "pictures": "image",
              "photo": "image", "photos": "image", "screenshot": "image", "video": "video", "videos": "video",
              "song": "audio", "audio": "audio", "music": "audio", "zip": "zip", "archive": "zip", "installer": "zip",
              "document": "document", "documents": "document", "doc": "document", "docs": "document",
              "spreadsheet": "spreadsheet", "sheet": "spreadsheet", "excel": "spreadsheet",
              "presentation": "presentation", "slides": "presentation", "deck": "presentation"}
KIND_RE = "|".join(sorted(KIND_WORDS, key=len, reverse=True))
SKIP_DIRS = {"node_modules", "snap", "site-packages", "__pycache__", "venv", ".venv", "build", "dist", "go"}


class Files(Skill):
    def patterns(self):
        return [
            (r"open (?:the )?file ((?:~|/).+)", self.open_path),
            (r"(?:what|which files?|what files?|what stuff) (?:did|have) i (?:just )?download(?:ed)?(?: (%s))?|"
             r"(?:show|list|tell)(?: me)? (?:my |the )?(?:(%s) )?downloads(?: from)? (%s)|"
             r"(?:show|list|tell)(?: me)? (?:what|the files?) i downloaded(?: (%s))?" % (WHEN, KIND_RE, WHEN, WHEN),
             self.downloaded),
            (r"(open|find|show me|where'?s|where is|locate|get) (?:me )?(?:the |that |my )?(?:(%s|file|thing|one) )?"
             r"(?:that )?i (?:just )?downloaded(?: (%s))?" % (KIND_RE, WHEN), self.downloaded_one),
            (r"open (?:the |my )?(?:latest|newest|last|most recent) (?:file|download)(?:ed file)?"
             r"(?: in (?:my )?(downloads|documents|desktop|pictures|videos))?", self.latest_file),
            # (not "find X on github": that's a site search)
            (r"(?:find|locate|where'?s|where is|where are)(?: my| the)? (?!.+ on (?:github|maps|google maps|amazon|"
             r"youtube|wikipedia|google)$)(.+?)(?: file| files)?", self.find_file),
        ]

    def latest_file(self, m):
        folder = os.path.join(HOME, (m.group(1) or "downloads").capitalize())
        files = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs if not f.startswith(".")]
        if not files:
            self.say("There's nothing in %s." % os.path.basename(folder))
            return
        newest = max(files, key=os.path.getmtime)
        self.j.executor.run("xdg-open %s" % shlex.quote(newest), reply="Opening %s." % os.path.basename(newest),
                            window=True)

    def open_path(self, m):
        path = os.path.expanduser(m.group(1).strip())
        if not os.path.exists(path):
            self.say("That file isn't there any more.")
            return
        self.j.executor.run("xdg-open %s" % shlex.quote(path), reply="Opening %s." % os.path.basename(path), window=True)

    def downloaded(self, m):
        kind = next((KIND_WORDS[g.lower()] for g in m.groups() if g and g.lower() in KIND_WORDS), None)
        when = next((g for g in m.groups() if g and g.lower() not in KIND_WORDS), None) or "today"
        hits = _downloads(when, kind)
        what = "%ss" % kind if kind else "anything"
        if not hits:
            self.say("You didn't download %s %s." % (what, when))
            return
        self._show(hits, when)
        names = [_short(h) for h in hits[:3]]
        if len(hits) > 3:
            names.append("%d more" % (len(hits) - 3))
        self.say("%s you downloaded %s." % (when.capitalize(), and_list(names)))

    def downloaded_one(self, m):
        verb, kind, when = m.group(1).lower(), m.group(2), m.group(3)
        kind = KIND_WORDS.get((kind or "").lower())
        hits = _downloads(when or "ever", kind)
        if not hits:
            self.say("I don't see %s you downloaded%s." % ("a %s" % kind if kind else "anything", " " + when if when else ""))
            return
        if len(hits) == 1 or not when:
            best = hits[0]
            if verb == "open" or self.j.ask("It's %s, from %s. Open it?" % (_short(best), _when(best))):
                self.j.executor.run("xdg-open %s" % shlex.quote(best), reply="Opening %s." % _short(best), window=True)
            return
        self._show(hits, when)
        self.say("You downloaded %d %s %s. They're on the card; say the first one, or the second, to open it."
                 % (len(hits), "%ss" % kind if kind else "files", when))

    def _show(self, hits, when):
        self.j.show({"title": "Downloaded %s" % when,
                     "text": "\n".join("- **%s** · %s" % (os.path.basename(h), _when(h)) for h in hits),
                     "choices": [{"label": os.path.basename(h)[:40], "do": "open file %s" % h.replace(HOME, "~")}
                                 for h in hits[:6]]})

    def find_file(self, m):
        query = m.group(1)
        # Only things that sound like files ("find my resume", "where's best.pt", "find the report file");
        # "where is the Eiffel Tower" goes on to Claude without searching the disk.
        if not re.search(r"\bmy\b|\bfiles?\b|\.\w{1,5}$", m.group(0), re.IGNORECASE):
            return False
        if re.search(r"\b(on the )?(web|internet|google|youtube)\b", query, re.IGNORECASE):
            return False
        hits = _find_files(query)
        if not hits:
            return False  # not a file on disk: maybe the browser or Claude knows
        best = hits[0]
        self.j.chat("sys", "found:\n" + "\n".join(h.replace(HOME, "~") for h in hits))
        self.j.show({"title": "Found %d file%s" % (len(hits), "" if len(hits) == 1 else "s"),
                     "text": "\n".join("- **%s** in `%s`" % (os.path.basename(h), os.path.dirname(h).replace(HOME, "~"))
                                       for h in hits)})
        where = os.path.relpath(os.path.dirname(best), HOME)
        where = "your home folder" if where == "." else where.replace("/", ", ")
        if self.j.ask("I found %s%s in %s. Want me to open it?" % (
                os.path.basename(best), " and %d more" % (len(hits) - 1) if len(hits) > 1 else "", where)):
            self.j.executor.run("xdg-open %s" % shlex.quote(best), reply="Opening it.", window=True)


def _find_files(query, limit=5, seconds=4):
    """Files under your home folder whose names contain every word of `query` (newest first)."""
    wanted = words(query)
    if not wanted:
        return []
    hits, deadline = [], time.time() + seconds
    for root, dirs, files in os.walk(HOME):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for f in files:
            low = f.lower()
            if not f.startswith(".") and not low.endswith((".swp", "~", ".tmp")) and all(w in low for w in wanted):
                hits.append(os.path.join(root, f))
        if time.time() > deadline:
            break
    return sorted(hits, key=lambda p: -os.path.getmtime(p))[:limit]


def _since(when, now=None):
    """(start, end) epoch seconds for words like "this morning", "yesterday", "2 hours ago"."""
    now = now or time.time()
    when = (when or "today").lower()
    midnight = datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    hour = 3600
    m = re.match(r"(an?|one|two|three|a few|a couple of|\d+) (minute|hour)s? ago", when)
    if m:
        n = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "a few": 3, "a couple of": 2}.get(m.group(1)) \
            or int(m.group(1))
        span = n * (60 if m.group(2) == "minute" else hour)
        return now - span * 2 - 600, now  # "an hour ago" is roughly, so look a bit wider
    yday = midnight - 24 * hour
    return {
        "this morning": (midnight, midnight + 12.5 * hour), "this afternoon": (midnight + 12 * hour, midnight + 17.5 * hour),
        "this evening": (midnight + 17 * hour, now), "tonight": (midnight + 17 * hour, now),
        "yesterday": (yday, midnight), "yesterday morning": (yday, yday + 12.5 * hour),
        "yesterday afternoon": (yday + 12 * hour, yday + 17.5 * hour), "yesterday evening": (yday + 17 * hour, midnight),
        "last night": (yday + 18 * hour, midnight + 5 * hour), "this week": (now - 7 * 24 * hour, now),
        "recently": (now - 3 * 24 * hour, now), "just now": (now - 15 * 60, now), "ever": (0, now),
    }.get(when, (midnight, now))


def _downloads(when, kind=None):
    """Files in ~/Downloads and its folders from that time, newest first. Modified time is when the download
    finished, and it stays put when files are sorted into folders (the change time doesn't)."""
    start, end = _since(when)
    out = []
    for root, dirs, files in os.walk(os.path.join(HOME, "Downloads")):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith(".") or f.endswith((".crdownload", ".part", ".tmp")):
                continue
            if kind and not f.lower().endswith(KINDS[kind]):
                continue
            path = os.path.join(root, f)
            try:
                t = os.path.getmtime(path)
            except OSError:
                continue
            if start <= t <= end:
                out.append((t, path))
    return [p for _, p in sorted(out, reverse=True)]


def _short(path):
    """A file name to say: without its extension, and without long numbers and dashes."""
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"[_\-.]+", " ", name)
    name = re.sub(r"\s+", " ", re.sub(r"\b\d{5,}\b", "", name)).strip()
    return name[:60] or os.path.basename(path)


def _when(path):
    t = os.path.getmtime(path)
    day = datetime.date.fromtimestamp(t)
    today = datetime.date.today()
    label = "today" if day == today else "yesterday" if (today - day).days == 1 else day.strftime("%A %d %B")
    return "%s at %s" % (label, time.strftime("%H:%M", time.localtime(t)))
