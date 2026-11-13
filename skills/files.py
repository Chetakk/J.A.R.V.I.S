"""Files: "open my latest download", "find my resume", "what did I download today", "open the pdf I downloaded
this morning", "open file ~/Downloads/x.pdf", "find pdfs from last week", "what files did I change today",
"show the .py files in projects from yesterday", "open the projects folder", "open resume.pdf", "open my resume",
"open it", "where is the ai supervisor folder", "which folder is resume.pdf in", "how big is this file". "Find my tax papers" with no name matching looks by meaning
(the names of the files in your own folders, never what's in them).

It also reads out what's in a folder ("what folders do we have in projects", "what's in the temp folder"), and
keeps that list, so the next thing you say can point into it: "open the first folder", "open the second one in
vs code", "the last one". Finding something and then opening it where you want is the whole chain.

which() says what "this file", "it", "resume.pdf", "my latest download" or "the second one" means, for the other
file skills; place() what "the projects folder", "downloads", "jarvis" or "the first folder" means
("open the first folder in vs code")."""
import datetime
import os
import re
import shlex
import subprocess
import time
import urllib.parse

import numpy as np

from skills.base import Skill
from skills.helpers import HOME, NTH, ORDINALS, PROJECTS, and_list, file_badge, words
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
# "what's in the view menu" / "my clipboard history": asked the same way, but not a folder (other skills').
NOT_A_FOLDER = re.compile(r"\b(?:menu|history|clipboard|bookmarks|memory|notes|fridge)$", re.IGNORECASE)
THIS = (r"(?:this|that|the|it|this one|that one)(?: (?:file|document|doc|pdf|spreadsheet|sheet|presentation|slides|"
        r"deck|picture|image|photo|one))?")
PLACES = dict({f.lower(): os.path.join(HOME, f) for f in FOLDERS}, home=HOME, projects=PROJECTS)
FRESH = 10 * 60  # "it" is the file JARVIS last found or opened for this long
last = {"paths": [], "at": 0.0}  # files JARVIS last found, for "summarise it", "rename it"
listing = {"paths": [], "at": 0.0}  # what it last listed or opened, in order, for "the first folder"
opened = {"path": None, "at": 0.0}  # the folder JARVIS last opened, for "make a folder here"


