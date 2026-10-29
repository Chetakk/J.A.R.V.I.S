"""Files: "open my latest download", "find my resume", "what did I download today", "open the pdf I downloaded
this morning", "open file ~/Downloads/x.pdf", "find pdfs from last week", "what files did I change today",
"show the .py files in projects from yesterday". "Find my tax papers" with no name matching looks by meaning
(the names of the files in your own folders, never what's in them).

which() says what "this file", "it", "resume.pdf" or "my latest download" means, for the other file skills."""
import datetime
import os
import re
import shlex
import time
import urllib.parse

import numpy as np

from skills.base import Skill
from skills.helpers import HOME, and_list, words
from system import desktop

WHEN = (r"today|this morning|this afternoon|this evening|tonight|earlier(?: today)?|yesterday(?: morning| afternoon|"
        r" evening)?|last night|this week|last week|this month|last month|recently|just now|"
        r"(?:in the )?(?:last|past) (?:\d+|two|three|few) days|"
        r"(?:an?|one|two|three|a few|a couple of|\d+) (?:minutes?|hours?|days?) ago")
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
EXT_WORDS = {"python": ".py", "markdown": ".md", "text": ".txt", "word": ".docx", "powerpoint": ".pptx",
             "javascript": ".js", "shell": ".sh", "yaml": ".yaml", "json": ".json", "log": ".log", "csv": ".csv"}
SKIP_DIRS = {"node_modules", "snap", "site-packages", "__pycache__", "venv", ".venv", "build", "dist", "go"}
FOLDERS = ("Documents", "Downloads", "Desktop", "Pictures", "Videos", "Music")  # yours, for meaning search
FOLDER_RE = (r"(?:(?:my |the )?(documents|downloads|desktop|pictures|videos|music|projects|home)(?: folder)?|"
             r"((?:~|/)\S+))")
THIS = (r"(?:this|that|the|it|this one|that one)(?: (?:file|document|doc|pdf|spreadsheet|sheet|presentation|slides|"
        r"deck|picture|image|photo|one))?")
