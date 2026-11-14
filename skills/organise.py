"""Organising files: "make a folder called invoices in documents", "rename this file to final report",
"move resume.pdf to documents", "copy it to documents", "duplicate this file", "move it here" (the folder Files
shows), "create ai supervisor" (right after opening a folder), "move all pdfs in downloads to documents", "delete old.txt" (to the Trash), "tidy my downloads", "find duplicate files in
downloads". Everything goes through system/fileops.py: nothing is overwritten or deleted for good, and "undo that"
puts it all back. JARVIS asks first whenever it had to work out which file you meant, and before anything that
touches more than one file or puts something in the Trash."""
import collections
import hashlib
import os
import re
import shutil

from skills import files
from skills.base import Skill
from skills.documents import sounds_like_file, sure
from skills.files import EXT_WORDS, FOLDER_RE, HOME, KIND_RE, KIND_WORDS, KINDS, PLACES, SKIP_DIRS, WHEN, find_dirs
from skills.helpers import and_list
from system.fileops import Batch, FileError

# "Tidy my downloads": loose files go into these folders (the ones your Downloads already has).
SORT = [("Images", KINDS["image"] + (".bmp", ".tif", ".tiff")), ("Videos", KINDS["video"]),
        ("Music", KINDS["audio"]), ("Installers", (".deb", ".appimage", ".run", ".sh")),
        ("Archives", KINDS["zip"]), ("Models", (".pt", ".pth", ".onnx", ".engine", ".tflite", ".safetensors", ".bin")),
        ("Documents", KINDS["document"] + KINDS["spreadsheet"] + KINDS["presentation"] + (".epub",))]
# "Delete the milk todo", "remove the reading layout": other skills' things, not files.
OTHER_THINGS = re.compile(r"\b(?:reminder|to ?-?do|task|layout|alarm|timer|bookmark|tab|line|word|that|it)s?\b", re.I)
# "create a playlist", "create a new note": other skills' things, not a folder here
NOT_A_NEW_FOLDER = re.compile(r"^(?:here|(?:a |new )*folder)$|\b(?:note|to ?-?do|reminder|playlist|tab|window|workspace|"
                              r"event|meeting|e?-?mail|message|file|document|doc|project|account|password|alarm|timer|"
                              r"list|layout|summary|report|backup|branch|commit|issue|pull request|it|that|this|"
                              r"me|you|copy|shortcut|link)s?\b", re.I)
NOT_EXT = {"old", "new", "big", "small", "these", "those", "my", "the", "all", "temp", "other", "extra", "empty",
           "large", "recent", "latest", "copied", "duplicate", "loose"}  # "delete the old files" isn't .old
MAIN = {os.path.normpath(p) for p in (HOME, *PLACES.values())}  # never moved, renamed or trashed whole


