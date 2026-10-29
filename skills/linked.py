"""Everything linked to a project or a topic, on one card: "everything about study buddy", "what do I have on
avtec", "what's linked to the camera calibration", "show me everything for jarvis".

What a knowledge graph would hold, worked out when you ask instead of kept in a database: the project itself
(what it is, its folder, git, whether it's running), its notes, to-dos that mention it, Day Tracker time on it,
bookmarks, files in Downloads / Documents / Desktop and saved cards with its name in them. Nothing is stored or
kept in sync, so it can't go stale. "Everything about X" is only answered here when X is a project; other topics
need "what do I have on X" (else it's a question for Claude)."""
import json
import os
import re
import subprocess
import time
import urllib.request

from skills.base import Skill
from skills.cards import CARDS_DIR
from skills.daytracker import HUB, spoken
from skills.helpers import HOME, and_list
from skills.notes import load_notes
from system.projects import key

FOLDERS = [os.path.join(HOME, d) for d in ("Downloads", "Documents", "Desktop")]
SHOW = 6  # items per section on the card
STOP = {"the", "a", "an", "my", "of", "for", "about", "on", "in", "to", "with", "and", "stuff", "things", "project",
        "station", "app", "repo", "folder"}
NOT_TOPICS = re.compile(r"^(?:today|tonight|tomorrow|this (?:morning|afternoon|evening|week|weekend)|my (?:plate|"
                        r"calendar|schedule)|me|you|it|that|this|now|later)$", re.IGNORECASE)
_TOPIC = r"(?:the |my )?(.+?)(?: project| station)?"


class Linked(Skill):
    def patterns(self):
        return [
            (r"(?:show me |pull up |give me |find |list )?(?:everything|all (?:the )?(?:stuff|things)) "
             r"(?:i (?:have|'?ve got) |you have |we have )(?:about|on|for|to do with|related to) " + _TOPIC,
             lambda m: self.everything(m.group(1), personal=True)),
            (r"what (?:do i have|have i got|do you have|do we have|is there|'?s there) (?:on|about|for|to do with) "
             + _TOPIC, lambda m: self.everything(m.group(1), personal=True)),
            (r"what'?s (?:linked|connected|related) to " + _TOPIC + r"|(?:show me )?(?:all )?my (?:stuff|things|notes "
             r"and files|files and notes) (?:about|on|for) " + _TOPIC,
             lambda m: self.everything(m.group(1) or m.group(2), personal=True)),
            (r"(?:show me |pull up |give me )?everything (?:about|on|for|to do with|related to) " + _TOPIC,
             lambda m: self.everything(m.group(1), personal=False)),
        ]

    def everything(self, topic, personal):
        topic = topic.strip()
        if NOT_TOPICS.match(topic):
            return False  # "what do I have on today": the day, not a topic
        p = self.j.projects.find(topic, strict=True)
        if not p and not personal:
            return False  # "everything about black holes" is a question
        if not p and not _terms(topic):
            return False
        found = gather(self.j, topic, p)
        counts = [(len(found[k]), word) for k, word in (("notes", "note"), ("todos", "to-do"),
                                                        ("bookmarks", "bookmark"), ("files", "file"),
                                                        ("cards", "saved card")) if found[k]]
        if not p and not counts:
            self.say("I don't have anything on %s: no notes, to-dos, bookmarks or files." % topic)
            return
        title = p.spoken if p else topic
        self.j.show({"title": "Everything: %s" % title, "text": card_text(found, p), "choices": _choices(p, found)})
        things = ["%d %s%s" % (n, w, "" if n == 1 else "s") for n, w in counts]
        if found["week"] >= 60:
            things.append("%s on it this week" % spoken(found["week"]))
        if p and found["running"]:
            things.append("it's running now")
        self.say("%s: %s. It's on your screen." % (title, and_list(things)) if things else
                 "I've only got the project itself for %s, nothing linked to it yet. It's on your screen." % title)


def _terms(topic):
    """The words a topic is matched on, without filler: "the camera calibration" -> ["camera", "calibration"]."""
    return [w for w in re.findall(r"[a-z0-9]+", topic.lower()) if w not in STOP]


def matcher(topic, p):
    """match(text) -> True when `text` is about the project `p` (any of its names) or has every word of `topic`
    (a plural matches its singular)."""
    if p:
        names = {re.sub(r"[^a-z0-9]+", " ", n.lower()).strip() for n in [p.name] + p.aliases}
        names = {n for n in names if len(n) >= 3}
        squashed = {key(n) for n in names if len(key(n)) >= 5}

        def match(text):
            low = re.sub(r"[^a-z0-9]+", " ", text.lower())
            return any(re.search(r"\b%s\b" % re.escape(n), low) for n in names) or \
                any(k in key(text) for k in squashed)
        return match
    stems = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _terms(topic)]

    def match(text):
        low = text.lower()
        return all(re.search(r"\b%s" % re.escape(s), low) for s in stems)
    return match