FRESH = 10 * 60  # "it" is the file JARVIS last found or opened for this long
last = {"paths": [], "at": 0.0}  # files JARVIS last found, for "summarise it", "rename it"


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
            (r"(?:what|which) files? (?:did|have) i (?:change|changed|edit|edited|save|saved|modify|modified|work on|"
             r"touch|touched)(?: (%s))?(?: in %s)?|(?:show|list|find)(?: me)? (?:the |my )?files? i (?:changed|edited|"
             r"saved|modified|worked on)(?: (%s))?(?: in %s)?" % (WHEN, FOLDER_RE, WHEN, FOLDER_RE), self.changed),
            (r"(?:find|show|list|get)(?: me)?(?: all)?(?: my| the)? (?:(%s)s?|\.?(\w{1,10}) files)"
             r"(?: (?:from|modified|changed|edited|saved|made|created) (%s)| (%s))?(?: (?:in|from) %s)?"
             r"(?: (?:from|modified|changed|edited|saved|made|created) (%s)| (%s))?"
             % (KIND_RE, WHEN, WHEN, FOLDER_RE, WHEN, WHEN), self.by_kind),
            (r"open (?:the |my )?(?:latest|newest|last|most recent) (?:file|download)(?:ed file)?"
             r"(?: in (?:my )?(downloads|documents|desktop|pictures|videos))?", self.latest_file),
            # (not "find X on github": that's a site search)
            (r"(?:find|locate|where'?s|where is|where are)(?: my| the)? (?!.+ on (?:github|maps|google maps|amazon|"
             r"youtube|wikipedia|google)$)(.+?)(?: file| files)?", self.find_file),
        ]

    def changed(self, m):
        when = m.group(1) or m.group(4) or "today"
        folder = _folder(m.group(2) or m.group(5), m.group(3) or m.group(6))
        self._list(_search(folder, when), when, folder, "changed")

    def by_kind(self, m):
        g = m.groups()
        kind, ext = (g[0] or "").lower(), (g[1] or "").lower()
        if ext and ext not in EXT_WORDS and not re.match(r"^[a-z0-9]{1,5}$", ext) or ext in ("my", "the", "all"):
            return False
        exts = KINDS[KIND_WORDS[kind]] if kind else (EXT_WORDS.get(ext) or "." + ext,)
        when = next((w for w in (g[2], g[3], g[6], g[7]) if w), None)
        folder = _folder(g[4], g[5])
        label = "%ss" % KIND_WORDS[kind] if kind else "%s files" % exts[0]
        self._list(_search(folder, when or "ever", exts), when, folder, label)

    def _list(self, hits, when, folder, label):
        where = "" if folder == HOME else " in %s" % folder.replace(HOME, "~")
        if not hits:
            self.say("There aren't any %s%s%s." % ("files you %s" % label if label == "changed" else label,
                                                   " " + when if when else "", where))
            return
        remember(hits)
        title = ("Files you changed" if label == "changed" else label.capitalize()) + (" " + when if when else "")
        title += where
        self.j.show({"title": title,
                     "text": "\n".join("- **%s** in `%s` · %s" % (os.path.basename(h),
                                                                  os.path.dirname(h).replace(HOME, "~"), _when(h))
                                       for h in hits[:30]) + ("\n\n…and %d more" % (len(hits) - 30)
                                                              if len(hits) > 30 else ""),
                     "choices": [{"label": os.path.basename(h)[:40], "do": "open file %s" % h.replace(HOME, "~")}
                                 for h in hits[:6]]})
        names = [_short(h) for h in hits[:3]]
        if len(hits) > 3:
            names.append("%d more" % (len(hits) - 3))
        self.say("%d files: %s. They're on the card." % (len(hits), and_list(names)) if len(hits) > 1
                 else "Just one: %s." % names[0])

    def latest_file(self, m):
        folder = os.path.join(HOME, (m.group(1) or "downloads").capitalize())
        files = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs if not f.startswith(".")]
        if not files:
            self.say("There's nothing in %s." % os.path.basename(folder))
            return
        newest = max(files, key=os.path.getmtime)
        remember([newest])
        self.j.executor.run("xdg-open %s" % shlex.quote(newest), reply="Opening %s." % os.path.basename(newest),
                            window=True)

    def open_path(self, m):
        path = os.path.expanduser(m.group(1).strip())
        if not os.path.exists(path):
            self.say("That file isn't there any more.")
            return
        remember([path])
        self.j.executor.run("xdg-open %s" % shlex.quote(path), reply="Opening %s." % os.path.basename(path), window=True)

    def downloaded(self, m):
        kind = next((KIND_WORDS[g.lower()] for g in m.groups() if g and g.lower() in KIND_WORDS), None)
        when = next((g for g in m.groups() if g and g.lower() not in KIND_WORDS), None) or "today"
        hits = _downloads(when, kind)
        what = "%ss" % kind if kind else "anything"
        if not hits:
            self.say("You didn't download %s %s." % (what, when))
            return
        remember(hits)
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
            remember([best])
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
        meaning = not hits and self.j.intents and by_meaning(self.j.intents, query)
        if meaning:
            hits = meaning
        if not hits:
            return False  # not a file on disk: maybe the browser or Claude knows
        remember(hits[:1])
        best = hits[0]
        self.j.chat("sys", "found:\n" + "\n".join(h.replace(HOME, "~") for h in hits))
        self.j.show({"title": "Found %d file%s" % (len(hits), "" if len(hits) == 1 else "s"),
                     "text": "\n".join("- **%s** in `%s`" % (os.path.basename(h), os.path.dirname(h).replace(HOME, "~"))
                                       for h in hits)})
        where = os.path.relpath(os.path.dirname(best), HOME)
        where = "your home folder" if where == "." else where.replace("/", ", ")
        if self.j.ask("%s %s%s in %s. Want me to open it?" % (
                "No file's called that, but the closest is" if meaning else "I found", os.path.basename(best),
                " and %d more" % (len(hits) - 1) if len(hits) > 1 else "", where)):
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
    count = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "a few": 3, "few": 3, "a couple of": 2}
    m = re.match(r"(an?|one|two|three|a few|a couple of|\d+) (minute|hour|day)s? ago", when)
    if m:
        n = count.get(m.group(1)) or int(m.group(1))
        if m.group(2) == "day":
            day = midnight - n * 24 * hour
            return day, day + 24 * hour
        span = n * (60 if m.group(2) == "minute" else hour)
        return now - span * 2 - 600, now  # "an hour ago" is roughly, so look a bit wider
    m = re.match(r"(?:in the )?(?:last|past) (\d+|two|three|few) days", when)
    if m:
        return midnight - (count.get(m.group(1)) or int(m.group(1))) * 24 * hour, now
    yday = midnight - 24 * hour
    first = datetime.datetime.fromtimestamp(midnight).replace(day=1)
    last_month = (first - datetime.timedelta(days=1)).replace(day=1)
    return {
        "this morning": (midnight, midnight + 12.5 * hour), "this afternoon": (midnight + 12 * hour, midnight + 17.5 * hour),
        "this evening": (midnight + 17 * hour, now), "tonight": (midnight + 17 * hour, now),
        "yesterday": (yday, midnight), "yesterday morning": (yday, yday + 12.5 * hour),
        "yesterday afternoon": (yday + 12 * hour, yday + 17.5 * hour), "yesterday evening": (yday + 17 * hour, midnight),
        "last night": (yday + 18 * hour, midnight + 5 * hour), "this week": (now - 7 * 24 * hour, now),
        "last week": (now - 14 * 24 * hour, now - 7 * 24 * hour),
        "this month": (first.timestamp(), now), "last month": (last_month.timestamp(), first.timestamp()),
        "recently": (now - 3 * 24 * hour, now), "just now": (now - 15 * 60, now), "ever": (0, now),
    }.get(when, (midnight, now))