class Files(Skill):
    group = "finding files"
    does = ("Downloads by when, the latest file, finding files by name or meaning, what's in a folder, and "
            "\"the first one\" of whatever it just listed")
    phrases = ("find my <file>", "what did I download today", "open the pdf I downloaded this morning",
               "open my latest download", "open the latest file in documents", "find pdfs from last week",
               "what files did I change today", "what folders are in <folder>", "what's in the <folder> folder",
               "open the first folder (one of the ones just listed)",
               "open the second one (one of the ones just listed)", "open resume.pdf", "open my resume",
               "where is the <folder> folder", "which folder is <file> in", "how big is <file>")
    gives = "speech, a card with choices, a file opened"
    permission = "auto"
    asks = "before opening a file it found by searching"
    checks = "the launch is watched for a window or an error"

    def patterns(self):
        return [
            (r"open (?:the )?file ((?:~|/).+)", self.open_path),
            # What's in a folder, before "open the X folder": "show me what's in the temp folder" lists it.
            (r"(?:(?:what|which|how many)|(?:list|show)(?: me)?(?: all)?(?: the)?) "
             r"(folders?|sub-?folders?|directories|files?|things|items?|stuff)"
             r"(?: (?:are|is|do we have|have we got|do i have|are there))?(?: there)? (?:in|inside|under|of) (.+)",
             self.list_in),
            (r"(?:what'?s|what is) (?:in|inside) (.+)|(?:list|show)(?: me)?(?: the)? contents of (.+)|"
             r"(?:show|list)(?: me)? what'?s (?:in|inside) (.+)", self.whats_in),
            # "open the second one" with no card on screen: one of the ones JARVIS just listed.
            (r"(?:open|show(?: me)?) (?:up )?(?:the |number )?%s(?: one| item| thing)?" % NTH, self.open_nth),
            # (before the meaning match, which takes "open the music folder" for the music player)
            (r"(?:open|show(?: me)?|bring up) (?:up )?(.+? (?:folder|directory))", self.open_folder),
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
            # "copy the last download and paste it here": the file itself, to paste in Files (or into a chat)
            (r"copy (?:the |my )?(?:latest|newest|last|most recent) (?:file|download)(?:ed file)?"
             r"(?: in (?:my )?(downloads|documents|desktop|pictures|videos))?( and paste it(?: in)?(?: here)?)?",
             self.copy_latest),
            # "open resume.pdf", "open my resume", "open it", "open ai supervisor" (just listed); not "... in vs code"
            (r"(?:open|show me|bring up) (?:up )?(?!.* (?:in|with|using|on|for) \S)(.+)", self.open_named),
            (r"(?:which|what) folder (?:is|are|has|holds?) (.+?)(?: in)?|where (?:is|are|'s) (.+?) (?:kept|saved|stored)",
             self.where_in),
            (r"(?:how big|how large|what size) (?:is|are) (.+)|(?:what'?s|what is) the size of (.+)|"
             r"when (?:did i|was|were) (.+?) (?:made|created|changed|edited|modified|saved|downloaded)|"
             r"when did i (?:make|create|change|edit|modify|save|download) (.+)", self.info),
            # (not "find X on github": that's a site search)
            (r"(?:find|locate|where'?s|where is|where are)(?: my| the)? (?!.+ on (?:github|maps|google maps|amazon|"
             r"youtube|wikipedia|google)$)(.+?)(?: file| files)?", self.find_file),
        ]

    def _target(self, said):
        """The file or folder `said` plainly names. False: it doesn't sound like one (hand it on); None: it does,
        but there's no such file (said so)."""
        said = said.strip()
        if not names_a_file(said):
            return False
        paths, _ = which(self.j, said)
        if paths:
            return paths[0]
        if re.fullmatch(THIS, re.sub(r"^(?:the|my) ", "", said, flags=re.IGNORECASE), re.IGNORECASE) \
                or not re.match(r"[~/]|.+\.\w{2,5}$", said):
            return False  # "open it" with nothing just found, "open the presentation": maybe another skill's
        self.say("I can't find %s." % re.sub(r"^(?:my|the) ", "", said, flags=re.IGNORECASE))
        return None

    def open_named(self, m):
        path = self._target(m.group(1))
        if path:
            self._open(path)
        return path

    def where_in(self, m):
        path = self._target(next(g for g in m.groups() if g))
        if not path:
            return path
        remember([path])
        self.say("%s is in %s." % (os.path.basename(path), _spoken_dir(os.path.dirname(path))))

    def info(self, m):
        path = self._target(next(g for g in m.groups() if g))
        if not path:
            return path
        remember([path])
        size, count = size_of(path)
        changed = _when(path)
        if os.path.isdir(path):
            self.say("%s has %d file%s, %s in all. Last changed %s." % (
                os.path.basename(path), count, "" if count == 1 else "s", size_words(size), changed))
        else:
            self.say("%s is %s, last changed %s." % (os.path.basename(path), size_words(size), changed))

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
        remember_listing(hits)
        title = ("Files you changed" if label == "changed" else label.capitalize()) + (" " + when if when else "")
        title += where
        self.j.show({"title": title,
                     "text": "\n".join("%d. %s**%s** in `%s` · %s" % (i, file_badge(h), os.path.basename(h),
                                                                    os.path.dirname(h).replace(HOME, "~"), _when(h))
                                       for i, h in enumerate(hits[:30], 1)) + ("\n\n…and %d more" % (len(hits) - 30)
                                                              if len(hits) > 30 else ""),
                     "choices": [{"label": os.path.basename(h)[:40], "do": "open file %s" % h.replace(HOME, "~")}
                                 for h in hits[:30]]})
        names = [_short(h) for h in hits[:3]]
        if len(hits) > 3:
            names.append("%d more" % (len(hits) - 3))
        self.say("%d files: %s. They're on the card." % (len(hits), and_list(names)) if len(hits) > 1
                 else "Just one: %s." % names[0])

    def _newest(self, folder):
        """The newest file in that folder of yours (Downloads if None), or None (said so)."""
        folder = os.path.join(HOME, (folder or "downloads").capitalize())
        files = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs if not f.startswith(".")]
        if not files:
            self.say("There's nothing in %s." % os.path.basename(folder))
            return None
        newest = max(files, key=os.path.getmtime)
        remember([newest])
        return newest

    def latest_file(self, m):
        newest = self._newest(m.group(1))
        if newest:
            self.j.executor.run("xdg-open %s" % shlex.quote(newest), reply="Opening %s." % os.path.basename(newest),
                                window=True)

    def copy_latest(self, m):
        from skills.helpers import paste_key, set_clipboard
        newest = self._newest(m.group(1))
        if not newest:
            return
        name = os.path.basename(newest)
        try:  # as a copied file: Files pastes the file itself, like after Ctrl+C on it
            set_clipboard("copy\n" + "file://" + urllib.parse.quote(newest), kind="x-special/gnome-copied-files")
        except (OSError, subprocess.SubprocessError) as e:
            self.say("I couldn't copy it: %s." % e)
            return
        key = m.group(2) and paste_key(self.say)
        if key:
            desktop.keys(key)
            self.say(["Pasted %s." % name,
                      "Pasted %s, right where you wanted it." % name, "Done, %s is pasted." % name])
        elif not m.group(2):
            self.say(["Copied %s. Say paste it, or press control V where you want it." % name,
                      "%s is on the clipboard. Say paste it, or hit control V wherever you want it." % name,
                      "%s is copied. Say paste it, or press control V where it should go." % name])

    def open_path(self, m):
        path = os.path.expanduser(m.group(1).strip())
        if not os.path.exists(path):
            self.say("That file isn't there any more.")
            return
        remember([path])
        self.j.executor.run("xdg-open %s" % shlex.quote(path), reply="Opening %s." % os.path.basename(path), window=True)

    def list_in(self, m):
        word = m.group(1).lower()
        kind = "folders" if word.startswith(("folder", "subfolder", "sub-folder", "director")) else \
            "files" if word.startswith("file") else None
        return self._contents(m.group(2), kind)

    def whats_in(self, m):
        said = next(g for g in m.groups() if g)
        if NOT_A_FOLDER.search(said):
            return False  # a window's menu, or the clipboard: another skill's
        return self._contents(said, None)

    def _contents(self, said, kind):
        """Read out what's in a folder, and keep the list: "open the first folder" then means one of these."""
        folder = place(self.j, said, folder=True)
        if not folder or not os.path.isdir(folder):
            return False  # not a folder here: maybe a menu, a website, or Claude knows
        items = _children(folder, kind)
        remember_listing(items)
        where = spoken_place(folder)
        if not items:
            self.say("There's nothing in %s." % where if kind is None
                     else "There aren't any %s in %s." % (kind, where))
            return
        dirs = [p for p in items if os.path.isdir(p)]
        what = kind or ("folders" if len(dirs) == len(items) else "things")
        self.j.show({"title": "In %s" % folder.replace(HOME, "~"),
                     "text": "\n".join("%d. %s**%s**%s" % (i, file_badge(p), os.path.basename(p),
                                                          "/" if os.path.isdir(p) else "")
                                       for i, p in enumerate(items[:30], 1))
                             + ("\n\n…and %d more" % (len(items) - 30) if len(items) > 30 else ""),
                     "choices": [{"label": os.path.basename(p)[:40], "do": "open file %s" % p.replace(HOME, "~")}
                                 for p in items[:30]]})
        names = [os.path.basename(p) for p in items[:3]]
        if len(items) > 3:
            names.append("%d more" % (len(items) - 3))
        if len(items) == 1:
            self.say("Just one %s in %s: %s." % (what.rstrip("s"), where, names[0]))
        else:
            self.say("%d %s in %s: %s. Say the first one, or its name, to open it."
                     % (len(items), what, where, and_list(names)))

    def open_nth(self, m):
        path = from_listing(m.group(1))
        if not path:
            return False  # nothing listed lately: a card choice, or not about files at all
        self._open(path)

    def _open(self, path):
        """Open a folder or file, and keep what's in it, so "the first one" can go a level deeper."""
        remember([path])
        if os.path.isdir(path):
            remember_listing(_children(path))
            opened["path"], opened["at"] = path, time.time()
        self.j.executor.run("xdg-open %s" % shlex.quote(path), reply="Opening %s." % spoken_place(path),
                            window=True)

    def open_folder(self, m):
        path = place(self.j, m.group(1), folder=True)
        if not path or self.j.registry.match_exact(m.group(0)):
            return False  # not a folder here, or a saved command for it ("open the downloads folder")
        self._open(path)  # "the first folder" now means one inside the folder you just opened

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
        remember_listing(hits)
        self.j.show({"title": "Downloaded %s" % when,
                     "text": "\n".join("%d. %s**%s** · %s" % (i, file_badge(h), os.path.basename(h), _when(h))
                                       for i, h in enumerate(hits, 1)),
                     "choices": [{"label": os.path.basename(h)[:40], "do": "open file %s" % h.replace(HOME, "~")}
                                 for h in hits[:30]]})

    def find_folder(self, query):
        """"Where is the ai supervisor folder": the one just listed, else ones by that name, else a project's."""
        name = re.sub(r"^(?:my|the) | (?:folder|directory)$", "", query.strip(), flags=re.IGNORECASE)
        listed = in_listing(name, folder=True)
        hits = [listed] if listed else find_dirs(name)
        if not hits:
            p = self.j.projects.find(name, strict=True)
            hits = [p.path] if p else []
        if not hits:
            return False  # not a folder of yours: maybe Claude knows
        remember(hits[:1])
        remember_listing(hits)
        if len(hits) > 1:
            self.j.show({"title": "Folders called %s" % name,
                         "text": "\n".join("%d. **%s** in `%s`" % (i, os.path.basename(h),
                                                                  os.path.dirname(h).replace(HOME, "~"))
                                           for i, h in enumerate(hits, 1)),
                         "choices": [{"label": _spoken_dir(os.path.dirname(h))[:40],
                                      "do": "open file %s" % h.replace(HOME, "~")} for h in hits]})
        where = and_list(_spoken_dir(os.path.dirname(h)) for h in hits[:3])
        if self.j.ask("%s is in %s. Want me to open it?" % (os.path.basename(hits[0]), where) if len(hits) == 1
                      else "There are %d folders called that, in %s. Open the first?" % (len(hits), where)):
            self._open(hits[0])

    def find_file(self, m):
        query = m.group(1)
        if re.search(r" (?:folder|directory)$", query, re.IGNORECASE):
            return self.find_folder(query)
        if not worth_searching(m) or self.j.browser.parse(m.group(0), guess=False):
            return False  # (and "find my resume in drive" is a search on Google Drive)
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
                     "text": "\n".join("- %s**%s** in `%s`" % (file_badge(h), os.path.basename(h),
                                                             os.path.dirname(h).replace(HOME, "~"))
                                       for h in hits)})
        where = _spoken_dir(os.path.dirname(best))
        remember_listing(hits)  # "open the second one" next
        if self.j.ask("%s %s%s in %s. Want me to open it?" % (
                "No file's called that, but the closest is" if meaning else "I found", os.path.basename(best),
                " and %d more" % (len(hits) - 1) if len(hits) > 1 else "", where)):
            self.j.executor.run("xdg-open %s" % shlex.quote(best), reply="Opening it.", window=True)


