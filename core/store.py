"""Your files JARVIS keeps (notes, memory, learned wordings, layouts, tasks): written whole or not at all, and a
file that can't be read is kept aside before anything new is saved over it."""
import json
import os
import shutil


def keep_damaged(path):
    """Copy a file that failed to parse to <path>.damaged-<time>, so saving afresh can't lose what was in it."""
    try:
        aside = "%s.damaged-%d" % (path, os.path.getmtime(path))
        if not os.path.exists(aside):
            shutil.copy2(path, aside)
            print("[store] %s couldn't be read; kept a copy as %s" % (path, aside))
    except OSError:
        pass


def load_json(path, default):
    """What's in `path`, or `default` when it's missing or damaged (a damaged one is kept aside first)."""
    try:
        with open(path) as f:
            return json.load(f)
    except OSError:
        return default
    except ValueError:
        keep_damaged(path)
        return default


def save_json(path, data, **dump):
    """Write `data` to a temp file and swap it in: a crash mid-write leaves the old file, never half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, **dump)
    os.replace(tmp, path)
