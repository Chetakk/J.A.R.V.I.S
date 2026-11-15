"""JARVIS's mind map: an Obsidian vault (brain/ in the repo, gitignored and refused by the pre-commit hook) with one
note per person, project, meeting, chat group and app, linked with [[names]]. Open brain/ in Obsidian to see the graph.

JARVIS owns only its frontmatter keys and the lines between its two markers; anything you add above or below them,
or any frontmatter key of your own ("role: manager"), stays when JARVIS rewrites the note.
tools/brain_build.py fills it from Calendar, Gmail, Chat, ~/Projects and the day tracker.

brain/raw/<date>.md mirrors what you told JARVIS to remember (notes.json, kept by skills/notes.py), word for word and
never rewritten by any AI; private notes and secrets are left out. brain/log.md is a dated diary of what JARVIS
remembered, forgot and rebuilt."""
import json
import os
import re
import time

import numpy as np
import yaml

BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain")
START, END = "%% jarvis: start %%", "%% jarvis: end %%"  # Obsidian comments: hidden when reading, the lines between show
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
FILLER = re.compile(r"\b(?:the|my|a|an|that|this|thing|stuff|one|project|app)\b", re.I)
MADE = ".made"  # Obsidian skips dot files
RAW_SEEN = ".raw.json"  # the notes raw/ showed last time, to log what was added or forgotten
NOT_TOPICS = {"raw"}  # day files of things you said: not something a question is "about"
DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
BAD = re.compile(r'[\\/:*?"<>|#^\[\]]+')  # not allowed in a file name or a [[link]]


def title(name):
    """A note name safe as a file and inside [[ ]]."""
    return " ".join(BAD.sub(" ", name or "").split()).strip(" .") or "untitled"