class Organise(Skill):
    group = "organising files"
    does = "Folders, renames, moves, copies, the Trash, many files at once, tidying, duplicates; all undoable"
    phrases = ("make a folder called <x> in documents", "rename <file> to <name>", "move <file> to <folder>",
               "delete <file> (to the Trash)", "tidy my downloads", "find duplicate files in downloads",
               "copy <file> to <folder>", "duplicate <file>", "move <file> here", "move all pdfs in downloads to documents")
    gives = "files changed, logged"
    permission = "confirm"
    asks = "before the Trash, several files, or a file it had to work out"
    checks = "nothing overwritten or deleted for good; undo that puts it back"

    def patterns(self):
        return [
            # "make a new folder in projects folder which is called ai supervisor"
            (r"(?:make|create|add)(?: me)? (?:a )?(?:new )?folder (?:in|on|inside|under) %s,? (?:which is |that's |"
             r"that is )?(?:called|named) [\"']?(.+?)[\"']?" % FOLDER_RE,
             lambda m: self._mkdir(m.group(3), m.group(1), m.group(2))),
            (r"(?:make|create|add)(?: me)? (?:a )?(?:new )?folder (?!(?:in |over )?here$)(?:called |named )?"
             r"[\"']?(.+?)[\"']?"
             r"(?: (?:in|on|inside|under) %s)?" % FOLDER_RE, self.mkdir),
            # "create ai supervisor" while looking at a folder: a new folder in it
            (r"create (?:a )?(?:new )?(?:folder |directory )?(?:called |named )?[\"']?(.+?)[\"']?(?: (?:in )?here)?",
             self.create_here),
            # "move all pdfs in downloads to documents", "delete the zips from downloads", "copy all images to pictures"
            (r"(move|copy|put|delete|trash|remove) (?:all )?(?:the |my )?(?:(%s)s?|\.?(\w{1,5}) files)(?: files)?"
             r"(?: (?:in|from) %s)?(?: (?:from )?(%s))?(?: (?:to|into|in) (?:the )?(.+?))?" % (KIND_RE, FOLDER_RE, WHEN),
             self.many),
            (r"(?:copy|duplicate|make a copy of) (.+?) (?:over )?(?:in )?here|copy (.+?) (?:in|into) this folder",
             lambda m: self.move(m, copy=True, to="here")),
            (r"(?:copy|duplicate|make a copy of) (.+?) (?:in|into|to) (?:a )?(?!(?:the |my )?clipboard$)"
             r"(new folder (?:called|named) .+|.+?)(?: folder)?", lambda m: self.move(m, copy=True)),
            (r"(?:duplicate|make a copy of) (.+)", lambda m: self.move(m, copy=True, to="beside")),
            (r"(?:move|put|bring) (.+?) (?:over )?(?:in )?here|(?:move|put) (.+?) (?:in|into) this folder",
             lambda m: self.move(m, to="here")),
            (r"rename (.+?) (?:to|as) [\"']?(.+?)[\"']?", self.rename),
            (r"(?:move|put|throw) (.+?) (?:in|into|to) the (?:trash|bin|recycle bin|rubbish(?: bin)?)|(?:delete|trash|"
             r"remove|throw away|get rid of) (.+?)(?: (?:in|to|into) the (?:trash|bin|rubbish))?", self.trash),
            # "move the terminal to the left" is a window (skills/windows.py), not a folder called "left"
            (r"(?:move|put) (.+?) (?:in|into|to) (?:a )?(?!(?:the )?(?:left|right)(?: side| half)?$)"
             r"(new folder (?:called|named) .+|.+?)(?: folder)?", self.move),
            (r"(?:tidy|tidy up|organi[sz]e|clean up|sort(?: out)?) (?:my |the )?(downloads|desktop)(?: folder)?",
             self.tidy),
            (r"(?:find|show|any|are there(?: any)?|check for|look for) (?:the )?duplicates?(?: files)?"
             r"(?: (?:in|on) %s)?|(?:find|show) (?:the )?duplicate(?: files)?(?: (?:in|on) %s)?"
             % (FOLDER_RE, FOLDER_RE), self.duplicates),
        ]

    # --- one file -------------------------------------------------------------------------------------------

    def _file(self, said, lenient=True):
        """(path, exact): exact when you named it outright, so it needn't be checked with you. False to hand on,
        None when it sounded like a file but there's none (said so)."""
        said = said.strip()
        if not sounds_like_file(said, strict=not lenient):
            return False
        paths, how = files.which(self.j, said)
        if not paths:
            if not sure(said):
                return False
            self.say("I can't find %s." % re.sub(r"^(?:my|the) ", "", said))
            return None
        if os.path.normpath(paths[0]) in MAIN:
            self.say("That's one of your main folders, so I'll leave it where it is.")
            return None
        # named outright, or picked from what JARVIS just read out ("the second one", "ai supervisor")
        exact = how in ("path", "listing") or (how == "name" and os.path.basename(paths[0]).lower()
                                              == re.sub(r"^(?:my|the) (?:file )?", "", said.lower()))
        return paths[0], exact

    def _done(self, batch, said):
        # (a folder made on the way isn't "it": "copy it to the desktop" then "move it to documents" moved Desktop)
        made = [s for s in batch.steps if s["op"] != "mkdir"] or batch.steps  # ("make a folder": that is "it")
        files.remember([s.get("dst") or s.get("path") for s in made])
        self.j.last_undo = lambda: self._undo(batch)
        self.j.chat("sys", "file changes logged in ~/.local/share/jarvis/file_changes.log")
        moved = [s for s in batch.steps if s["op"] != "mkdir"]
        self.say(said + " Say undo that to put %s back." % ("them" if len(moved) > 1 else "it"))

    def _undo(self, batch):
        failed = batch.undo()
        self.say("Put back." if not failed else "Mostly put back, but %d had changed since, so I left %s."
                 % (failed, "it" if failed == 1 else "them"))

    def mkdir(self, m):
        return self._mkdir(m.group(1), m.group(2), m.group(3))

    def _mkdir(self, name, where, path):
        name = name.strip()
        if "/" in name or name in (".", ".."):
            self.say("A folder name can't have a slash in it.")
            return
        # No place said: the folder Files shows, or JARVIS just opened ("open projects", "make a new folder
        # here"), else Documents
        parent = files._folder(where, path) if (where or path) else \
            files.here(self.j) or os.path.join(HOME, "Documents")
        target = os.path.join(parent, name)
        if os.path.isdir(target):
            self.say("There's already a %s folder in %s." % (name, _where(parent)))
            return
        batch = Batch("make folder " + target)
        batch.mkdir(target)
        self._done(batch, "Made %s in %s." % (name, _where(parent)))

    def create_here(self, m):
        """"create ai supervisor" right after "open projects", or with a Files window in front: a new folder."""
        name = m.group(1).strip()
        if NOT_A_NEW_FOLDER.search(name) or len(name.split()) > 5:
            return False  # a note, a playlist, a tab... (other skills'), or a whole sentence
        parent = files.here(self.j)
        if not parent:
            return False  # not looking at a folder
        return self._mkdir(name, None, parent)

    def many(self, m):
        """"move all pdfs in downloads to documents": every one of that kind in that folder (and its folders),
        shown on a card and asked about first."""
        verb, kind, ext, fname, fpath, when, dest = m.groups()
        verb, ext = verb.lower(), (ext or "").lower()
        if ext and (ext in NOT_EXT or not (ext in EXT_WORDS or re.fullmatch(r"[a-z0-9]{1,5}", ext))):
            return False
        if not (ext or kind.lower().endswith("s") or re.search(r"\ball\b", m.group(0), re.IGNORECASE)):
            return False  # "move the pdf to documents": one file, the one-file rules
        trash = verb in ("delete", "trash", "remove") or bool(dest and re.fullmatch(
            r"(?:trash|bin|recycle bin|rubbish(?: bin)?)", dest, re.IGNORECASE))
        if (not trash and not dest) or (verb in ("delete", "trash", "remove") and dest and not trash):
            return False
        exts = KINDS[KIND_WORDS[kind.lower()]] if kind else (EXT_WORDS.get(ext) or "." + ext,)
        what = "%ss" % KIND_WORDS[kind.lower()] if kind else "%s files" % exts[0]
        folder = files._folder(fname, fpath) if (fname or fpath) else files.here(self.j) or \
            os.path.join(HOME, "Downloads")
        target = None if trash else self._folder(dest, near=folder)
        if not trash and not target:
            return
        hits = [h for h in files._search(folder, when or "ever", exts)
                if not target or not (h + os.sep).startswith(target.rstrip(os.sep) + os.sep)]
        if not hits:
            self.say("There aren't any %s in %s%s." % (what, _where(folder), " " + when if when else ""))
            return
        self.j.show({"title": "%s %d %s" % ("Trash" if trash else verb.capitalize(), len(hits), what),
                     "text": "\n".join("- %s**%s** in `%s`" % (files.file_badge(h), os.path.basename(h),
                                                              os.path.dirname(h).replace(HOME, "~"))
                                       for h in hits[:30]) + ("\n\n…and %d more" % (len(hits) - 30)
                                                              if len(hits) > 30 else "")})
        n = "%d %s" % (len(hits), what) if len(hits) > 1 else os.path.basename(hits[0])
        copy = verb == "copy"
        question = "Put %s from %s in the Trash?" % (n, _where(folder)) if trash else "%s %s from %s to %s?" % (
            "Copy" if copy else "Move", n, _where(folder), _where(target))
        if not self.j.ask(question):
            self.say(["Okay, left them.",
                      "Fair enough, they're staying put.", "Alright, they stay where they are."])
            return
        batch = Batch("%s %s in %s" % (verb, what, folder))
        if target and not os.path.isdir(target):
            batch.mkdir(target)
        failed = 0
        for h in hits:
            try:
                if trash:
                    batch.trash(h)
                else:
                    (batch.copy if copy else batch.move)(h, os.path.join(target, os.path.basename(h)))
            except (FileError, OSError, shutil.Error):
                failed += 1
        done = len(hits) - failed
        self._done(batch, "%s %d.%s" % ("Put in the Trash:" if trash else "Copied" if copy else "Moved", done,
                                        " %d couldn't be." % failed if failed else ""))

    def rename(self, m):
        got = self._file(m.group(1))
        if not got:
            return got
        path, exact = got
        new = m.group(2).strip()
        if "/" in new:
            self.say("A name can't have a slash in it.")
            return
        ext = os.path.splitext(path)[1]
        if ext and not os.path.isdir(path) and not new.lower().endswith(ext.lower()):
            new += ext  # "rename it to final report" keeps .pdf
        if not exact and not self.j.ask("Rename %s to %s?" % (os.path.basename(path), new)):
            self.say(["Okay, left it.",
                      "It stays right where it is.", "Alright, it stays where it is."])
            return
        batch = Batch("rename")
        try:
            dst = batch.move(path, os.path.join(os.path.dirname(path), new))
        except (FileError, OSError) as e:
            self.say("I couldn't rename it: %s." % e)
            return
        self._done(batch, "Renamed it to %s." % os.path.basename(dst))

    def move(self, m, copy=False, to=None):
        """Move or copy one file or folder. to: "here" (the folder on screen), "beside" (a copy next to it), else
        the folder in group 2."""
        said = next(g for g in m.groups() if g)
        if " and " in said or (copy and OTHER_THINGS.search(said) and not files.names_a_file(said)):
            return False  # "copy the last download and paste it here", "duplicate this tab": other skills'
        got = self._file(said)
        if not got:
            return got
        path, exact = got
        if to == "beside":
            dest = os.path.dirname(path)
        elif to == "here":
            dest = files.here(self.j, anywhere=True)
            if not dest:
                self.say("I can't tell which folder you mean. Open it in Files and ask again.")
                return
        else:
            dest = self._folder(m.group(2), near=os.path.dirname(path))
        if not dest:
            return
        if not copy and os.path.dirname(path) == dest:
            self.say("It's already in %s." % _where(dest))
            return
        if os.path.isdir(path) and (dest + os.sep).startswith(path + os.sep):
            self.say("I can't put a folder inside itself.")
            return
        verb = "Copy" if copy else "Move"
        if not exact and not self.j.ask("%s %s to %s?" % (verb, os.path.basename(path), _where(dest))):
            self.say(["Okay, left it.",
                      "It stays right where it is.", "Alright, it stays where it is."])
            return
        batch = Batch(verb.lower())
        if not os.path.isdir(dest):
            batch.mkdir(dest)
        try:
            dst = (batch.copy if copy else batch.move)(path, os.path.join(dest, os.path.basename(path)))
        except (FileError, OSError, shutil.Error) as e:
            batch.undo()
            self.say("I couldn't %s it: %s." % (verb.lower(), e))
            return
        if to == "beside":
            self._done(batch, "Made a copy: %s." % os.path.basename(dst))
        else:
            self._done(batch, "%s %s to %s." % ("Copied" if copy else "Moved", files._short(dst), _where(dest)))

    def _folder(self, said, near):
        """The folder `said` means: here, one just listed, one of yours, a path, a new one, one found by name, or a
        project's. None: said why."""
        if re.fullmatch(r"(?:over )?here|there|this folder|that folder", said.strip(), re.IGNORECASE):
            path = files.here(self.j, anywhere=True)
            if not path:
                self.say("I can't tell which folder you mean. Open it in Files and ask again.")
            return path
        picked = files.from_listing(said, folder=True) or files.in_listing(said, folder=True)
        if picked:
            return picked
        said = re.sub(r"^(?:my |the )", "", said.strip(), flags=re.IGNORECASE)
        m = re.match(r"new folder (?:called|named) (.+)", said, re.IGNORECASE)
        if m:
            return os.path.join(near, m.group(1).strip())
        m = re.fullmatch(FOLDER_RE, said, re.IGNORECASE)
        if m:
            return files._folder(m.group(1), m.group(2))
        p = self.j.projects.find(said, strict=True)
        if p:
            return p.path
        found = find_dirs(said)
        if len(found) == 1:
            return found[0]
        if found:
            pick = self.j.choose("There's %s. Which one?" % and_list(_where(f) for f in found[:3]),
                                 [_where(f) for f in found[:3]])
            return next((f for f in found if _where(f) == pick), None)
        if self.j.ask("There's no %s folder. Make one in %s?" % (said, _where(near))):
            return os.path.join(near, said)
        self.say(["Okay, left it.",
                  "It stays right where it is.", "Alright, it stays where it is."])
        return None

    def trash(self, m):
        said = m.group(1) or m.group(2)
        if OTHER_THINGS.search(said) and not sure(said) and not (
                re.fullmatch(r"it|that", said.strip(), re.IGNORECASE) and files.recent_file()):
            return False  # other skills' things ("delete the milk todo"); "delete it" right after finding a file is it
        got = self._file(said)
        if not got:
            return got
        path = got[0]
        what = "the %s folder and everything in it" % os.path.basename(path) if os.path.isdir(path) else \
            os.path.basename(path)
        if not self.j.ask("Put %s in the Trash?" % what):
            self.say(["Okay, kept it.",
                      "Kept.", "Alright, it stays."])
            return
        batch = Batch("trash")
        try:
            batch.trash(path)
        except (FileError, OSError) as e:
            self.say("I couldn't: %s." % e)
            return
        self._done(batch, "It's in the Trash.")

    # --- many files -----------------------------------------------------------------------------------------

    def tidy(self, m):
        folder = os.path.join(HOME, m.group(1).capitalize())
        plan = collections.OrderedDict()
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if name.startswith(".") or not os.path.isfile(path) or name.endswith((".crdownload", ".part")):
                continue
            box = next((box for box, exts in SORT if name.lower().endswith(exts)), None)
            if box:
                plan.setdefault(box, []).append(path)
        if not plan:
            self.say("%s is already tidy: no loose files to sort." % m.group(1).capitalize())
            return
        count = sum(len(v) for v in plan.values())
        self.j.show({"title": "Tidy %s" % m.group(1).capitalize(),
                     "text": "```stack\nFiles: %s | files\n```\n\n" % ", ".join(
                         "%s %d" % (box, len(paths)) for box, paths in plan.items()) + "\n".join("- **%s/** ← %s" % (box, ", ".join(os.path.basename(p) for p in paths[:8])
                                                           + (" and %d more" % (len(paths) - 8) if len(paths) > 8
                                                              else "")) for box, paths in plan.items())})
        if not self.j.ask("Sort %d file%s into %s?" % (count, "" if count == 1 else "s",
                                                         and_list("%s (%d)" % (b, len(p)) for b, p in plan.items()))):
            self.say("Okay, left it as it is.")
            return
        batch = Batch("tidy " + folder)
        failed = 0
        for box, paths in plan.items():
            if not os.path.isdir(os.path.join(folder, box)):
                batch.mkdir(os.path.join(folder, box))
            for path in paths:
                try:
                    batch.move(path, os.path.join(folder, box, os.path.basename(path)))
                except (FileError, OSError):
                    failed += 1
        self._done(batch, "Sorted %d file%s%s." % (count - failed, "" if count - failed == 1 else "s",
                                                  ", %d couldn't be moved" % failed if failed else ""))

    def duplicates(self, m):
        name, path = (m.group(1) or m.group(3)), (m.group(2) or m.group(4))
        folder = files._folder(name, path) if (name or path) else os.path.join(HOME, "Downloads")
        self.say("Looking for copies in %s." % _where(folder))
        groups = find_duplicates(folder)
        if not groups:
            self.say("No duplicates in %s." % _where(folder))
            return
        extra = [p for g in groups for p in g[1:]]
        wasted = sum(os.path.getsize(p) for p in extra)
        kept = sum(os.path.getsize(g[0]) for g in groups)
        unit, div = next(((u, d) for u, d in (("GB", 2 ** 30), ("MB", 2 ** 20), ("KB", 2 ** 10))
                          if wasted + kept >= d), ("bytes", 1))
        self.j.show({"title": "Duplicates in %s" % _where(folder),
                     "text": "```stack\nThese files: extra copies %.1f warn, one of each %.1f | %s\n```\n\n" % (
                         wasted / div, kept / div, unit) + "\n".join("- **%s** (keep) · copies: %s" % (
                         g[0].replace(folder + "/", ""), ", ".join(p.replace(folder + "/", "") for p in g[1:]))
                                       for g in groups[:20]) + "\n\n%d extra copies, %s." % (len(extra),
                                                                                         _size(wasted))})
        if not self.j.ask("%d file%s %s copies, taking %s. Put the extra copies in the Trash? I'll keep one of each."
                          % (len(groups), "" if len(groups) == 1 else "s", "has" if len(groups) == 1 else "have",
                             _size(wasted))):
            self.say(["Okay, left them.",
                      "Fair enough, they're staying put.", "Alright, they stay where they are."])
            return
        batch = Batch("duplicates in " + folder)
        failed = 0
        for p in extra:
            try:
                batch.trash(p)
            except (FileError, OSError):
                failed += 1
        self._done(batch, "Put %d copies in the Trash%s." % (len(extra) - failed,
                                                              ", %d wouldn't go" % failed if failed else ""))


