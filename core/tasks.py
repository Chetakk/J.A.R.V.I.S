"""A multi-step task: what it's for, its steps and what came of each, kept on disk so it survives a restart.

One task at a time (~/.local/share/jarvis/task.json); finished ones move to tasks.json (the last 20).
A step is {"do": "a JARVIS phrase"} or {"run": "a shell command"} or {"agent": "files"|"code", ...}, with
an optional "say" (spoken first), "check" (how to see it worked) and "then": "ask" (Claude plans the rest
after seeing this step's result). skills/tasks.py runs them; this module only keeps and checks them.
"""
import glob
import json
import os
import re
import subprocess
import time

from system import desktop

STATE_DIR = os.path.expanduser("~/.local/share/jarvis")
TASK_FILE = os.path.join(STATE_DIR, "task.json")
HISTORY_FILE = os.path.join(STATE_DIR, "tasks.json")
KEEP = 20  # finished tasks kept in the history

# Step status: what came of it. "unchecked" = it ran, but nothing showed whether it worked.
PENDING, RUNNING, DONE, UNCHECKED, FAILED, SKIPPED, DECLINED = (
    "pending", "running", "done", "unchecked", "failed", "skipped", "declined")
# Task state. "waiting" = for your OK before it starts; "handed" = given to a Claude Code task terminal.
OPEN_STATES = ("waiting", "running", "paused")
MARK = {DONE: "x", UNCHECKED: "x", SKIPPED: "-", DECLINED: "-"}
NOTE = {UNCHECKED: " · *not checked*", FAILED: " ✗", SKIPPED: " · *skipped*", DECLINED: " · *you said no*"}

# Steps that change or remove things, send, or can't be undone: asked about before a task starts (unless you
# asked for that yourself) and never retried on their own.
DESTRUCTIVE = re.compile(r"\b(?:delete|remove|trash|erase|wipe|empty|clear|forget|close|quit|kill|uninstall|"
                         r"install|rename|move (?!.*\bworkspace\b)|tidy|organi[sz]e|send|post|submit|buy|pay|"
                         r"order|shut ?down|restart|reboot|log ?out|format|overwrite|upload|reset)\b", re.IGNORECASE)
# What a step that's safe to try twice starts with: opening, switching, looking, reading. (Not typing, running
# or adding something: twice would do it twice.)
RETRY_SAFE = re.compile(r"^(?:open|launch|switch to|go to|show|bring up|find|search|look up|google|maximi[sz]e|"
                        r"minimi[sz]e|put \S+(?: \S+)? on the (?:left|right)|move this to workspace|what|is|are|"
                        r"check|summari[sz]e|read|list|how)\b", re.IGNORECASE)
# Never from Claude, in a task or through `jarvis do`: JARVIS itself, its memory, its own code.
BLOCKED = re.compile(r"^(?:(?:close|quit|exit|shut ?down|turn off|restart|reload|kill) (?:yourself|jarvis)|"
                     r"forget (?:this conversation|everything|number \d+)|open claude|"
                     r"(?:ask|tell|have) claude(?: code)? .*\b(?:yourself|jarvis)\b)", re.IGNORECASE)

BROWSERS = ("chrome", "chromium", "firefox", "brave", "epiphany")


def new(request, steps, title="", source="claude"):
    """A task for `request` from Claude's (or your) steps; steps that aren't understood are dropped."""
    task = {"id": time.strftime("%Y%m%d-%H%M%S"), "request": request, "title": title or request[:50],
            "source": source, "state": "running", "at": time.time(), "started": time.time(), "rounds": 1,
            "why": "", "steps": []}
    add_steps(task, steps)
    return task


def add_steps(task, steps, limit=12):
    """Append Claude's steps (its "task" field, or the older shell-only "steps"), up to `limit` in all."""
    for raw in steps or []:
        step = _step(raw)
        if step and len(task["steps"]) < limit:
            task["steps"].append(step)
    return task


