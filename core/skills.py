"""Built-in skills JARVIS handles itself, before asking Claude.

stop, status, questions about the screen, browser tabs, clipboard, finding files, windows, media,
notes, the Jetson Nano and git. Each skill is a phrase pattern plus a handler; a handler may return
False to let the request carry on (to the command list, the browser, or Claude).
"""
import difflib
import json
import os
import re
import shlex
import subprocess
import time
import urllib.request

from core import desktop, health
from core.daytracker import DayTracker
from core.todos import Todos
from core.claude_bridge import WORKSPACE, ClaudeError
from core.safety import MANUAL

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, "Projects")
NOTES_FILE = os.path.join(HOME, ".local/share/jarvis/notes.json")
SKIP_DIRS = {"node_modules", "snap", "site-packages", "__pycache__", "venv", ".venv", "build", "dist", "go"}
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
BROWSERS = ("google-chrome", "chromium", "firefox")

# Shared with the wake word detector, which listens for these even while JARVIS is talking.
STOP = re.compile(r"^\W*(?:ok(?:ay)?\W+)?(?:stop(?: talking| it| that)?|be quiet|quiet|shut up|enough|"
                  r"that'?s enough|cancel|never ?mind|silence)\W*$", re.IGNORECASE)


class Skills:
    def __init__(self, jarvis):
        self.j = jarvis
        self.say = jarvis.speaker.say
        rules = [
            (STOP, self.stop),
            (r"(?:jarvis |system )?status|health check|are you (?:ok|okay|working)|how are you running", self.status),
            # day tracker timer and today's totals (core/daytracker.py); before projects, so "start work" isn't a project
            *DayTracker(self.say).rules(),
            *Todos(self.say).rules(),  # todos and reminders, kept in the day tracker (core/todos.py)
            # screen
            (r"(?:what'?s|what is) on (?:my|the) screen.*|(?:look at|check|read) (?:my|the) screen.*|"
             r"what am i looking at|what do you see|describe (?:my|the) screen|(?:what'?s|what is|explain) "
             r"(?:this|that) (?:error|page|window|code|message|thing).*|read (?:this|the) page.*", self.screen),
            # browser tabs
            (r"close (?:this|the|that) tab", lambda m: self.tab("ctrl+w", "Closed.")),
            (r"(?:open )?(?:a )?new tab", lambda m: self.tab("ctrl+t", "")),
            (r"(?:re-?open|bring back|undo close) (?:the )?(?:last |closed )*tab", lambda m: self.tab("ctrl+shift+t", "")),
            (r"next tab", lambda m: self.tab("ctrl+Tab", "")),
            (r"previous tab|last tab", lambda m: self.tab("ctrl+shift+Tab", "")),
            (r"go back|back a page|previous page", lambda m: self.tab("alt+Left", "")),
            (r"go forward|forward a page|next page", lambda m: self.tab("alt+Right", "")),
            (r"(?:reload|refresh)(?: the| this)?(?: page| tab)?", lambda m: self.tab("F5", "")),
            (r"scroll (down|up)(?: a bit)?", lambda m: self.tab("Page_Down" if m.group(1) == "down" else "Page_Up", "")),
            (r"scroll to (?:the )?(top|bottom)", lambda m: self.tab("Home" if m.group(1) == "top" else "End", "")),
            (r"zoom (in|out)", lambda m: self.tab("ctrl+plus" if m.group(1) == "in" else "ctrl+minus", "")),
            (r"(?:reset|normal) zoom|zoom (?:to )?normal", lambda m: self.tab("ctrl+0", "")),
            (r"(?:switch|go) to (?:the |my )?(.+?) tab", self.find_tab),
            # media
            (r"(?:pause|stop)(?: the)?(?: music| video| song| youtube| playback)?", lambda m: self.media("Pause")),
            (r"(?:play|resume|continue|unpause)(?: the)?(?: music| video| song| youtube| playback)?",
             lambda m: self.media("Play")),
            (r"(?:next|skip)(?: this)?(?: song| track| video)?", lambda m: self.media("Next")),
            (r"(?:previous|last|go back a) (?:song|track|video)", lambda m: self.media("Previous")),
            (r"what'?s playing|what (?:song|music) is (?:this|playing)|what'?s this song|what am i listening to",
             lambda m: self.playing()),
            # clipboard
            (r"(?:what'?s|what is) (?:on|in) (?:my|the) clipboard|read (?:my|the) clipboard|what did i (?:just )?copy",
             lambda m: self.read_clipboard()),
            (r"((?:summari[sz]e|explain|translate|fix|correct|rewrite|reword|shorten|improve|make|turn|"
             r"proofread|simplify)\b.*?)\b(?:what i (?:just )?copied|(?:my|the) clipboard|the copied text)\b(.*)",
             self.clipboard_job),
            # notes
            (r"(?:remember|note|make a note)(?: that)? (.+)", self.remember),
            (r"what did i (?:ask|tell) you to remember|what do you remember|(?:list |read )?my notes",
             lambda m: self.list_notes()),
            (r"(?:what do you (?:know|remember) about|do you remember) (.+)", self.recall),
            (r"forget (?:about |the note )?(.+)", self.forget),
            # files
            (r"open (?:the |my )?(?:latest|newest|last|most recent) (?:file|download)(?:ed file)?"
             r"(?: in (?:my )?(downloads|documents|desktop|pictures|videos))?", self.latest_file),
            (r"(?:find|locate|where'?s|where is|where are)(?: my| the)? (.+?)(?: file| files)?", self.find_file),
            # jetson nano
            (r"(?:is )?(?:the )?nano (?:up|online|alive|reachable|there|connected|working)|nano status|"
             r"check (?:on )?(?:the )?nano", lambda m: self.nano_status()),
            (r"(?:restart|reconnect|fix|start) (?:the )?nano(?: link| connection| kvm)?", lambda m: self.nano_restart()),
            (r"(?:switch|move|hand)(?: control| the keyboard| the mouse)? to (?:the )?nano|give (?:the )?nano "
             r"(?:the )?(?:keyboard|mouse|control)", lambda m: self.nano_focus()),
            (r"run (.+) on (?:the )?nano", self.nano_run),
            # windows / workspaces
            (r"(?:switch|go) to workspace (\w+)", self.workspace),
            (r"move (?:this|the|this window|it|(.+?)) to workspace (\w+)", self.move_window),
            (r"(maximi[sz]e|minimi[sz]e|full ?screen|restore)(?: the| this)?(?: window)?(?: (.+))?", self.window_state),
            (r"close (?:this|the) window", lambda m: self.close_window()),
            (r"(?:put|move|snap) (?:this|it|the window|(.+?)) (?:to |on )?(?:the )?(left|right)(?: side| half)?",
             self.snap),
            (r"(?:switch to|focus|bring up|show me|go to) (.+)", self.focus),
            # git / projects
            (r"git status(?: (?:of|for|in))?(?: (?:the )?(.+?)(?: project)?)?|(?:any )?changes in (?:the )?(.+?)(?: project)?|"
             r"what changed in (?:the )?(.+?)(?: project)?", self.git_status),
            (r"open (?:the )?(.+?) project(?: in vs ?code)?|open (?:the )?(.+?) in vs ?code", self.open_project),
            (r"run (?:the )?tests(?: (?:in|for|of) (?:the )?(.+?)(?: project)?)?", self.run_tests),
            (r"(?:ask|tell|have) claude (?:to )?(.+?) in (?:the )?(.+?) project", self.claude_project),
        ]
        self.rules = [(re.compile("^(?:%s)$" % p, re.IGNORECASE) if isinstance(p, str) else p, fn)
                      for p, fn in rules]

    def handle(self, text):
        """True if a skill took care of `text`."""
        text = text.strip().rstrip("?.!")
        for pattern, fn in self.rules:
            m = pattern.match(text)
            if m:
                try:
                    done = fn(m)
                except Exception as e:  # a broken skill must never take JARVIS down
                    print("[skills] %s failed: %r" % (getattr(fn, "__name__", "skill"), e))
                    self.say("Sorry, that didn't work: %s." % e)
                    return True
                if done is not False:
                    return True
        return False

    # --- stop / status ---

    def stop(self, m=None):
        self.j.speaker.stop()
        return True

    def status(self, m=None):
        j = self.j
        voice = {"idle": "listening for my name", "off": "voice input is off", "starting": "still starting"}.get(
            j.rest, j.rest)
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/" % j.whisper_port, timeout=0.5)
            stt = "speech recognition is up"
        except Exception:
            stt = "speech recognition is NOT running"
        st = os.statvfs("/")
        disk = st.f_bavail * st.f_frsize / 1e9
        mem = _meminfo("MemAvailable") / 1e9
        claude = "Claude is available" if j.claude.bin else "Claude Code is not installed"
        lines = ["Voice: %s." % voice, "Speech: %s." % stt, "%s (%s mode)." % (claude, j.mode),
                 "Disk: %.1f GB free." % disk, "Memory: %.1f GB free." % mem]
        j.chat("sys", "status\n" + "\n".join(lines))
        broken = list(health.problems(j).values())
        lines += broken
        problems = [l for l in lines if "NOT" in l or "off" in l] + (["Disk is nearly full."] if disk < 2 else [])
        problems += [l for l in broken if l not in problems]
        self.say("All good. I'm %s, and there's %.0f gigabytes of disk free." % (voice, disk) if not problems
                 else "Mostly fine, but: %s" % " ".join(problems))

    # --- screen ---

    def screen(self, m):
        path = os.path.join(WORKSPACE, "screen.png")
        self.say(["Let me take a look.", "One sec, looking at your screen."])
        _screenshot(path)
        self.j._ask_claude(m.group(0), image=path)

    # --- browser tabs ---

    def _browser(self):
        win = desktop.active_window()
        if win and any(b in win.wm_class for b in BROWSERS):
            return win
        for b in BROWSERS:
            win = desktop.find_window(b)
            if win:
                desktop.focus(win)
                time.sleep(0.4)
                return win
        return None

    def tab(self, combo, reply):
        if not self._browser():
            self.say("I don't see a browser open.")
            return
        desktop.keys(combo)
        if reply:
            self.say(reply)

    def find_tab(self, m):
        target = m.group(1).lower()
        win = self._browser()
        if not win:
            self.say("I don't see a browser open.")
            return
        first = None
        for _ in range(40):  # cycle through the tabs, reading the window title
            title = (desktop.find_window(win.wm_class.split()[0]) or win).title
            if target in title.lower():
                self.say(["Here it is.", "Switched."])
                return
            if title == first:
                break
            first = first or title
            desktop.keys("ctrl+Tab")
            time.sleep(0.2)
        self.say("I couldn't find a %s tab." % target)

    # --- media ---

    def media(self, action):
        player = _mpris_player()
        if player:
            player[1].call_sync(action, None, 0, 2000, None)  # Play / Pause / Next / Previous
            return
        win = desktop.find_window("youtube")
        if win and action in ("Play", "Pause"):
            desktop.focus(win)
            time.sleep(0.4)
            desktop.keys("k")  # YouTube's own play/pause key
            return
        if action in ("Play", "Pause"):
            return False  # nothing playing: let "play X" go to the browser / Claude
        self.say("Nothing seems to be playing.")

    def playing(self):
        player = _mpris_player()
        if not player:
            self.say("Nothing seems to be playing.")
            return
        md = player[1].get_cached_property("Metadata")
        md = md.unpack() if md else {}
        title, artist = md.get("xesam:title"), ", ".join(md.get("xesam:artist") or [])
        if not title:
            self.say("Something's playing, but it doesn't say what.")
        else:
            self.say("It's %s%s." % (title, " by %s" % artist if artist else ""))

    # --- clipboard ---

    def read_clipboard(self):
        text = _clipboard()
        if not text:
            self.say("Your clipboard is empty.")
        elif len(text) <= 200:
            self.say("You copied: %s" % text)
        else:
            self.j.chat("sys", "clipboard:\n" + text[:3000])
            self.say("It's %d characters; I've put it in the chat. It starts with: %s" % (len(text), text[:80]))

    def clipboard_job(self, m):
        text = _clipboard()
        if not text:
            self.say("Your clipboard is empty. Copy something first.")
            return
        instruction = (m.group(1) + " this " + m.group(2)).strip()
        self.say(["On it.", "Sure, one moment."])
        try:
            result = self.j.claude.rewrite(instruction, text)
        except ClaudeError as e:
            self.say("Sorry, that didn't work: %s." % e)
            return
        verb = m.group(1).split()[0].lower()
        if verb in ("summarise", "summarize", "explain") and len(result) < 400:
            self.j.chat("claude", result)
            self.say(result)
        else:
            _set_clipboard(result)
            self.j.chat("claude", result)
            self.say(["Done, it's on your clipboard. Just paste it.", "Done. Paste it wherever you like."])

    # --- notes ---

    def _notes(self):
        try:
            with open(NOTES_FILE) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def _save_notes(self, notes):
        os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
        with open(NOTES_FILE, "w") as f:
            json.dump(notes, f, indent=1)

    def remember(self, m):
        note = m.group(1).strip()
        if re.match(r"^to\b", note):
            return False  # "remember to call mom at 5" is a reminder, not a note
        notes = self._notes()
        notes.append({"text": note, "at": time.time()})
        self._save_notes(notes)
        self.say(["Got it, I'll remember that.", "Okay, noted."])

    def list_notes(self):
        notes = self._notes()
        if not notes:
            self.say("You haven't asked me to remember anything yet.")
            return
        self.j.chat("sys", "notes:\n" + "\n".join("- " + n["text"] for n in notes))
        recent = notes[-3:]
        self.say("You've asked me to remember %d thing%s. %s: %s." % (
            len(notes), "" if len(notes) == 1 else "s", "The latest" if len(notes) > 3 else "They are",
            "; ".join(n["text"] for n in reversed(recent))))

    def recall(self, m):
        hits = _search(self._notes(), m.group(1))
        if not hits:
            return False  # maybe a general question: let Claude have it
        self.say("You told me: %s." % "; and ".join(n["text"] for n in hits[:2]))

    def forget(self, m):
        what = m.group(1).strip()
        notes = self._notes()
        if re.match(r"^(?:everything|all(?: (?:my|the))? notes|all of it)$", what, re.IGNORECASE):
            if notes and self.j.ask("Forget all %d notes?" % len(notes)):
                self._save_notes([])
                self.say("Done, all forgotten.")
            return
        hits = _search(notes, what)
        if not hits:
            return False
        if self.j.ask('Forget "%s"?' % hits[0]["text"]):
            notes.remove(hits[0])
            self._save_notes(notes)
            self.say("Forgotten.")

    # --- files ---

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
        where = os.path.relpath(os.path.dirname(best), HOME)
        where = "your home folder" if where == "." else where.replace("/", ", ")
        if self.j.ask("I found %s%s in %s. Want me to open it?" % (
                os.path.basename(best), " and %d more" % (len(hits) - 1) if len(hits) > 1 else "", where)):
            self.j.executor.run("xdg-open %s" % shlex.quote(best), reply="Opening it.", window=True)

    # --- windows ---

    def workspace(self, m):
        n = _number(m.group(1))
        if not n:
            return False
        desktop.switch_to(n)

    def move_window(self, m):
        n = _number(m.group(2))
        win = desktop.find_window(m.group(1)) if m.group(1) else desktop.active_window()
        if not (n and win):
            return False
        desktop.move_to_workspace(win, n)
        self.say("Moved it to workspace %d." % n)

    def window_state(self, m):
        action = m.group(1).lower().replace(" ", "")
        win = desktop.find_window(m.group(2)) if m.group(2) else desktop.active_window()
        if not win:
            return False
        if action.startswith("minimi"):
            desktop.minimize(win)
        elif action == "fullscreen":
            desktop.set_state(win, 2, "_NET_WM_STATE_FULLSCREEN")
        elif action == "restore":
            desktop.set_state(win, 0, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
            desktop.set_state(win, 0, "_NET_WM_STATE_FULLSCREEN")
        else:
            desktop.set_state(win, 1, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")

    def close_window(self):
        win = desktop.active_window()
        if win and self.j.ask("Close %s?" % (win.title.split(" - ")[-1] or "this window")):
            desktop.close_window(win)

    def snap(self, m):
        win = desktop.find_window(m.group(1)) if m.group(1) else desktop.active_window()
        if not win:
            return False
        desktop.focus(win)
        time.sleep(0.3)
        desktop.keys("super+%s" % ("Left" if m.group(2).lower() == "left" else "Right"))

    def focus(self, m):
        target = m.group(1).strip()
        if re.search(r"\b(workspace|tab|website|site|page)\b", target, re.IGNORECASE):
            return False
        win = desktop.find_window(target)
        if not win:
            return False
        desktop.focus(win)

    # --- jetson nano ---

    def _nano(self):
        return dict({"host": "192.168.50.10", "user": "biztel-nano", "key": "~/.ssh/boisys_nano",
                     "service": "nano-kvm.service"}, **(self.j.settings.get("nano") or {}))

    def nano_status(self):
        n = self._nano()
        up = subprocess.call(["ping", "-c1", "-W1", n["host"]], stdout=subprocess.DEVNULL) == 0
        link = subprocess.call(["systemctl", "--user", "is-active", "--quiet", n["service"]]) == 0
        if up and link:
            self.say("The Nano is up, and the keyboard link is running.")
        elif up:
            self.say("The Nano is up, but the keyboard link isn't running. Say restart the Nano link to fix it.")
        else:
            self.say("I can't reach the Nano. It may be off, or the cable is unplugged.")

    def nano_restart(self):
        n = self._nano()
        if self.j.ask("Restart the Nano keyboard link?"):
            subprocess.call(["systemctl", "--user", "restart", n["service"]])
            time.sleep(3)
            self.nano_status()

    def nano_focus(self):
        script = os.path.expanduser("~/.local/bin/nano-focus.sh")
        if not os.path.exists(script):
            return False
        subprocess.Popen([script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def nano_run(self, m):
        n = self._nano()
        inner = m.group(1).strip()
        ssh = "ssh -i %s -o BatchMode=yes -o ConnectTimeout=5 %s@%s %s" % (
            n["key"], n["user"], n["host"], shlex.quote(inner))
        level, reason = self.j.safety.classify(inner)
        if level == MANUAL:
            self.j.executor.terminal.send(ssh, "on the Nano: " + reason)
            self.say("That one's risky on the Nano too, so it's ready in your terminal for you to run.")
            return
        if not self.j.ask("Run %s on the Nano?" % inner):
            return
        try:
            res = subprocess.run(ssh, shell=True, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            self.say("The Nano didn't answer in time.")
            return
        out = (res.stdout or res.stderr).strip()
        self.j.chat("sys", "nano$ %s\n%s" % (inner, out[-2000:]))
        if res.returncode == 255:
            self.say("I couldn't connect to the Nano.")
        elif len(out) <= 150:
            self.say(out or "Done, no output.")
        else:
            self.say("Done. The output's in the chat.")

    # --- git / projects ---

    def git_status(self, m):
        name = next((g for g in m.groups() if g), None)
        path = _project(name) if name else _project("J.A.R.V.I.S")
        if not path:
            return False if name else None
        if not os.path.isdir(os.path.join(path, ".git")):
            self.say("%s isn't a git project." % os.path.basename(path))
            return
        res = subprocess.run(["git", "-C", path, "status", "--porcelain=v1", "-b"], capture_output=True, text=True,
                             timeout=10)
        lines = res.stdout.splitlines()
        branch = lines[0][3:].split("...")[0] if lines else "?"
        changed = [l for l in lines[1:] if not l.startswith("??")]
        new = [l for l in lines[1:] if l.startswith("??")]
        self.j.chat("sys", "git status %s\n%s" % (os.path.basename(path), res.stdout[-2000:]))
        if not changed and not new:
            self.say("%s is clean, on branch %s." % (os.path.basename(path), branch))
        else:
            self.say("%s on branch %s: %d changed file%s and %d new." % (
                os.path.basename(path), branch, len(changed), "" if len(changed) == 1 else "s", len(new)))

    def open_project(self, m):
        name = m.group(1) or m.group(2)
        path = _project(name)
        if not path:
            return False
        self.j.executor.run("code %s" % shlex.quote(path), reply="Opening %s." % os.path.basename(path), window=True)

    def run_tests(self, m):
        path = _project(m.group(1)) if m.group(1) else None
        if m.group(1) and not path:
            return False
        path = path or os.path.join(PROJECTS, "J.A.R.V.I.S")
        self.j.claude.open_terminal(cwd=path, prompt="Run this project's tests and tell me what passed and failed.")
        self.say("I've asked Claude to run the tests for %s. It's in the terminal." % os.path.basename(path))

    def claude_project(self, m):
        path = _project(m.group(2))
        if not path:
            return False
        self.j.claude.open_terminal(cwd=path, prompt=m.group(1))
        self.say("Okay, Claude's on it in %s." % os.path.basename(path))


# --- helpers ---

def _number(word):
    word = (word or "").lower()
    return int(word) if word.isdigit() else NUMBERS.get(word)


def _meminfo(key):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key):
                return int(line.split()[1]) * 1024
    return 0


def _screenshot(path):
    """Whole screen, scaled down so it's quick to send."""
    from PIL import Image
    tmp = path + ".full.png"
    subprocess.run(["gnome-screenshot", "-f", tmp], timeout=10, stderr=subprocess.DEVNULL)
    img = Image.open(tmp)
    img.thumbnail((1600, 1600))
    img.save(path)
    os.remove(tmp)


def _clipboard():
    try:
        return subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, text=True,
                              timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _set_clipboard(text):
    p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    p.communicate(text.encode(), timeout=3)


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in ("the", "a", "my", "an", "of", "file")]


def _search(notes, query):
    words = set(_words(query))
    scored = [(len(words & set(_words(n["text"]))), n) for n in notes]
    return [n for score, n in sorted(scored, key=lambda s: -s[0]) if score]


def _find_files(query, limit=5, seconds=4):
    """Files under your home folder whose names contain every word of `query` (newest first)."""
    words = _words(query)
    if not words:
        return []
    hits, deadline = [], time.time() + seconds
    for root, dirs, files in os.walk(HOME):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for f in files:
            low = f.lower()
            if not f.startswith(".") and not low.endswith((".swp", "~", ".tmp")) and all(w in low for w in words):
                hits.append(os.path.join(root, f))
        if time.time() > deadline:
            break
    return sorted(hits, key=lambda p: -os.path.getmtime(p))[:limit]


def _project(name):
    """Best-matching folder in ~/Projects for a spoken name ("jarvis" -> J.A.R.V.I.S)."""
    if not name or not os.path.isdir(PROJECTS):
        return None
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    dirs = {re.sub(r"[^a-z0-9]", "", d.lower()): d for d in os.listdir(PROJECTS)
            if os.path.isdir(os.path.join(PROJECTS, d))}
    if key in dirs:
        return os.path.join(PROJECTS, dirs[key])
    close = difflib.get_close_matches(key, list(dirs), n=1, cutoff=0.7)
    if close:
        return os.path.join(PROJECTS, dirs[close[0]])
    part = [d for k, d in dirs.items() if key and key in k]
    return os.path.join(PROJECTS, part[0]) if len(part) == 1 else None


def _mpris_player():
    """(name, proxy) of the media player that's playing (or any, if none is)."""
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                              None, None, 0, 2000, None).unpack()[0]
        players = []
        for n in names:
            if n.startswith("org.mpris.MediaPlayer2."):
                proxy = Gio.DBusProxy.new_sync(bus, 0, None, n, "/org/mpris/MediaPlayer2",
                                               "org.mpris.MediaPlayer2.Player", None)
                players.append((n, proxy))
        players.sort(key=lambda p: _mpris_status(p[1]) != "Playing")
        return players[0] if players else None
    except Exception:
        return None


def _mpris_status(proxy):
    st = proxy.get_cached_property("PlaybackStatus")
    return st.unpack() if st else None
