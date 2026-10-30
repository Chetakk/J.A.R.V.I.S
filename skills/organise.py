"""Organising files: "make a folder called invoices in documents", "rename this file to final report",
"move resume.pdf to documents", "delete old.txt" (to the Trash), "tidy my downloads", "find duplicate files in
downloads". Everything goes through system/fileops.py: nothing is overwritten or deleted for good, and "undo that"
puts it all back. JARVIS asks first whenever it had to work out which file you meant, and before anything that
touches more than one file or puts something in the Trash."""
import collections
import hashlib
import os
import re

from skills import files
from skills.base import Skill
from skills.documents import sounds_like_file, sure
from skills.files import FOLDER_RE, HOME, KINDS, SKIP_DIRS
from skills.helpers import and_list
from system.fileops import Batch, FileError

# "Tidy my downloads": loose files go into these folders (the ones your Downloads already has).
SORT = [("Images", KINDS["image"] + (".bmp", ".tif", ".tiff")), ("Videos", KINDS["video"]),
        ("Music", KINDS["audio"]), ("Installers", (".deb", ".appimage", ".run", ".sh")),
        ("Archives", KINDS["zip"]), ("Models", (".pt", ".pth", ".onnx", ".engine", ".tflite", ".safetensors", ".bin")),
        ("Documents", KINDS["document"] + KINDS["spreadsheet"] + KINDS["presentation"] + (".epub",))]
# "Delete the milk todo", "remove the reading layout": other skills' things, not files.
OTHER_THINGS = re.compile(r"\b(?:reminder|to ?-?do|task|layout|alarm|timer|bookmark|tab|line|word|that|it)s?\b", re.I)