def _step(raw):
    if isinstance(raw, str):
        raw = {"do": raw}
    if not isinstance(raw, dict):
        return None
    step = {"status": PENDING, "tries": 0, "result": ""}
    if str(raw.get("do") or "").strip():
        step["do"] = str(raw["do"]).strip()
    elif str(raw.get("run") or raw.get("command") or "").strip():  # "command": Claude's older "steps"
        step["run"] = str(raw.get("run") or raw.get("command")).strip()
        step["output"] = bool(raw.get("output"))
    elif raw.get("agent") in ("files", "code") and str(raw.get("task") or raw.get("ask") or "").strip():
        step["agent"] = raw["agent"]
        step["task"] = str(raw.get("task") or raw.get("ask")).strip()
        if raw.get("folder"):
            step["folder"] = str(raw["folder"])
    else:
        return None
    if str(raw.get("say") or "").strip():
        step["say"] = str(raw["say"]).strip()
    if isinstance(raw.get("check"), dict):
        check = {k: str(v) for k, v in raw["check"].items() if k in ("window", "process", "file", "url") and v}
        if check:
            step["check"] = check
    if raw.get("then") == "ask":
        step["then"] = "ask"
    return step


def label(step):
    """The step in a few words, for the card and for speaking."""
    if "do" in step:
        return step["do"]
    if "run" in step:
        return "run `%s`" % step["run"]
    if step["agent"] == "files":
        return "files agent: %s" % step["task"]
    return "Claude Code: %s" % step["task"]


def spoken(step):
    return re.sub(r"[`*]", "", label(step))


def risky(step, safety=None):
    """Why this step needs your OK before the task runs it (and is never retried on its own), or None."""
    if "do" in step:
        m = DESTRUCTIVE.search(step["do"])
        return m and "it would %s" % m.group(0).lower().strip()
    if "run" in step:
        level, reason = safety.classify(step["run"]) if safety else ("confirm", "")
        return None if level == "auto" else reason or "it changes things"
    return "it hands work to Claude Code" if step["agent"] == "code" else None


def retry_safe(step, safety=None):
    """Can it simply be tried again if it didn't work? Only opening, switching, looking things up, safe commands."""
    if risky(step, safety):
        return False
    if "do" in step:
        return bool(RETRY_SAFE.match(step["do"]))
    return "run" in step or step.get("agent") == "files"


def current(task):
    """Index of the next step to run (the first not finished), or None when all are."""
    return next((i for i, s in enumerate(task["steps"]) if s["status"] in (PENDING, RUNNING, FAILED)), None)


def counts(task):
    done = sum(s["status"] in (DONE, UNCHECKED) for s in task["steps"])
    return done, len(task["steps"])


# --- checks: did a step really do what it should? Local and quick; (True|False, what was seen) ---

def check(what, wait=6.0):
    """Look for what a step should have left behind: {"window": words} (a window's title or app), {"process":
    name}, {"file": path, may use * patterns}, {"url": a site or page-title words}. Retried for up to `wait`
    seconds, since apps take a moment to open. (ok, what was seen)."""
    deadline = time.time() + wait
    while True:
        ok, seen = _check_once(what)
        if ok or time.time() >= deadline:
            return ok, seen
        time.sleep(0.5)


def _check_once(what):
    seen = []
    for kind, value in what.items():
        value = value.strip()
        if kind == "window":
            w = _window(value.lower())
            ok, line = bool(w), ("a window: %s" % (w.title or w.wm_class) if w else "no window with “%s”" % value)
        elif kind == "process":
            name = os.path.basename(value.split()[0]) if value else ""
            ok = bool(name) and subprocess.call(["pgrep", "-if", name], stdout=subprocess.DEVNULL) == 0
            line = "%s is running" % name if ok else "%s isn't running" % name
        elif kind == "file":
            hits = glob.glob(os.path.expanduser(value))
            ok, line = bool(hits), ("%s is there" % hits[0].replace(os.path.expanduser("~"), "~") if hits
                                    else "no file %s" % value)
        else:  # url: a browser tab showing it (its title has the site's name in it)
            word = site_word(value)
            tab = next((w for w in reversed(desktop.windows()) if any(b in w.wm_class for b in BROWSERS)
                        and word in w.title.lower()), None)
            ok, line = bool(tab), ("the browser shows %s" % tab.title if tab else "no browser tab with “%s”" % word)
        if not ok:
            return False, line
        seen.append(line)
    return True, "; ".join(seen)


def _window(words):
    """Front-most window whose app is `words` or whose title has them as whole words. A word under 3 letters
    must be the whole title (a folder "a" in Files), else it would be found in nearly any window."""
    words = words.replace("vs code", "code").replace("vscode", "code").strip()
    if len(words) < 3:
        return next((w for w in reversed(desktop.windows()) if words and w.title.lower().strip() == words), None)
    pattern = re.compile(r"(?<!\w)%s(?!\w)" % re.escape(words))
    return next((w for w in reversed(desktop.windows()) if pattern.search(w.wm_class)
                 or pattern.search(w.title.lower())), None)