def worth_searching(m):
    """Only things that sound like files ("find my resume", "where's best.pt", "find the report file");
    "where is the Eiffel Tower" goes on to Claude without searching the disk."""
    if not re.search(r"\bmy\b|\bfiles?\b|\.\w{1,5}$", m.group(0), re.IGNORECASE):
        return False
    return not re.search(r"\b(on the )?(web|internet|google|youtube)\b", m.group(1), re.IGNORECASE)


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


def remember_listing(paths):
    """What JARVIS just listed or opened, in the order it read them out."""
    listing["paths"], listing["at"] = list(paths), time.time()


def recent_listing():
    """Those folders and files, while that's fresh and they're still there."""
    if time.time() - listing["at"] < FRESH:
        return [p for p in listing["paths"] if os.path.exists(p)]
    return []


def from_listing(said, folder=False):
    """"the first", "second one", "the last file" -> that one of the things JARVIS just listed or opened.
    None when `said` isn't an ordinal, nothing was listed lately, or the list is shorter than that."""
    m = re.fullmatch(r"(?:number )?%s(?: (one|item|thing|folder|directory|file|document))?" % NTH, said.strip(),
                     re.IGNORECASE)
    if not m:
        return None
    paths = recent_listing()
    if folder or (m.group(2) or "").lower() in ("folder", "directory"):  # "the third folder" counts folders only
        paths = [p for p in paths if os.path.isdir(p)]
    elif (m.group(2) or "").lower() in ("file", "document"):
        paths = [p for p in paths if not os.path.isdir(p)]
    word = m.group(1).lower()
    n = len(paths) if ORDINALS.get(word) == -1 else (ORDINALS.get(word) or int(word))
    return paths[n - 1] if 0 < n <= len(paths) else None


