"""Your projects in ~/Projects: what each one is (from its README), what you call it, and running it.

Running is `biztel run` (biztel-project-builder/cli): a terminal on JARVIS's workspace runs the project's
backend and frontend and prints their links. While it runs, its state file says which links it serves, so
JARVIS can tell you ("Avtec HS is up, on port 5173") and stop it again without asking biztel.

config/projects.yaml adds spoken names ("aliases") and leaves folders out ("skip").
"""
import difflib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time

import yaml

from core import desktop

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, "Projects")
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "projects.yaml")
RUNS = os.path.join(HOME, ".local/state/biztel/runs")
READMES = ("README.md", "CLAUDE.md", "CONTEXT.md", "README.txt", "README")
FILLER = re.compile(r"\b(?:the|my|project|station|app|repo|folder)\b", re.IGNORECASE)


def key(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


class Project:
    def __init__(self, name, path, title, summary, aliases):
        self.name, self.path, self.title, self.summary, self.aliases = name, path, title, summary, aliases

    @property
    def spoken(self):
        """How to say it back: "Avtec HS" (project.json), else the folder with spaces ("study budd")."""
        return self.aliases[0]

    def touched(self):
        """When you last worked on it: the newest file a level or two down (git, not node_modules)."""
        newest = 0
        for root, dirs, files in os.walk(self.path):
            depth = root[len(self.path):].count(os.sep)
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "dist", "__pycache__")
                       and depth < 2]
            for f in files:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
        try:
            newest = max(newest, os.path.getmtime(os.path.join(self.path, ".git", "index")))
        except OSError:
            pass
        return newest