def _downloads(when, kind=None):
    """Files in ~/Downloads and its folders from that time, newest first. Modified time is when the download
    finished, and it stays put when files are sorted into folders (the change time doesn't)."""
    return _search(os.path.join(HOME, "Downloads"), when, KINDS[kind] if kind else None)


def _search(folder, when, exts=None, seconds=4):
    """Files under `folder` changed in that time (with one of `exts`), newest first."""
    start, end = _since(when)
    out, deadline = [], time.time() + seconds
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for f in files:
            if f.startswith(".") or f.endswith((".crdownload", ".part", ".tmp", ".swp", "~", ".pyc")):
                continue
            if exts and not f.lower().endswith(tuple(exts)):
                continue
            path = os.path.join(root, f)
            try:
                t = os.path.getmtime(path)
            except OSError:
                continue
            if start <= t <= end:
                out.append((t, path))
        if time.time() > deadline:
            break
    return [p for _, p in sorted(out, reverse=True)]


def _folder(name, path=None):
    if path:
        return os.path.expanduser(path)
    if not name or name.lower() == "home":
        return HOME
    return os.path.join(HOME, name.capitalize())


# --- which file you mean -----------------------------------------------------------------------------------

def remember(paths):
    last["paths"], last["at"] = list(paths), time.time()


def recent_file():
    """The file JARVIS last found or opened, while that's fresh."""
    if time.time() - last["at"] < FRESH:
        return [p for p in last["paths"] if os.path.exists(p)]
    return []


def which(j, said):
    """The files `said` means: "this file" / "it" (the file open in front, or the one just found), a path,
    "my latest download", "the pdf I downloaded today", or a name ("resume.pdf", "my resume").
    Returns (paths, how): how is "this", "path", "download" or "name"; ([], None) when there's no such file."""
    said = re.sub(r"^(?:the|my) ", "", said.strip().strip("\"'"), flags=re.IGNORECASE)
    if re.fullmatch(THIS, said, re.IGNORECASE):
        # "it" / "that" is the file just talked about; "this" the one you're looking at.
        kind = KIND_WORDS.get(said.split()[-1].lower())  # "this pdf" isn't the .md file open in front
        fits = lambda paths: [p for p in paths if not kind or p.lower().endswith(KINDS[kind])]  # noqa: E731
        if said.lower().startswith(("it", "that")):
            found = fits(recent_file()) or fits(front_files(j))
        else:
            found = fits(front_files(j)) or fits(recent_file())
        return found, "this" if found else None
    if said.startswith(("~", "/")):
        path = os.path.expanduser(said)
        return ([path], "path") if os.path.exists(path) else ([], None)
    m = re.fullmatch(r"(?:latest|newest|last|most recent) (?:download|downloaded file)|(?:(%s|file|thing|one) )?"
                     r"(?:that )?i (?:just )?downloaded(?: (%s))?" % (KIND_RE, WHEN), said, re.IGNORECASE)
    if m:
        hits = _downloads(m.group(2) or "ever", KIND_WORDS.get((m.group(1) or "").lower()))
        return (hits[:1], "download") if hits else ([], None)
    hits = _find_files(said)
    exact = [h for h in hits if os.path.basename(h).lower() == said.lower()]
    return (exact or hits[:1]), "name" if hits else None