class Organise(Skill):
    group = "organising files"
    does = "Folders, renames, moves, the Trash, tidying, duplicates; all undoable"
    phrases = ("make a folder called <x> in documents", "rename <file> to <name>", "move <file> to <folder>",
               "delete <file> (to the Trash)", "tidy my downloads", "find duplicate files in downloads")
    gives = "files changed, logged"
    permission = "confirm"
    asks = "before the Trash, several files, or a file it had to work out"
    checks = "nothing overwritten or deleted for good; undo that puts it back"

    def patterns(self):
        return [
            (r"(?:make|create|add)(?: me)? (?:a )?(?:new )?folder (?:called |named )?[\"']?(.+?)[\"']?"
             r"(?: (?:in|on|inside|under) %s)?" % FOLDER_RE, self.mkdir),
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
        exact = how == "path" or (how == "name" and os.path.basename(paths[0]).lower()
                                  == re.sub(r"^(?:my|the) (?:file )?", "", said.lower()))
        return paths[0], exact

    def _done(self, batch, said):
        files.remember([s.get("dst") or s.get("path") for s in batch.steps])
        self.j.last_undo = lambda: self._undo(batch)
        self.j.chat("sys", "file changes logged in ~/.local/share/jarvis/file_changes.log")
        moved = [s for s in batch.steps if s["op"] != "mkdir"]
        self.say(said + " Say undo that to put %s back." % ("them" if len(moved) > 1 else "it"))

    def _undo(self, batch):
        failed = batch.undo()
        self.say("Put back." if not failed else "Mostly put back, but %d had changed since, so I left %s."
                 % (failed, "it" if failed == 1 else "them"))

    def mkdir(self, m):
        name, where, path = m.group(1).strip(), m.group(2), m.group(3)
        if "/" in name or name in (".", ".."):
            self.say("A folder name can't have a slash in it.")
            return
        parent = files._folder(where, path) if (where or path) else os.path.join(HOME, "Documents")
        target = os.path.join(parent, name)
        if os.path.isdir(target):
            self.say("There's already a %s folder in %s." % (name, _where(parent)))
            return
        batch = Batch("make folder " + target)
        batch.mkdir(target)
        self._done(batch, "Made %s in %s." % (name, _where(parent)))

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
            self.say("Okay, left it.")
            return
        batch = Batch("rename")
        try:
            dst = batch.move(path, os.path.join(os.path.dirname(path), new))
        except (FileError, OSError) as e:
            self.say("I couldn't rename it: %s." % e)
            return
        self._done(batch, "Renamed it to %s." % os.path.basename(dst))

    def move(self, m):
        got = self._file(m.group(1))
        if not got:
            return got
        path, exact = got
        dest = self._folder(m.group(2), near=os.path.dirname(path))
        if not dest:
            return
        if os.path.dirname(path) == dest:
            self.say("It's already in %s." % _where(dest))
            return
        if not exact and not self.j.ask("Move %s to %s?" % (os.path.basename(path), _where(dest))):
            self.say("Okay, left it.")
            return
        batch = Batch("move")
        if not os.path.isdir(dest):
            batch.mkdir(dest)
        try:
            dst = batch.move(path, os.path.join(dest, os.path.basename(path)))
        except (FileError, OSError) as e:
            batch.undo()
            self.say("I couldn't move it: %s." % e)
            return
        self._done(batch, "Moved %s to %s." % (files._short(dst), _where(dest)))

    def _folder(self, said, near):
        """The folder `said` means: one of yours, a path, a new one, or one found by name. None: said why."""
        said = re.sub(r"^(?:my |the )", "", said.strip(), flags=re.IGNORECASE)
        m = re.match(r"new folder (?:called|named) (.+)", said, re.IGNORECASE)
        if m:
            return os.path.join(near, m.group(1).strip())
        m = re.fullmatch(FOLDER_RE, said, re.IGNORECASE)
        if m:
            return files._folder(m.group(1), m.group(2))
        found = _find_dirs(said)
        if len(found) == 1:
            return found[0]
        if found:
            pick = self.j.choose("There's %s. Which one?" % and_list(_where(f) for f in found[:3]),
                                 [_where(f) for f in found[:3]])
            return next((f for f in found if _where(f) == pick), None)
        if self.j.ask("There's no %s folder. Make one in %s?" % (said, _where(near))):
            return os.path.join(near, said)
        self.say("Okay, left it.")
        return None

    def trash(self, m):
        said = m.group(1) or m.group(2)
        if OTHER_THINGS.search(said) and not sure(said):
            return False  # other skills' things ("delete the milk todo")
        got = self._file(said)
        if not got:
            return got
        path = got[0]
        what = "the %s folder and everything in it" % os.path.basename(path) if os.path.isdir(path) else \
            os.path.basename(path)
        if not self.j.ask("Put %s in the Trash?" % what):
            self.say("Okay, kept it.")
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
                     "text": "\n".join("- **%s/** ← %s" % (box, ", ".join(os.path.basename(p) for p in paths[:8])
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
        self.j.show({"title": "Duplicates in %s" % _where(folder),
                     "text": "\n".join("- **%s** (keep) · copies: %s" % (
                         g[0].replace(folder + "/", ""), ", ".join(p.replace(folder + "/", "") for p in g[1:]))
                                       for g in groups[:20]) + "\n\n%d extra copies, %s." % (len(extra),
                                                                                         _size(wasted))})
        if not self.j.ask("%d file%s %s copies, taking %s. Put the extra copies in the Trash? I'll keep one of each."
                          % (len(groups), "" if len(groups) == 1 else "s", "has" if len(groups) == 1 else "have",
                             _size(wasted))):
            self.say("Okay, left them.")
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


def _find_dirs(name, depth=4):
    """Folders under your home folder called `name` (any case), shallowest first."""
    want, out = name.lower(), []
    for root, dirs, _ in os.walk(HOME):
        if root.count(os.sep) - HOME.count(os.sep) >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        out += [os.path.join(root, d) for d in dirs if d.lower() == want]
    return sorted(out, key=lambda p: p.count(os.sep))


def _where(path):
    rel = os.path.relpath(path, HOME)
    return "your home folder" if rel == "." else rel.replace("/", ", ") if not rel.startswith("..") else path


def _size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "bytes" else "%.1f %s") % (n, unit)
        n /= 1024.0