def _children(folder, kind=None):
    """What's in `folder`, folders first then files, each by name; no hidden ones. kind: "folders" or "files"."""
    try:
        paths = [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if not n.startswith(".")]
    except OSError:
        return []
    dirs = [p for p in paths if os.path.isdir(p)]
    files = [p for p in paths if not os.path.isdir(p)]
    return dirs if kind == "folders" else files if kind == "files" else dirs + files


def recent_folder():
    """The folder JARVIS last opened, while that's fresh and it's still there (else None)."""
    path = opened["path"]
    return path if path and time.time() - opened["at"] < FRESH and os.path.isdir(path) else None


def _plain(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def in_listing(said, folder=False):
    """One of the things JARVIS just listed, or in the folder it just opened, called `said` ("ai supervisor" is
    AI-Supervisor, "resume" is resume.pdf). No disk search beyond that one folder."""
    want = _plain(re.sub(r"^(?:the|my) |(?: (?:folder|directory|file))$", "", said.strip(), flags=re.IGNORECASE))
    if len(want) < 2:
        return None
    paths = recent_listing() + (_children(recent_folder()) if recent_folder() else [])
    for p in paths:
        name = os.path.basename(p)
        if (not folder or os.path.isdir(p)) and want in (_plain(name), _plain(os.path.splitext(name)[0])):
            return p
    return None


def names_a_file(said):
    """Whether `said` plainly names a file or folder, by its words and what JARVIS just listed (no disk search):
    a path, a name with its extension, "my resume", "the ai supervisor folder", "it" / "this file", "ai supervisor"
    just after opening its folder."""
    from skills.documents import DOC, EXTS
    said = said.strip()
    return bool(re.match(r"[~/]", said) or said.lower().endswith(EXTS) or re.fullmatch(THIS, said, re.IGNORECASE)
                or re.search(r" (?:folder|directory)$", said, re.IGNORECASE)
                or re.match(r"(?:my|the) (?:.+ )?%s$" % DOC, said, re.IGNORECASE) or in_listing(said))


def here(j, anywhere=False):
    """The folder "here" means: the one Files shows in front, else the one JARVIS just opened. anywhere: else
    also a terminal's or VS Code's folder. None if none."""
    from skills.screen import front_window
    win = front_window()
    if win and "nautilus" in win.wm_class.lower():
        path = _files_location(win)
        if path:
            return path
    return recent_folder() or (front_folder(j) if anywhere else None)


def find_dirs(name, depth=4):
    """Folders under your home folder called `name` (ignoring case, spaces and dashes), shallowest first."""
    want, out = _plain(name), []
    for root, dirs, _ in os.walk(HOME):
        if root.count(os.sep) - HOME.count(os.sep) >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        out += [os.path.join(root, d) for d in dirs if _plain(d) == want]
    return sorted(out, key=lambda p: p.count(os.sep))


def size_of(path, seconds=3):
    """(bytes, files) in a file or folder; a folder's count stops after `seconds` (then it's "at least")."""
    if not os.path.isdir(path):
        return os.path.getsize(path), 1
    total = count = 0
    deadline = time.time() + seconds
    for root, dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
                count += 1
            except OSError:
                pass
        if time.time() > deadline:
            break
    return total, count


def size_words(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "bytes" else "%.1f %s") % (n, unit)
        n /= 1024.0


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
    picked = from_listing(said) or in_listing(said)  # "the second one", "ai supervisor": a file or a folder
    if picked:
        return [picked], "listing"
    m = re.fullmatch(r"(.+?) (?:folder|directory)", said, re.IGNORECASE)
    if m:  # "the ai supervisor folder": only folders are looked for
        path = place(j, said, folder=True)
        return ([path], "folder") if path and os.path.isdir(path) else ([], None)
    m = re.fullmatch(r"(?:latest|newest|last|most recent) (?:download|downloaded file)|(?:(%s|file|thing|one) )?"
                     r"(?:that )?i (?:just )?downloaded(?: (%s))?" % (KIND_RE, WHEN), said, re.IGNORECASE)
    if m:
        hits = _downloads(m.group(2) or "ever", KIND_WORDS.get((m.group(1) or "").lower()))
        return (hits[:1], "download") if hits else ([], None)
    hits = _find_files(said)
    exact = [h for h in hits if os.path.basename(h).lower() == said.lower()]
    return (exact or hits[:1]), "name" if hits else None


def place(j, said, folder=False):
    """The folder or file `said` names: "the projects folder", "downloads", "~/src", "jarvis" (a project), "temp"
    (a folder in your home), "resume.pdf", "it". folder=True: only a folder. None when it's neither (a website,
    an app, a folder that isn't there)."""
    said = re.sub(r"^(?:the|my) ", "", said.strip().strip("\"'"), flags=re.IGNORECASE)
    m = re.fullmatch(r"(.+?) (?:folder|directory)", said, re.IGNORECASE)
    if m:
        said, folder = m.group(1), True
    if said.lower() in PLACES:
        return PLACES[said.lower()]
    if re.fullmatch(r"(?:this|that)(?: one)?", said, re.IGNORECASE) and folder:
        return front_folder(j)  # "open vs code in this folder": the one on your screen
    if said.lower() == "project":  # "the project folder": the one in the window you're in, else ~/Projects
        p = j.projects.focused()
        return p.path if p else PROJECTS
    picked = from_listing(said, folder) or in_listing(said, folder)
    if picked:
        return picked
    if said.startswith(("~", "/")):
        path = os.path.expanduser(said)
        return path if os.path.isdir(path) or os.path.exists(path) and not folder else None
    if re.fullmatch(THIS, said, re.IGNORECASE) and not folder:
        paths, _ = which(j, said)
        if paths:
            return paths[0]
    p = j.projects.find(said, strict=True)
    if p:
        return p.path
    if not folder:
        paths, _ = which(j, said)
        if paths:
            return paths[0]
    return _folder_named(said)


def _spoken_dir(path):
    """"Documents, invoices", "your home folder"."""
    rel = os.path.relpath(path, HOME)
    return "your home folder" if rel == "." else rel.replace("/", ", ") if not rel.startswith("..") else path


def spoken_place(path):
    """"your home folder", "the Projects folder", "resume.pdf"."""
    if path == HOME:
        return "your home folder"
    return "the %s folder" % os.path.basename(path) if os.path.isdir(path) else os.path.basename(path)


def _folder_named(said, depth=3, seconds=2):
    """A folder in your home (up to `depth` levels down) called `said`, ignoring case, spaces and dashes."""
    want = re.sub(r"[^a-z0-9]", "", said.lower())
    if len(want) < 3:
        return None
    deadline = time.time() + seconds
    for root, dirs, _ in os.walk(HOME):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for d in dirs:
            if re.sub(r"[^a-z0-9]", "", d.lower()) == want:
                return os.path.join(root, d)
        if root.count(os.sep) - HOME.count(os.sep) >= depth - 1 or time.time() > deadline:
            dirs[:] = []
    return None


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


def front_folder(j):
    """The folder in the window you're looking at: the one Files is showing, a terminal's (from its title,
    "user@host: ~/src"), or VS Code's project. Else a Files window on this workspace. None if none."""
    from skills.screen import front_window
    win = front_window()
    if win and "nautilus" not in win.wm_class.lower():
        if "code" in win.wm_class:
            p = j.projects.in_title(win)
            if p:
                return p.path
        m = re.search(r":\s*(~?/[^\s]*)\s*$", win.title or "")
        path = os.path.expanduser(m.group(1)) if m else None
        if path and os.path.isdir(path):
            return path
        ws = desktop.current_workspace()
        win = next((w for w in reversed(desktop.windows())
                    if "nautilus" in w.wm_class.lower() and w.workspace in (ws, None)), None)
    return _files_location(win) if win else None


def _files_location(win):
    """The folder Files is showing: its location bar copied (Ctrl+L, Ctrl+C), then your clipboard put back.
    Else the folder its title names."""
    from skills.helpers import clipboard, set_clipboard
    desktop.focus(win)
    old = clipboard()
    for wait in (0.2, 0.6):  # a busy computer can be slow to focus Files and fill its address bar: try once slower
        time.sleep(wait)
        set_clipboard("")
        desktop.keys("ctrl+l")
        time.sleep(wait)
        desktop.keys("ctrl+c")
        time.sleep(wait)
        desktop.keys("Escape")
        got = clipboard().strip()
        path = os.path.expanduser(urllib.parse.unquote(got[7:] if got.startswith("file://") else got))
        if got and os.path.isdir(path):
            set_clipboard(old)
            return path
    set_clipboard(old)
    title = (win.title or "").strip()
    if not title or title == "Home":
        return HOME if title else None
    # The title is only the folder's name, and look-alikes exist (~/Projects/AI-Process-Engineer and
    # ~/Downloads/AI process engineer): the one JARVIS just opened, else the only one with exactly that name.
    recent = recent_folder()
    if recent and os.path.basename(recent) == title:
        return recent
    exact = [d for d in find_dirs(title) if os.path.basename(d) == title]
    return exact[0] if len(exact) == 1 else None


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
