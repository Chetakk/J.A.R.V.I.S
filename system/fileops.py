"""Changes to your files that can always be taken back: moving, renaming, new folders, and the Trash (never a
permanent delete). Nothing is ever overwritten: a name that's taken gets " (2)". Each change is written to
~/.local/share/jarvis/file_changes.log, and a batch of them can be undone in one go."""
import glob
import json
import os
import shutil
import subprocess
import time
from urllib.parse import quote, unquote

LOG = os.path.expanduser("~/.local/share/jarvis/file_changes.log")
TRASH = os.path.expanduser("~/.local/share/Trash")


class FileError(Exception):
    pass


class Batch:
    """The changes one request makes; undo() reverses them, newest first."""

    def __init__(self, what):
        self.what, self.steps = what, []

    def __bool__(self):
        return bool(self.steps)

    def _log(self, step):
        self.steps.append(step)
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a") as f:
                f.write(json.dumps(dict(step, at=time.strftime("%Y-%m-%d %H:%M:%S"), why=self.what)) + "\n")
        except OSError:
            pass

    def move(self, src, dst):
        """Move or rename `src` to `dst` (a free name next to it if `dst` is taken). Returns where it went."""
        if not os.path.lexists(src):
            raise FileError("%s isn't there any more" % os.path.basename(src))
        dst = free_name(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        self._log({"op": "move", "src": src, "dst": dst})
        return dst

    def mkdir(self, path):
        if os.path.isdir(path):
            return path
        os.makedirs(path)
        self._log({"op": "mkdir", "path": path})
        return path

    def trash(self, path):
        """Move `path` to the Trash (the desktop's own, so it shows in Files and can be restored from there)."""
        if not os.path.lexists(path):
            raise FileError("%s isn't there any more" % os.path.basename(path))
        before = time.time() - 1
        res = subprocess.run(["gio", "trash", path], capture_output=True, text=True, timeout=30)
        if res.returncode != 0 or os.path.lexists(path):
            raise FileError("it couldn't go to the Trash: %s" % (res.stderr.strip() or "unknown error"))
        self._log({"op": "trash", "path": path, "info": _trash_info(path, before)})

    def undo(self):
        """Reverse every change, newest first. Returns how many couldn't be (already changed since)."""
        failed = 0
        for step in reversed(self.steps):
            try:
                if step["op"] == "move":
                    if os.path.lexists(step["src"]) or not os.path.lexists(step["dst"]):
                        raise FileError("changed since")
                    shutil.move(step["dst"], step["src"])
                elif step["op"] == "mkdir":
                    os.rmdir(step["path"])  # only while it's empty
                elif step["op"] == "trash":
                    _restore(step)
            except (OSError, FileError):
                failed += 1
        self.steps = []
        return failed


def free_name(path):
    """`path`, or "name (2).ext", "name (3).ext"... when it's taken."""
    if not os.path.lexists(path):
        return path
    stem, ext = os.path.splitext(path)
    if os.path.isdir(path):
        stem, ext = path, ""
    n = 2
    while os.path.lexists("%s (%d)%s" % (stem, n, ext)):
        n += 1
    return "%s (%d)%s" % (stem, n, ext)


def _trash_info(path, since):
    """The .trashinfo file gio wrote for `path` (it names the copy in Trash/files)."""
    want = "Path=" + quote(os.path.abspath(path))
    for info in sorted(glob.glob(os.path.join(TRASH, "info", "*.trashinfo")), key=os.path.getmtime, reverse=True):
        if os.path.getmtime(info) < since:
            break
        try:
            with open(info) as f:
                if want in f.read().splitlines():
                    return info
        except OSError:
            pass
    return None


def _restore(step):
    info = step.get("info")
    if not info or not os.path.exists(info) or os.path.lexists(step["path"]):
        raise FileError("not in the Trash any more")
    with open(info) as f:
        path = next((unquote(l[5:]) for l in f.read().splitlines() if l.startswith("Path=")), step["path"])
    name = os.path.basename(info)[:-len(".trashinfo")]
    shutil.move(os.path.join(TRASH, "files", name), path)
    os.remove(info)