def gather(j, topic, p):
    """{"notes", "todos", "bookmarks", "files", "cards": [...], "today", "week": seconds in VS Code on `p`,
    "running": its biztel run state, "git": a line} for the card."""
    match = matcher(topic, p)
    out = {"notes": [n for n in load_notes() if (p and n.get("project") == p.name) or match(n.get("text", ""))],
           "todos": [], "today": 0, "week": 0, "running": None, "git": ""}
    try:
        with urllib.request.urlopen(HUB + "/api/state", timeout=1.5) as r:
            state = json.load(r)
    except (OSError, ValueError):
        state = {}
    out["todos"] = [t for t in state.get("todos") or [] if not t.get("done") and match(t.get("text", ""))]
    if p:
        out["today"], out["week"] = _time_on(p, state.get("autoEntries") or [])
        out["running"] = j.projects.running().get(p.name)
        out["git"] = _git(p.path)
    web = getattr(j.skills, "web", None)
    out["bookmarks"] = [b for b in (web._bookmarks() if web else [])
                        if match("%s %s %s" % (b["name"], b["url"], b["folder"]))]
    out["files"] = _files(match, p)
    out["cards"] = _cards(match)
    return out


def _time_on(p, entries):
    """(seconds today, seconds in the last 7 days) the Day Tracker saw VS Code open on the project's folder."""
    now = time.time()
    day = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    today = week = 0
    for e in entries:
        start = e.get("start", 0) / 1000
        if start < now - 7 * 86400:
            continue
        parts = e.get("parts") or {e.get("label") or "": e.get("end", 0) - e.get("start", 0)}
        secs = sum(ms for label, ms in parts.items() if label == "VS Code · " + p.name) / 1000
        week += secs
        today += secs if start >= day else 0
    return today, week


def _git(path):
    """"main · last commit 2 hours ago: Fix the login · 3 files changed", or "" if it isn't a git repo."""
    def git(*args):
        try:
            return subprocess.run(["git", "-C", path] + list(args), capture_output=True, text=True,
                                  timeout=2).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return ""
    last = git("log", "-1", "--format=%cr: %s")
    changed = len([l for l in git("status", "--porcelain").splitlines() if l.strip()])
    return " · ".join(x for x in (branch, "last commit %s" % last if last else "",
                                  "%d file%s changed" % (changed, "" if changed == 1 else "s") if changed else
                                  "nothing uncommitted") if x)


def _files(match, p):
    """Newest files in Downloads, Documents and Desktop (3 levels down) whose names match, as (path, mtime)."""
    hits = []
    for top in FOLDERS:
        for root, dirs, files in os.walk(top):
            depth = root[len(top):].count(os.sep)
            dirs[:] = [d for d in dirs if not d.startswith(".") and depth < 2 and d != "node_modules"]
            for name in files + [d for d in dirs if match(d)]:
                path = os.path.join(root, name)
                if not name.startswith(".") and match(name) and not (p and path.startswith(p.path)):
                    try:
                        hits.append((path, os.path.getmtime(path)))
                    except OSError:
                        pass
    return sorted(set(hits), key=lambda h: -h[1])


def _cards(match):
    """Cards you saved ("save that") whose title or text match, newest first."""
    out = []
    for name in sorted(os.listdir(CARDS_DIR) if os.path.isdir(CARDS_DIR) else [], reverse=True):
        path = os.path.join(CARDS_DIR, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                if match(name.replace("-", " ") + " " + f.read(20000)):
                    out.append(path)
        except OSError:
            pass
    return out


def card_text(found, p):
    lines = []
    if p:
        lines.append("**%s**%s" % (p.title or p.spoken, " — %s" % p.summary if p.summary else ""))
        lines.append("`%s`" % p.path.replace(HOME, "~"))
        if found["git"]:
            lines.append("Git: %s" % found["git"])
        if found["running"]:
            urls = " · ".join("[%s](%s)" % (l["url"].split("//")[-1], l["url"])
                              for l in found["running"].get("links") or [])
            lines.append("Running now%s" % (": " + urls if urls else ""))
        if found["week"] >= 60:
            lines.append("Time in VS Code: %s today, %s this week" % (spoken(found["today"]), spoken(found["week"])))
        lines.append("")

    def section(title, items, line):
        if items:
            lines.extend(["**%s (%d)**" % (title, len(items))] + [line(i) for i in items[:SHOW]] +
                         (["- … and %d more" % (len(items) - SHOW)] if len(items) > SHOW else []) + [""])

    section("Notes", found["notes"], lambda n: "- %s" % n.get("text", ""))
    section("To-dos", found["todos"], lambda t: "- [ ] %s" % t.get("text", ""))
    section("Bookmarks", found["bookmarks"], lambda b: "- [%s](%s)" % (b["name"].replace("]", ""), b["url"]))
    section("Files", found["files"], lambda f: "- [%s](file://%s) · %s" % (
        os.path.basename(f[0]), f[0], time.strftime("%-d %b", time.localtime(f[1]))))
    section("Saved cards", found["cards"], lambda c: "- [%s](file://%s)" % (os.path.basename(c), c))
    return "\n".join(lines).strip() or "Nothing linked yet."


def _choices(p, found):
    if not p:
        return []
    say = p.spoken
    return [{"label": "Open in VS Code", "do": "open %s in vs code" % say},
            {"label": "Stop it", "do": "stop %s" % say} if found["running"] else {"label": "Run it", "do": "run %s" % say},
            {"label": "Git status", "do": "git status of %s" % say}]