def find_duplicates(folder):
    """Groups of files with the same content under `folder` (same size, then the same SHA-1), the one to keep
    first: the one without "(1)" or "copy" in its name, else the oldest. Biggest waste first."""
    by_size = collections.defaultdict(list)
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for n in names:
            p = os.path.join(root, n)
            if n.startswith(".") or os.path.islink(p):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size:
                by_size[size].append(p)
    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_start = collections.defaultdict(list)  # the first 64 KB first: most same-size files differ there
        for p in paths:
            by_start[_hash(p, 1 << 16)].append(p)
        by_hash = collections.defaultdict(list)
        for start, maybe in by_start.items():
            for p in maybe if start and len(maybe) > 1 else ():
                by_hash[_hash(p) if size > 1 << 16 else start].append(p)
        for h, same in by_hash.items():
            if not h:
                continue
            if len(same) > 1:
                same.sort(key=lambda p: (bool(re.search(r"\(\d+\)|\bcopy\b", os.path.basename(p), re.I)),
                                         os.path.getmtime(p)))
                groups.append(same)
    return sorted(groups, key=lambda g: -os.path.getsize(g[0]) * (len(g) - 1))


def _hash(path, limit=None):
    """SHA-1 of the file (of its first `limit` bytes)."""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


_where = files._spoken_dir
_size = files.size_words