class Projects:
    def __init__(self, root=PROJECTS):
        self.root = root
        self.items = []
        self.current = None  # the project you last named: "run it", "stop it", "its links"
        self.refresh()

    def refresh(self):
        try:
            with open(CONFIG) as f:
                cfg = yaml.safe_load(f) or {}
        except OSError:
            cfg = {}
        extra = {key(k): [str(a) for a in v or []] for k, v in (cfg.get("aliases") or {}).items()}
        skip = {key(s) for s in cfg.get("skip") or []}
        items = []
        for name in sorted(os.listdir(self.root) if os.path.isdir(self.root) else [], key=str.lower):
            path = os.path.join(self.root, name)
            if name.startswith(".") or not os.path.isdir(path) or key(name) in skip:
                continue
            title, summary = _readme(path)
            items.append(Project(name, path, title, summary, _aliases(name, path, title) + extra.get(key(name), [])))
        self.items = items
        return items

    def find(self, spoken, strict=False):
        """The project you mean by `spoken` ("avtec hs", "the upe stations", "jarvis"), or None.
        strict: only a near-certain match, for phrases that might not be about a project at all."""
        if not spoken:
            return None
        if re.match(r"^(?:it|this|that|this one|that one|the project)$", spoken.strip(), re.IGNORECASE):
            return self.current
        k = key(FILLER.sub(" ", spoken)) or key(spoken)
        names = {}
        for p in self.items:
            for a in [p.name] + p.aliases:
                names.setdefault(key(a), set()).add(p)
        hit = names.get(k)
        if hit and len(hit) == 1:
            return self._pick(next(iter(hit)))
        if not strict and len(k) >= 3:
            part = {p for n, ps in names.items() if n.startswith(k) for p in ps}
            if len(part) == 1:
                return self._pick(part.pop())
        close = difflib.get_close_matches(k, list(names), n=2, cutoff=0.88 if strict else 0.78)
        if close and len(names[close[0]]) == 1:
            return self._pick(next(iter(names[close[0]])))
        return None

    def candidates(self, spoken):
        """Projects `spoken` could mean, for "did you mean avtec hs or avtec circlip?"."""
        k = key(FILLER.sub(" ", spoken))
        return [p for p in self.items if k and any(key(a).startswith(k) for a in [p.name] + p.aliases)]

    def _pick(self, p):
        self.current = p
        return p

    def recent(self, n=3):
        return sorted(self.items, key=lambda p: -p.touched())[:n]

    # --- running (biztel run) ---

    def running(self):
        """{folder name: state} of what biztel run runs now."""
        out = {}
        for f in os.listdir(RUNS) if os.path.isdir(RUNS) else []:
            try:
                with open(os.path.join(RUNS, f)) as fh:
                    st = json.load(fh)
                os.kill(st["pid"], 0)
                out[st["name"]] = st
            except (OSError, ValueError, KeyError):
                pass
        return out

    def run(self, p, on_up=None):
        """Open a terminal running `biztel run` for `p`. on_up(links) once its links are known (thread)."""
        biztel = shutil.which("biztel") or os.path.join(HOME, ".npm-global/bin/biztel")
        inner = "%s run %s; echo; read -rp 'Stopped. Press Enter to close. '" % (
            shlex.quote(biztel), shlex.quote(p.path))
        desktop.go_to_jarvis_workspace()
        subprocess.Popen(["gnome-terminal", "--title", "biztel run · %s" % p.name, "--role", _role(p),
                          "--", "bash", "-c", inner],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if on_up:
            threading.Thread(target=self._wait_up, args=(p, on_up), daemon=True).start()

    def _wait_up(self, p, on_up, seconds=60):
        """Links come in one by one; report once a few seconds pass with no new one."""
        deadline, links, since = time.time() + seconds, [], time.time()
        while time.time() < deadline:
            time.sleep(1)
            st = self.running().get(p.name)
            if st is None:
                if time.time() - since > 15:  # it never started, or it stopped again
                    return on_up(None)
                continue
            if st["links"] != links:
                links, since = st["links"], time.time()
            elif links and time.time() - since >= 4:
                return on_up(links)
        on_up(links)

    def show(self, p):
        """Bring up the terminal `p` runs in. True if there was one."""
        return desktop.activate_role(_role(p))

    def stop(self, st, wait=8):
        """Stop a run like `biztel stop` does: SIGTERM to biztel run, then SIGKILL what's left."""
        try:
            os.kill(st["pid"], signal.SIGTERM)
        except OSError:
            pass
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                os.kill(st["pid"], 0)
                time.sleep(0.2)
            except OSError:
                break
        for s in st.get("services") or []:
            try:
                os.killpg(s["pid"], signal.SIGKILL)
            except OSError:
                pass


def _role(p):
    return "biztel-run-" + key(p.name)


def _aliases(name, path, title):
    """Ways to say a folder name: "avtec-hs" -> "avtec hs", "ProductVT" -> "product vt", "J.A.R.V.I.S" -> "jarvis"."""
    out = []
    try:
        with open(os.path.join(path, "project.json")) as f:
            pj = json.load(f).get("name")
        if pj and pj != name:
            out.append(str(pj))
    except (OSError, ValueError, AttributeError):
        pass
    if re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]?\.?", name):
        out.append(name.replace(".", "").lower())
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", re.sub(r"[-_.]+", " ", name)).strip()
    out.append(words.lower())
    if title and len(title.split()) <= 4 and key(title) != key(name):
        out.append(title)
    seen, uniq = set(), []
    for a in out:
        if key(a) and key(a) not in seen:
            seen.add(key(a))
            uniq.append(a)
    return uniq


def _readme(path):
    """(title, one-line summary) from the project's README: the heading and the first real sentence."""
    for f in READMES:
        try:
            with open(os.path.join(path, f), errors="replace") as fh:
                text = fh.read(6000)
        except OSError:
            continue
        title, para, in_code = "", [], False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            if s.startswith("#"):
                if para:
                    break
                title = title or s.lstrip("#").strip()
                continue
            if not s:
                if para:
                    break
                continue
            if s.startswith(("|", "<", "![", "[!", "---", ">", "- [", "* [")):
                continue
            para.append(s)
        summary = _plain(" ".join(para))
        summary = re.split(r"(?<=[.!?])\s", summary)[0][:220] if summary else ""
        return _plain(title), summary
    return "", ""


def _plain(md):
    md = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", md)
    return re.sub(r"[`*_]{1,3}", "", md).strip()
