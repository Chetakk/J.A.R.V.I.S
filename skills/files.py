"""Files: "open my latest download", "find my resume"."""
import os
import re
import shlex
import time

from skills.base import Skill
from skills.helpers import HOME, words

SKIP_DIRS = {"node_modules", "snap", "site-packages", "__pycache__", "venv", ".venv", "build", "dist", "go"}


class Files(Skill):
    def patterns(self):
        return [
            (r"open (?:the |my )?(?:latest|newest|last|most recent) (?:file|download)(?:ed file)?"
             r"(?: in (?:my )?(downloads|documents|desktop|pictures|videos))?", self.latest_file),
            (r"(?:find|locate|where'?s|where is|where are)(?: my| the)? (.+?)(?: file| files)?", self.find_file),
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