def site_word(url):
    """"https://www.youtube.com/watch?v=x" -> "youtube"; "Gmail inbox" -> "gmail inbox"."""
    host = re.sub(r"^\w+://", "", url.strip().lower()).split("/")[0]
    if "." in host and " " not in host:
        parts = [p for p in host.split(".") if p not in ("www", "m", "com", "org", "net", "io", "in", "co", "dev")]
        return parts[0] if parts else host
    return url.strip().lower()


# --- on disk ---

def save(task):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = TASK_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(task, f, indent=1)
        os.replace(tmp, TASK_FILE)
    except OSError as e:
        print("[tasks] couldn't save: %s" % e)


def load():
    """The task that was on when JARVIS last ran, or None. One that was running is paused now (cut off)."""
    try:
        with open(TASK_FILE) as f:
            task = json.load(f)
    except (OSError, ValueError):
        return None
    if task.get("state") == "running":
        task["state"], task["why"] = "paused", "I was restarted in the middle of it"
        for step in task["steps"]:
            if step["status"] == RUNNING:  # cut off half way: run again only if that's safe
                step["status"], step["result"] = PENDING, "cut off by a restart"
    return task


def finish(task):
    """A finished (or stopped) task goes into the history; nothing is left to continue."""
    history = past()
    history.append(task)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(HISTORY_FILE, "w") as f:
            json.dump(history[-KEEP:], f)
        os.remove(TASK_FILE)
    except OSError:
        pass


def past():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


# --- what you see ---

def text(task):
    """The task as a checklist: each step ticked off or marked with what came of it."""
    lines = []
    now = current(task)
    for i, step in enumerate(task["steps"]):
        st = step["status"]
        note = NOTE.get(st, "")
        if i == now and task["state"] == "running":
            note = " ← now"
        elif i == now and task["state"] == "waiting":
            note = " ← waiting for your OK"
        elif i == now and task["state"] == "paused" and st != FAILED:
            note = " ← paused here"
        line = "- [%s] %s%s" % (MARK.get(st, " "), label(step), note)
        if step.get("result") and st != PENDING:
            line += "\n  - %s" % step["result"][:140]
        lines.append(line)
    return "\n".join(lines)


def card(task):
    """The task's card: its checklist, how far it got, and buttons for what you can do with it now."""
    done, total = counts(task)
    state = task["state"]
    head = {"running": "Working: %d of %d done" % (done, total), "waiting": "%d steps, waiting for your OK" % total,
            "paused": "Paused at %d of %d" % (done, total), "done": "Done: %d of %d" % (done, total),
            "failed": "Stopped: %d of %d done" % (done, total), "stopped": "Stopped at %d of %d" % (done, total),
            "handed": "Handed to Claude Code"}.get(state, state)
    body = "**%s**%s\n\n%s" % (head, " · %s" % task["why"] if task.get("why") and state != "running" else "",
                                text(task))
    unchecked = sum(s["status"] == UNCHECKED for s in task["steps"])
    if unchecked and state in ("done", "failed", "stopped"):
        body += "\n\n*%d step%s ran but couldn't be checked.*" % (unchecked, "" if unchecked == 1 else "s")
    choices = []
    if state == "running":
        choices = [{"label": "Pause", "do": "pause the task"}, {"label": "Stop", "do": "stop the task"}]
    elif state == "paused":
        choices = [{"label": "Continue", "do": "continue the task"}, {"label": "Skip this step", "do": "skip that step"},
                   {"label": "Hand to Claude Code", "do": "hand the task to claude code"},
                   {"label": "Stop", "do": "stop the task"}]
    return {"id": "task", "title": "Task: %s" % task["title"][:50], "text": body, "choices": choices}


def summary(task):
    """Steps and results in plain lines, for Claude to plan the rest (results are data, marked as such)."""
    out = []
    for i, step in enumerate(task["steps"], 1):
        out.append("%d. %s: %s%s" % (i, spoken(step), step["status"],
                                     " (%s)" % step["result"][:600] if step.get("result") else ""))
    return "\n".join(out)