def key(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


class Note:
    def __init__(self, path, kind, name, meta, body):
        self.path, self.kind, self.name, self.meta, self.body = path, kind, name, meta, body
        self.links = {m.group(1).strip() for m in LINK.finditer(body)}

    @property
    def names(self):
        return [self.name] + [str(a) for a in self.meta.get("aliases") or []]

    def __repr__(self):
        return "<Note %s/%s>" % (self.kind, self.name)


class Mind:
    def __init__(self, root=BRAIN, embed=None):
        self.root = os.path.abspath(root)
        self.embed = embed  # sentences -> vectors (core.intent.Embedder.embed); find() by meaning needs it
        self._vecs = {}  # path -> (mtime, vector)

    # --- writing ---

    def write(self, kind, name, meta=None, lines=(), folder=""):
        """Create or update brain/<kind>/<folder>/<name>.md: JARVIS's keys merged into the frontmatter, its block
        replaced. A note of that name elsewhere under <kind> is moved here first, with whatever you wrote in it."""
        base = title(name) + ".md"
        path = os.path.join(self.root, kind, *[title(f) for f in folder.split("/") if f], base)
        made = self._made()
        rel = os.path.relpath(path, self.root)
        if not os.path.exists(path):
            old = next((os.path.join(d, base) for d, _, files in os.walk(os.path.join(self.root, kind))
                        if base in files), None)
            if old:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.replace(old, path)
                made = (made - {os.path.relpath(old, self.root)}) | {rel}
                self._save_made(made)
                _prune(os.path.dirname(old), os.path.join(self.root, kind))
            elif any(m.split(os.sep)[0] == kind and os.path.basename(m) == base for m in made):
                return None  # you deleted it: JARVIS doesn't bring it back
        old_meta, before, after = {}, "", ""
        if os.path.exists(path):
            with open(path) as f:
                old_meta, text = _split(f.read())
            if START in text and END in text:
                before, rest = text.split(START, 1)
                after = rest.split(END, 1)[1]
            else:
                after = "\n" + text  # a note you wrote yourself: JARVIS's block goes on top
        meta = dict(old_meta, **{k: v for k, v in (meta or {}).items() if v not in (None, "", [])})
        meta.setdefault("type", kind.rstrip("s"))
        text = "---\n%s---\n%s%s\n%s\n%s%s" % (
            yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), before, START, "\n".join(lines), END,
            after if after.strip() else "\n")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
        if rel not in made:
            made.add(rel)
            with open(os.path.join(self.root, MADE), "a") as f:
                f.write(rel + "\n")
        return path

    # --- the diary and what you told JARVIS ---

    def log(self, text):
        """One dated line in brain/log.md."""
        path = os.path.join(self.root, "log.md")
        os.makedirs(self.root, exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a") as f:
            if new:
                f.write("# JARVIS's diary\n\nWhat JARVIS remembered, forgot and rebuilt, newest last.\n\n")
            f.write("- %s %s\n" % (time.strftime("%Y-%m-%d %H:%M"), text))

    def mirror(self, notes):
        """Rewrite brain/raw/ from notes.json ([{text, at, project?, private?}]): one file per day, your words as
        said, with known names linked. Logs what was added or forgotten since last time."""
        public = [n for n in notes if not n.get("private")]
        folder = os.path.join(self.root, "raw")
        link = linker({n.name: n.names[1:] for n in self.notes() if n.kind not in NOT_TOPICS | {"apps"}})
        days = {}
        for n in sorted(public, key=lambda n: n.get("at") or 0):
            line = link(n["text"])
            if n.get("project"):
                line = "%s: %s" % (link(n["project"]) if "[[" in link(n["project"]) else n["project"], line)
            days.setdefault(time.strftime("%Y-%m-%d", time.localtime(n.get("at") or 0)), []).append(
                "- %s %s" % (time.strftime("%H:%M", time.localtime(n.get("at") or 0)), line))
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):
            if f.endswith(".md") and f[:-3] not in days:
                os.remove(os.path.join(folder, f))  # every note of that day was forgotten
        for d, lines in days.items():
            text = "---\ntype: raw\n---\nWhat you told JARVIS to remember on %s, as you said it. Change it by voice " \
                   "(\"forget ...\"), not here: this file is rewritten from JARVIS's notes.\n\n%s\n" % (d, "\n".join(lines))
            path = os.path.join(folder, d + ".md")
            old = open(path).read() if os.path.exists(path) else None
            if old != text:
                with open(path + ".tmp", "w") as f:
                    f.write(text)
                os.replace(path + ".tmp", path)
        seen_path = os.path.join(self.root, RAW_SEEN)
        try:
            with open(seen_path) as f:
                seen = json.load(f)
        except (OSError, ValueError):
            seen = None
        now = [n["text"] for n in public]
        if seen is not None:
            for t in now:
                if t not in seen:
                    self.log("remembered: %s" % t)
            for t in seen:
                if t not in now:
                    self.log("forgot: %s" % t)
        with open(seen_path, "w") as f:
            json.dump(now, f)

    # --- answering from it ---

    def facts(self, note):
        """JARVIS's lines in a note, plain enough to say: links as their words, headings as "Mail to you:",
        "2026-11-03" as "3 Nov"."""
        block = note.body.split(START, 1)[1].split(END, 1)[0] if START in note.body else note.body
        out, heading = [], ""
        for line in block.splitlines():
            line = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", line)
            line = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line).replace("**", "").replace("*", "").strip()
            line = re.sub(r",? ?·? ?\[(?:open it|link|watch it)\]\([^)]*\)", "", line)  # bare links: nothing to say
            line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)  # [title](url) -> title
            if line.startswith("✓ done ·"):
                continue  # the legend of a playlist note
            line = DATE.sub(lambda m: time.strftime("%-d %b", time.strptime(m.group(0), "%Y-%m-%d")), line)
            if line.startswith("#"):
                heading = line.lstrip("# ").strip()
            elif line.startswith("- "):
                out.append("%s: %s" % (heading, line[2:]) if heading else line[2:])
            elif line:
                out.append(line)
        return out

    def mentioned(self, text, notes=None):
        """Notes whose name or alias is in `text`, whole words only ("when do I meet Rahul" -> Rahul's note)."""
        said = " %s " % " ".join(re.findall(r"[a-z0-9]+", text.lower()))
        out = []
        for n in notes or self.notes():
            if n.kind in NOT_TOPICS:
                continue
            for name in n.names:
                w = " ".join(re.findall(r"[a-z0-9]+", name.lower()))
                if len(w.replace(" ", "")) >= 4 and " %s " % w in said:
                    out.append(n)
                    break
        return out

    def forget(self, path):
        """Remove a note whose source is gone (a deleted Notepad tab), so the same name can come back later."""
        rel = os.path.relpath(path, self.root)
        if os.path.exists(path):
            os.remove(path)
        self._save_made(self._made() - {rel})
        _prune(os.path.dirname(path), self.root)

    def _save_made(self, made):
        with open(os.path.join(self.root, MADE), "w") as f:
            f.writelines(r + "\n" for r in sorted(made))

    def _made(self):
        """Every note JARVIS has ever written (brain/.made), so one you deleted stays deleted."""
        try:
            with open(os.path.join(self.root, MADE)) as f:
                return {line.rstrip("\n") for line in f if line.strip()}
        except OSError:
            return set()

    # --- reading ---

    def notes(self):
        """Every note; its kind is the top folder (people, projects, ...), whatever folders it sits in below that."""
        out = []
        for kind in sorted(os.listdir(self.root)) if os.path.isdir(self.root) else []:
            if kind.startswith(".") or not os.path.isdir(os.path.join(self.root, kind)):
                continue
            for d, dirs, files in sorted(os.walk(os.path.join(self.root, kind))):
                dirs[:] = [x for x in dirs if not x.startswith(".")]
                for f in sorted(files):
                    if f.endswith(".md"):
                        path = os.path.join(d, f)
                        with open(path) as fh:
                            meta, body = _split(fh.read())
                        out.append(Note(path, kind, f[:-3], meta, body))
        return out

    def note(self, name, notes=None):
        """The note called `name` or with it as an alias ("avtec hs", "Rahul Kumar"), or None."""
        k = key(name)
        return next((n for n in notes or self.notes() if k in {key(x) for x in n.names}), None) if k else None

    def find(self, text, cutoff=0.5):
        """The note `text` is about: its name or alias said exactly, else the closest by meaning (needs embed)."""
        notes = [n for n in self.notes() if n.kind not in NOT_TOPICS]
        hit = self.note(text, notes)
        if hit or not self.embed or not notes:
            return hit
        want = self.embed([FILLER.sub(" ", text).strip() or text])[0]
        scored = sorted(((float((self._vector(n) @ want).max()), i) for i, n in enumerate(notes)), reverse=True)
        best, runner = scored[0], scored[1] if len(scored) > 1 else (0, None)
        if best[0] < cutoff:
            return None
        if best[0] - runner[0] >= 0.04:
            return notes[best[1]]
        # a tie between a family and one of its own projects ("supervisor"): the family
        a, b = notes[best[1]], notes[runner[1]]
        for fam, member in ((a, b), (b, a)):
            if fam.meta.get("type") == "family" and key(fam.name) in {key(x) for x in member.links}:
                return fam
        return None  # shortcut: one cutoff for every kind of note; tune on real questions

    def around(self, name, notes=None):
        """The notes linked to or from `name`'s note, one step away."""
        notes = notes or self.notes()
        me = self.note(name, notes)
        if not me:
            return []
        mine = {key(x) for x in me.names}
        out = [self.note(link, notes) for link in me.links]
        out += [n for n in notes if any(key(link) in mine for link in n.links)]
        seen, uniq = {me.path}, []
        for n in out:
            if n and n.path not in seen:
                seen.add(n.path)
                uniq.append(n)
        return uniq

    def words(self):
        """Every name and alias JARVIS knows, by kind: what the ears should be able to hear."""
        out = {}
        for n in self.notes():
            out.setdefault(n.kind, set()).update(n.names)
        return out

    def _vector(self, note):
        mtime = os.path.getmtime(note.path)
        cached = self._vecs.get(note.path)
        if not cached or cached[0] != mtime:
            rows = note.names + ([str(note.meta["about"])] if note.meta.get("about") else [])
            cached = (mtime, self.embed(rows))  # each name and the summary on its own: the best one counts
            self._vecs[note.path] = cached
        return cached[1]