def front_files(j):
    """The file(s) in the window you're looking at: the selection in Files, or the document a viewer or editor
    has open (from its command line, else from your recently used files by the name in its title)."""
    from skills.screen import front_window  # (screen imports the helpers this module uses)
    win = front_window()
    if not win:
        return []
    if "nautilus" in win.wm_class.lower():
        return _files_selection(win)
    pid = desktop.pid(win)
    title = win.title or ""
    args = []
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            argv = [a.decode(errors="replace") for a in f.read().split(b"\0")[1:] if a]
        cwd = os.readlink("/proc/%d/cwd" % pid)
        for a in argv:
            a = urllib.parse.unquote(a[7:]) if a.startswith("file://") else a
            path = os.path.join(cwd, a)
            if not a.startswith("-") and os.path.isfile(path):
                args.append(os.path.abspath(path))
    except (OSError, TypeError):
        pass
    named = [a for a in args if os.path.basename(a) in title]
    if named or len(args) == 1:
        return named[:1] or args
    m = re.search(r"([^/\\:*?\"<>|—–]+\.[A-Za-z0-9]{2,5})\b", title)
    if not m:
        return []
    name = m.group(1).strip().lstrip("*● ")
    used = _recently_used(name)
    if used:
        return used[:1]
    hits = [h for h in _find_files(name) if os.path.basename(h) == name]
    return hits[:1]


def _files_selection(win):
    """What's selected in Files: copied (Ctrl+C puts the paths on the clipboard), then your clipboard put back."""
    from skills.helpers import clipboard, set_clipboard
    import subprocess
    desktop.focus(win)
    time.sleep(0.2)
    old = clipboard()
    set_clipboard("")
    desktop.keys("ctrl+c")
    time.sleep(0.3)
    got = clipboard()
    if "file://" not in got:
        try:
            got = subprocess.run(["xclip", "-o", "-selection", "clipboard", "-t", "x-special/gnome-copied-files"],
                                 capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            got = ""
    set_clipboard(old)
    return [urllib.parse.unquote(l[7:]) for l in got.splitlines() if l.startswith("file://")]


def _recently_used(name):
    """Paths in the desktop's recently-used list (~/.local/share/recently-used.xbel) with that file name."""
    try:
        with open(os.path.join(HOME, ".local/share/recently-used.xbel"), encoding="utf-8") as f:
            xbel = f.read()
    except OSError:
        return []
    out = []
    for href in reversed(re.findall(r'href="file://([^"]+)"', xbel)):
        path = urllib.parse.unquote(href)
        if os.path.basename(path) == name and os.path.exists(path) and path not in out:
            out.append(path)
    return out


# --- by meaning ---------------------------------------------------------------------------------------------

_names = {"key": None, "paths": [], "matrix": None}


def by_meaning(intents, query, limit=5, least=0.45):
    """Files in your own folders whose names mean what `query` says ("tax papers" -> ITR_2025.pdf), best first.
    Only names (with their folder), never contents. The names' vectors are kept until a file comes or goes.
    MiniLM alone is weak on file names ("cad drawings" is 0.40 from "CIPET CAD 29.pdf", "holiday photos" 0.41
    from a screenshot), so a name with one of the words in it scores a little more."""
    paths = []
    for top in FOLDERS:
        for root, dirs, files in os.walk(os.path.join(HOME, top)):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
            paths += [os.path.join(root, f) for f in files if not f.startswith(".")]
            if len(paths) > 3000:
                break
    if not paths:
        return []
    key = hash(tuple(paths))
    if _names["key"] != key:
        texts = ["%s (in %s)" % (_short(p), os.path.basename(os.path.dirname(p))) for p in paths]
        _names["matrix"] = np.vstack([intents.embed(texts[i:i + 64]) for i in range(0, len(texts), 64)])
        _names["key"], _names["paths"] = key, paths
    scores = _names["matrix"] @ intents.embed([query])[0]
    for w in (w for w in words(query) if len(w) >= 3):
        scores = scores + np.array([0.15 if w in os.path.basename(p).lower() else 0.0 for p in paths])
    best = np.argsort(-scores)[:limit]
    return [_names["paths"][i] for i in best if scores[i] >= least]


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
