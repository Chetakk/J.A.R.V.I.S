"""JARVIS's mind map: an Obsidian vault (brain/ in the repo, gitignored and refused by the pre-commit hook) with one
note per person, project, meeting, chat group and app, linked with [[names]]. Open brain/ in Obsidian to see the graph.

JARVIS owns only its frontmatter keys and the lines between its two markers; anything you add above or below them,
or any frontmatter key of your own ("role: manager"), stays when JARVIS rewrites the note.
tools/brain_build.py fills it from Calendar, Gmail, Chat, ~/Projects and the day tracker."""
import os
import re

import numpy as np
import yaml

BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain")
START, END = "%% jarvis: start %%", "%% jarvis: end %%"  # Obsidian comments: hidden when reading, the lines between show
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
FILLER = re.compile(r"\b(?:the|my|a|an|that|this|thing|stuff|one|project|app)\b", re.I)
MADE = ".made"  # Obsidian skips dot files
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

    def write(self, kind, name, meta=None, lines=()):
        """Create or update brain/<kind>/<name>.md: JARVIS's keys merged into the frontmatter, its block replaced."""
        path = os.path.join(self.root, kind, title(name) + ".md")
        made = self._made()
        rel = os.path.relpath(path, self.root)
        if rel in made and not os.path.exists(path):
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

    def _made(self):
        """Every note JARVIS has ever written (brain/.made), so one you deleted stays deleted."""
        try:
            with open(os.path.join(self.root, MADE)) as f:
                return {line.rstrip("\n") for line in f if line.strip()}
        except OSError:
            return set()

    # --- reading ---

    def notes(self):
        out = []
        for kind in sorted(os.listdir(self.root)) if os.path.isdir(self.root) else []:
            folder = os.path.join(self.root, kind)
            if kind.startswith(".") or not os.path.isdir(folder):
                continue
            for f in sorted(os.listdir(folder)):
                if f.endswith(".md"):
                    path = os.path.join(folder, f)
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
        notes = self.notes()
        hit = self.note(text, notes)
        if hit or not self.embed or not notes:
            return hit
        want = self.embed([FILLER.sub(" ", text).strip() or text])[0]
        scored = sorted(((float((self._vector(n) @ want).max()), i) for i, n in enumerate(notes)), reverse=True)
        best, runner = scored[0], scored[1] if len(scored) > 1 else (0, None)
        # shortcut: one cutoff for every kind of note; tune on real questions once recall is wired in
        return notes[best[1]] if best[0] >= cutoff and best[0] - runner[0] >= 0.04 else None

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
    os.remove(m.note("supervisor").path)
    assert m.write("projects", "AI Supervisor", {}, ["back?"]) is None and not m.note("supervisor")
    print("ok")