def _prune(folder, stop):
    """Remove folders left empty by a move, up to (not including) `stop`."""
    while os.path.abspath(folder).startswith(os.path.abspath(stop) + os.sep):
        try:
            os.rmdir(folder)
        except OSError:
            return
        folder = os.path.dirname(folder)


def _split(text):
    """(frontmatter dict, the rest) of a note."""
    m = FRONT.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), text[m.end():]


def linker(names):
    """A function that puts [[ ]] around known names in a line: {"AI Supervisor": ["ai supervisor", ...]}.
    Longest wording first, whole words only, case ignored; a name already inside [[ ]] is left alone."""
    pairs = sorted(((w, n) for n, ws in names.items() for w in set([n] + list(ws)) if len(key(w)) >= 4),
                   key=lambda p: -len(p[0]))
    if not pairs:
        return lambda line: line
    by_word = {w.lower(): n for w, n in pairs}
    rx = re.compile(r"\[\[[^\]]*\]\]|(?<![\w-])(%s)(?![\w-])" % "|".join(re.escape(w) for w, _ in pairs), re.I)

    def link(line):
        def one(m):
            if not m.group(1):
                return m.group(0)
            name = by_word[m.group(1).lower()]
            return "[[%s]]" % name if m.group(1).lower() == name.lower() else "[[%s|%s]]" % (name, m.group(1))
        return rx.sub(one, line)
    return link


if __name__ == "__main__":  # self-check on a throwaway vault
    import tempfile
    root = tempfile.mkdtemp()
    m = Mind(root)
    p = m.write("people", "Rahul: K", {"email": "r@x.com"}, ["Works on [[AI Supervisor]]"])
    with open(p, "a") as f:
        f.write("My own line.\n")
    m.write("people", "Rahul: K", {"email": "r2@x.com", "aliases": ["rahul"]}, ["Meets you on [[AI Supervisor]]"])
    text = open(p).read()
    assert "My own line." in text and "Meets you" in text and "Works on" not in text, text
    assert "email: r2@x.com" in text and text.count(START) == 1
    m.write("projects", "AI Supervisor", {"aliases": ["supervisor"]}, ["x"])
    assert m.note("supervisor").name == "AI Supervisor" and m.note("RAHUL").name == "Rahul K"
    assert [n.name for n in m.around("AI Supervisor")] == ["Rahul K"]
    link = linker({"AI Supervisor": ["ai supervisor", "supervisor"]})
    assert link("the Supervisor demo, [[AI Supervisor]] ok") == "the [[AI Supervisor|Supervisor]] demo, [[AI Supervisor]] ok"
    m.mirror([{"text": "Rahul K owes me the invoice", "at": 1794700000}, {"text": "pin", "at": 1794700001, "private": True}])
    m.mirror([{"text": "Rahul K owes me the invoice", "at": 1794700000}, {"text": "demo moved", "at": 1794700002}])
    raw = open(os.path.join(root, "raw", time.strftime("%Y-%m-%d", time.localtime(1794700000)) + ".md")).read()
    diary = open(os.path.join(root, "log.md")).read()
    assert "[[Rahul K]] owes me" in raw and "pin" not in raw and "demo moved" in raw, raw
    assert "remembered: demo moved" in diary and "pin" not in diary, diary
    assert m.facts(m.note("Rahul K")) == ["Meets you on AI Supervisor"], m.facts(m.note("Rahul K"))
    assert [n.name for n in m.mentioned("what's up with rahul k today")] == ["Rahul K"]
    with open(m.note("supervisor").path, "a") as f:
        f.write("mine\n")
    moved = m.write("projects", "AI Supervisor", {}, ["x"], folder="Biztel work/AI Supervisor")
    assert moved.endswith("projects/Biztel work/AI Supervisor/AI Supervisor.md") and "mine" in open(moved).read()
    assert len([n for n in m.notes() if n.name == "AI Supervisor"]) == 1 and m.note("supervisor").kind == "projects"
    m.write("projects", "AI Supervisor", {}, ["x"])  # back to the top: the empty folders go
    assert not os.path.exists(os.path.join(root, "projects", "Biztel work"))
    os.remove(m.note("supervisor").path)
    assert m.write("projects", "AI Supervisor", {}, ["back?"]) is None and not m.note("supervisor")
    print("ok")
