"""A sealed world for testing what a skill really does: the real JARVIS and skill code runs, but everything it
would touch outside itself is fake and recorded.

  shell      subprocess.* / os.system: nothing runs. Rules give a command its output, exit code or error.
  net        urllib urlopen / sockets: nothing leaves (the Day Tracker and Study Buddy hubs are live data).
  desktop    X11 windows, keys, clicks: a list of pretend windows; actions are recorded.
  claude     Claude Code: canned answers, or ClaudeError.
  speech     what JARVIS says is recorded, never spoken.
  answers    replies to JARVIS's yes/no and "X, or Y?" questions.
  ui         cards and the chat panel: recorded.
  clipboard  jv.clipboard.text: set by xclip and by a window's copy key (its .selected); read by xclip -o.
  keyring    the GNOME Keyring: a dict (jv.keyring.items); available=False for a desktop without one.
  manual     commands JARVIS put in the manual terminal for you to run: jv.manual, nothing opened.
  screen     jv.screen_changes: whether a window's pixels change after a click (see grab()).
  processes  jv.add_process(pid, name, cpu=...): /proc/<pid>/stat and os.kill see only these (jv.signals).
  files      jv.files[path] = text: what reading that file gives (/proc, /sys); others read as usual.

conftest.py also points HOME at a temp folder (wiped before each test), so notes, memory and reminders
start empty and your real ones are never read or written; and it unsets DISPLAY and the session bus, so any
call the fakes missed fails instead of reaching your desktop.

    def test_volume(jv):
        jv.shell.on(r"pactl get-sink-volume", out="Volume: front-left: 26214 /  40% / ...")
        jv.do("what's the volume")
        assert jv.says("40")
"""
import copy
import io
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import urllib.error

from claude.bridge import ClaudeError, _parse_answer
from system import desktop
from voice import speech


class Shell:
    """Fake subprocess. The first rule whose regex is found in the command line decides what happens;
    a command no rule knows "runs" and succeeds with no output."""

    def __init__(self):
        self.rules = []
        self.calls = []  # command lines, in order
        self.inputs = []  # (command line, stdin text) for run(input=...) and Popen stdin writes
        self.missing = set()  # program names shutil.which() doesn't find
        self.on_input = None  # on_input(command line, stdin text), e.g. the clipboard taking what xclip was given

    def on(self, pattern, out="", code=0, err="", raises=None):
        """Rules added later win over earlier ones (put the general ones first).
        out: text, or a function(command line) giving text or (text, exit code).
        raises: an exception to throw instead, e.g. FileNotFoundError() or subprocess.TimeoutExpired."""
        self.rules.insert(0, (re.compile(pattern), out, code, err, raises))
        return self

    def ran(self, pattern):
        """The command lines matching `pattern` that were run."""
        return [c for c in self.calls if re.search(pattern, c)]

    def _result(self, args, shell=False):
        line = args if isinstance(args, str) else " ".join(shlex.quote(str(a)) for a in args)
        self.calls.append(line)
        for pattern, out, code, err, raises in self.rules:
            if pattern.search(line):
                if raises is not None:
                    raise raises() if isinstance(raises, type) else raises
                out = out(line) if callable(out) else out  # a function may give (output, exit code)
                if isinstance(out, tuple):
                    out, code = out
                return line, out, code, err
        return line, "", 0, ""

    # --- the patched subprocess functions ---
    def run(self, args, *a, input=None, capture_output=False, text=None, universal_newlines=None, check=False,
            **kw):
        line, out, code, err = self._result(args, kw.get("shell"))
        if input is not None:
            self.inputs.append((line, input.decode() if isinstance(input, bytes) else input))
            if self.on_input:
                self.on_input(line, self.inputs[-1][1])
        as_text = text or universal_newlines or kw.get("encoding")
        out, err = (out, err) if as_text else (out.encode(), err.encode())
        if check and code:
            raise subprocess.CalledProcessError(code, args, out, err)
        return subprocess.CompletedProcess(args, code, out, err)

    def check_output(self, args, *a, **kw):
        kw.pop("stdout", None)
        return self.run(args, *a, check=True, **kw).stdout

    def call(self, args, *a, **kw):
        return self.run(args, *a, **kw).returncode

    def check_call(self, args, *a, **kw):
        self.run(args, *a, check=True, **kw)
        return 0

    def popen(self, args, *a, **kw):
        line, out, code, err = self._result(args, kw.get("shell"))
        for stream, data in ((kw.get("stdout"), out), (kw.get("stderr"), err)):
            if data and hasattr(stream, "write"):  # a file it was told to write to (e.g. the executor's stderr)
                stream.write(data.encode() if "b" in getattr(stream, "mode", "b") else data)
                stream.flush()
        return FakePopen(self, line, args, out, code, err, text=kw.get("text") or kw.get("universal_newlines"))

    def system(self, line):
        return self._result(line, True)[2]


class FakePopen:
    _pids = iter(range(40000, 90000))

    def __init__(self, shell, line, args, out, code, err, text=False):
        self.args, self.pid, self.returncode = args, next(self._pids), code
        self._shell, self._line = shell, line
        self.stdout = io.StringIO(out) if text else io.BytesIO(out.encode())
        self.stderr = io.StringIO(err) if text else io.BytesIO(err.encode())
        self.stdin = _Stdin(shell, line)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, input=None, timeout=None):
        if input is not None:
            self._shell.inputs.append((self._line, input.decode() if isinstance(input, bytes) else input))
        return self.stdout.read(), self.stderr.read()

    def kill(self):
        pass

    terminate = send_signal = kill

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Stdin:
    def __init__(self, shell, line):
        self._shell, self._line = shell, line

    def write(self, data):
        self._shell.inputs.append((self._line, data.decode() if isinstance(data, bytes) else data))
        return len(data)

    def flush(self):
        pass

    close = flush


class Net:
    """Fake urllib: a URL no rule knows is unreachable (URLError), like a hub that's down."""

    def __init__(self):
        self.rules = []
        self.calls = []  # (method, url, body)

    def on(self, pattern, body="", status=200, raises=None):
        """body: str, or a dict/list sent as JSON, or a function(url, data) returning either."""
        self.rules.insert(0, (re.compile(pattern), body, status, raises))
        return self

    def opened(self, pattern):
        return [c for c in self.calls if re.search(pattern, c[1])]

    def urlopen(self, req, data=None, timeout=None, **kw):
        url = req if isinstance(req, str) else req.full_url
        data = data if data is not None else getattr(req, "data", None)
        method = (req.get_method() if not isinstance(req, str) else ("POST" if data else "GET"))
        self.calls.append((method, url, data.decode() if isinstance(data, bytes) else data))
        for pattern, body, status, raises in self.rules:
            if pattern.search(url):
                if raises is not None:
                    raise raises
                body = body(url, data) if callable(body) else body
                if not isinstance(body, (str, bytes)):
                    body = json.dumps(body)
                return _Response(body, status, url)
        raise urllib.error.URLError("no network in tests: %s" % url)


class _Response(io.BytesIO):
    def __init__(self, body, status, url):
        super().__init__(body.encode() if isinstance(body, str) else body)
        self.status = self.code = status
        self.url = url
        self.headers = {}

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Pulse:
    """Pretend PulseAudio behind pactl: speakers (sinks) and microphones (sources), the default of each, volumes,
    and what's playing or recording. Switching and volume changes change this state, as pactl would.
    broken: device names pactl refuses to switch to or set."""

    def __init__(self, shell):
        self.devs = {"sink": [], "source": []}
        self.default = {"sink": None, "source": None}
        self.streams = {"sink-inputs": ["12"], "source-outputs": ["7"]}
        self.moved = []  # (stream, device name)
        self.broken = set()
        shell.on(r"^pactl info$", out=lambda line: "Server Name: pulseaudio\nDefault Sink: %s\nDefault Source: %s\n"
                 % (self.default["sink"] or "", self.default["source"] or ""))
        shell.on(r"^pactl list (sinks|sources)$", out=lambda line: self._list(line.split()[-1][:-1]))
        shell.on(r"^pactl list short (sink-inputs|source-outputs)$",
                 out=lambda line: "".join("%s\t1\tprotocol-native.c\tfloat32le 2ch 44100Hz\n" % s
                                          for s in self.streams[line.split()[-1]]))
        shell.on(r"^pactl set-default-(sink|source) ", out=self._set_default)
        shell.on(r"^pactl move-(sink-input|source-output) ", out=self._move)
        shell.on(r"^pactl set-(sink|source)-volume ", out=self._volume)
        shell.on(r"^pactl set-(sink|source)-mute ", out=self._mute)

    def add(self, kind, name, description, volume=60, mute=False, default=False, form="", port=""):
        self.devs[kind].append({"name": name, "description": description, "volume": volume, "mute": mute,
                                "form": form, "port": port})
        if default or self.default[kind] is None:
            self.default[kind] = name
        return self

    def get(self, name):
        return next(d for kind in self.devs.values() for d in kind if d["name"] == name)

    def _list(self, kind):
        blocks = []
        for i, d in enumerate(self.devs[kind]):
            blocks.append("%s #%d\n\tState: RUNNING\n\tName: %s\n\tDescription: %s\n\tMute: %s\n"
                          "\tVolume: front-left: 0 / %3d%% / 0 dB,   front-right: 0 / %3d%% / 0 dB\n"
                          "%s\tProperties:\n\t\tdevice.form_factor = \"%s\"" % (
                              kind.capitalize(), i, d["name"], d["description"], "yes" if d["mute"] else "no",
                              d["volume"], d["volume"], "\tActive Port: %s\n" % d["port"] if d["port"] else "",
                              d["form"]))
        if kind == "source":  # every speaker has an echo "monitor" source; it isn't a microphone
            blocks += ["Source #9%d\n\tName: %s.monitor\n\tDescription: Monitor of %s\n\tMute: no\n"
                       "\tVolume: front-left: 0 / 100%% / 0 dB" % (i, d["name"], d["description"])
                       for i, d in enumerate(self.devs["sink"])]
        return "\n\n".join(blocks) + "\n"

    def _known(self, name):
        return any(d["name"] == name for kind in self.devs.values() for d in kind) and name not in self.broken

    def _set_default(self, line):
        kind, name = line.split()[1][len("set-default-"):], line.split()[2]
        if not self._known(name):
            return "", 1
        self.default[kind] = name
        return ""

    def _move(self, line):
        _, _, stream, name = line.split()
        self.moved.append((stream, name))
        return ""

    def _volume(self, line):
        name, level = line.split()[2], int(line.split()[3].rstrip("%"))
        if not self._known(name):
            return "", 1
        self.get(name)["volume"] = level
        return ""

    def _mute(self, line):
        name, on = line.split()[2], line.split()[3]
        if not self._known(name):
            return "", 1
        self.get(name)["mute"] = on in ("1", "true", "yes")
        return ""


class Clock:
    """For a module's `time`: sleep() moves the clock on at once, so a wait loop ("wait up to 10 s for the window")
    runs its course without the wait. monkeypatch.setattr(module, "time", Clock())."""

    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def __getattr__(self, name):
        return getattr(time, name)


class Hub:
    """The Day Tracker hub (http://localhost:7313, skills/daytracker.py): GET/PUT /api/state with a revision
    number. A PUT with an old revision gets 409 and the current state back, as the real hub does.
    conflicts: how many of the next PUTs lose to a pretend write from the tracker page first."""

    URL = r"^http://localhost:7313/api/state$"

    def __init__(self, net, **state):
        self.state = dict({"rev": 1, "entries": [], "active": None, "goals": {}, "todos": [], "reminders": []},
                          **state)
        self.puts = []
        self.conflicts = 0
        net.on(self.URL, self._api)

    def _api(self, url, data):
        if data is None:
            return copy.deepcopy(self.state)
        body = json.loads(data)
        if self.conflicts:
            self.conflicts -= 1
            self.state["rev"] += 1  # the page saved in between
        if body["rev"] != self.state["rev"]:
            raise urllib.error.HTTPError(url, 409, "Conflict", {}, io.BytesIO(json.dumps(self.state).encode()))
        self.puts.append(body["state"])
        self.state.update(body["state"])
        self.state["rev"] += 1
        return copy.deepcopy(self.state)


class Clipboard:
    """The desktop clipboard behind xclip: "xclip -o" reads it, "xclip -selection clipboard" (with input) sets
    it, and a window's copy key copies its selection into it (see Desktop.keys)."""

    def __init__(self, shell, text=""):
        self.text = text
        shell.on(r"^xclip -o", out=lambda line: self.text)
        shell.on_input = self._set

    def _set(self, line, text):
        if line == "xclip -selection clipboard":
            self.text = text


class Keyring:
    """The GNOME Keyring (system/keyring.py) as a dict. available=False is a desktop without a keyring daemon."""

    def __init__(self):
        self.items = {}
        self.available = True

    def patch(self, monkeypatch):
        from system import keyring
        fakes = {
            "available": lambda: self.available,
            "store": lambda name, value: self.available and bool(self.items.__setitem__(name, value) or True),
            "lookup": lambda name: self.items.get(name) if self.available else None,
            "delete": lambda name: self.available and self.items.pop(name, None) is not None,
            "names": lambda: list(self.items) if self.available else [],
        }
        for name, fn in fakes.items():
            monkeypatch.setattr(keyring, name, fn)


class FakeWindow(desktop.Window):
    """text: what its accessibility tree gives; selected: what its copy key copies; url: a browser's address."""

    def __init__(self, wid, title, wm_class, workspace=1, geometry=(0, 0, 800, 600), pid=None, role=None,
                 frozen=False, text="", selected="", url=""):
        super().__init__(wid, title, wm_class, workspace)
        self.geometry, self.pid, self.role, self.frozen, self.text = geometry, pid, role, frozen, text
        self.selected, self.url = selected, url


class Desktop:
    """Pretend windows, bottom to top (the last is in front). Patches every system.desktop function that talks to
    X; the ones built on top of them (find_window, place, side, ...) run for real on these windows."""

    def __init__(self):
        self.wins = []
        self.active = None
        self.workspace = 1
        self.tiling = False
        self.area = (0, 0, 1920, 1080)
        self.actions = []  # ("focus", title), ("keys", "ctrl+w"), ("click", (x, y)), ...
        self.stuck = set()  # ids of windows that won't move to another workspace
        self.clipboard = None  # the Clipboard, for copy keys
        self._ids = iter(range(0x1000001, 0x2000000))

    def add(self, title, wm_class, front=True, **kw):
        w = FakeWindow(next(self._ids), title, wm_class.lower(), **kw)
        self.wins.append(w)
        if front:
            self.active = w
        return w

    def did(self, kind):
        """What was done of that kind, e.g. did("keys") -> ["ctrl+t"]."""
        return [v for k, v in self.actions if k == kind]

    def _get(self, wid):
        return next((w for w in self.wins if w.id == wid), None)

    def patch(self, monkeypatch):
        rec = self.actions.append
        title = lambda w: getattr(w, "title", w)  # noqa: E731

        def focus(w):
            rec(("focus", title(w)))
            self.active = self._get(w.id) or self.active

        def close_window(w):
            rec(("close", title(w)))
            self.wins = [x for x in self.wins if x.id != w.id]
            if self.active is not None and self.active.id == w.id:
                self.active = self.wins[-1] if self.wins else None

        def move_resize(w, x, y, width, height):
            rec(("move", (title(w), x, y, width, height)))
            real = self._get(w.id)
            if real:
                real.geometry = (x, y, width, height)

        def set_state(w, on, *props):
            rec(("state", (title(w), on) + props))
            real = self._get(w.id)
            if real and on == 1 and any("MAXIMIZED" in p for p in props):
                real.geometry = self.area
            elif real and on == 0 and real.geometry == self.area:
                real.geometry = (self.area[0], self.area[1], self.area[2] // 2, self.area[3])

        def activate(text):
            w = next((w for w in reversed(self.wins) if text.lower() in w.title.lower()), None)
            if w:
                focus(w)
            return bool(w)

        def activate_role(role):
            w = next((w for w in reversed(self.wins) if w.role == role), None)
            if w:
                focus(w)
            return bool(w)

        def keys(combo):
            """A copy key copies the front window's selection (its address after ctrl+l in a browser)."""
            last = self.did("keys")[-1:]
            rec(("keys", combo))
            w = self.active
            if w is not None and combo in ("ctrl+c", "ctrl+shift+c") and self.clipboard is not None:
                copied = w.url if last == ["ctrl+l"] else w.selected
                if copied:
                    self.clipboard.text = copied

        def move_to_workspace(w, n):
            rec(("to_workspace", (title(w), n)))
            real = self._get(w.id)
            if real and real.id not in self.stuck:
                real.workspace = n

        def switch_to(n):
            rec(("workspace", n))
            self.workspace = n

        fakes = {
            "windows": lambda: list(self.wins),
            "active_window": lambda: self.active,
            "current_workspace": lambda: self.workspace,
            "pid": lambda w: getattr(self._get(w.id), "pid", None),
            "geometry": lambda w: getattr(self._get(w.id), "geometry", None),
            "workarea": lambda: self.area,
            "tiling": lambda: self.tiling,
            "unresponsive": lambda wins, seconds=2.0: [w for w in wins if getattr(self._get(w.id), "frozen", False)],
            "windows_with_role": lambda role: [w for w in self.wins if w.role == role],
            "switch_to": switch_to,
            "focus": focus,
            "move_to_workspace": move_to_workspace,
            "set_state": set_state,
            "minimize": lambda w: rec(("minimize", title(w))),
            "close_window": close_window,
            "move_resize": move_resize,
            "keys": keys,
            "click": lambda x, y, button=1: rec(("click", (x, y, button))),
            "activate": activate,
            "activate_role": activate_role,
            "_act": lambda name, wid, data: rec(("x11", name)),
        }
        for name, fn in fakes.items():
            monkeypatch.setattr(desktop, name, fn)

        # The accessibility tree (system/accessibility.py): a window's .text, terminals marked as such.
        from system import accessibility

        def window_text(pid, title="", seconds=2.0, limit=20000):
            w = next((w for w in self.wins if (pid and w.pid == pid) or (title and w.title == title)), None)
            return ((w.text or "")[:limit], "terminal" in w.wm_class) if w else ("", False)
        monkeypatch.setattr(accessibility, "window_text", window_text)
        monkeypatch.setattr(accessibility, "controls", lambda pid, title="", seconds=2.0, limit=300: [])


class FakeClaude:
    """Stands in for j.claude's calls out to Claude Code. Queue replies with reply(); with none queued, ask()
    answers "(claude answer)" and the others return "(claude text)". fail() makes the next call raise."""

    def __init__(self):
        self.calls = []  # (method, prompt)
        self._replies = []

    def reply(self, answer):
        """answer: a str (said), a dict of answer fields (ask) or text (rewrite/_once/files_agent), or an
        exception to raise."""
        self._replies.append(answer)
        return self

    def fail(self, message="I couldn't reach Claude"):
        return self.reply(ClaudeError(message))

    def asked(self, pattern=""):
        return [p for m, p in self.calls if re.search(pattern, p, re.I | re.S)]

    def _next(self, default):
        r = self._replies.pop(0) if self._replies else default
        if isinstance(r, Exception):
            raise r
        return r

    def patch(self, monkeypatch, claude):
        def ask(text, recent=(), on_say=None, image=None, about=None):
            # All of what Claude would be sent, so a test can check nothing private is in it.
            self.calls.append(("ask", "\n".join([about or ""] + list(recent) + [text])))
            r = self._next({"say": "(claude answer)"})
            answer = _parse_answer(json.dumps(r if isinstance(r, dict) else {"say": r}))
            answer["spoken"] = bool(on_say and answer["say"])
            if answer["spoken"]:
                on_say(answer["say"])
            return answer

        def text_call(method):
            def fn(prompt, *a, **k):
                content = a[0] if method == "rewrite" and a else ""  # rewrite(instruction, the text)
                self.calls.append((method, prompt + ("\n\n" + content if content else "")))
                r = self._next("(claude text)")
                return r.get("say", "") if isinstance(r, dict) else r
            return fn

        def locate(image, target, size):
            self.calls.append(("locate", target))
            return self._next((None, ""))

        def open_terminal(cwd=None, prompt=None, model=None):
            self.calls.append(("open_terminal", prompt or ""))
            return True

        monkeypatch.setattr(claude, "ask", ask)
        monkeypatch.setattr(claude, "locate", locate)
        monkeypatch.setattr(claude, "open_terminal", open_terminal)
        for method in ("rewrite", "_once", "files_agent"):
            monkeypatch.setattr(claude, method, text_call(method))


class FakeUI:
    """j.ui: cards and the chat panel. Every call is recorded in .calls; shown cards in .cards."""

    def __init__(self):
        self.calls = []
        self.cards = []
        self.visible = False

    def show_card(self, card, *a, **k):
        self.calls.append(("show_card", card))
        self.cards.append(card)
        self.visible = True

    def current_card(self):
        return self.cards[-1] if self.cards and self.visible else None

    def card_visible(self, *a):
        return self.visible

    def close_card(self, *a, **k):
        self.calls.append(("close_card", a))
        self.visible = False

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: self.calls.append((name, a)) or None


class JV:
    """One JARVIS with the fake world around it. do(text) runs a request the way typing it would."""

    def __init__(self, monkeypatch, build, shell, net):
        """shell, net: the Shell and Net already patched in (before JARVIS is built: it runs commands then)."""
        global current
        current = self
        self.shell, self.net, self.desktop, self.claude, self.ui = shell, net, Desktop(), FakeClaude(), FakeUI()
        self.spoken = []  # every say(): a list of the alternative lines it was given
        self.questions = []  # what JARVIS asked you
        self.answers = []  # your replies, used in order; none left = no answer (like the timeout)
        self.screenshots = []  # where gnome-screenshot was asked to save
        self.trashed = []  # what gio trash put in the Trash
        self.trash_fails = False
        self.screen_changes = None  # see grab()
        self.procs = {}  # pid -> pretend process (add_process)
        self.signals = []  # (pid, signal) sent with os.kill
        self._grabs = 0
        self.files = {}  # path -> text: what reading that file gives (e.g. "/proc/net/wireless")
        self.desktop.patch(monkeypatch)
        self.keyring = Keyring()
        self.clipboard = self.desktop.clipboard = Clipboard(shell)
        self.keyring.patch(monkeypatch)
        shell.on(r"^gnome-screenshot ", out=self._screenshot)
        shell.on(r"^gio trash ", out=self._trash)
        monkeypatch.setattr(speech.Speaker, "say", _say)
        self.j = build()
        j = self.j
        j.ui = self.ui
        j.follow_up = j.keep_following = lambda: None
        self.claude.patch(monkeypatch, j.claude)
        # The manual terminal (risky commands pre-typed for you to run): recorded, no terminal opened.
        self.manual = []  # (command, reason)
        monkeypatch.setattr(j.executor.terminal, "send", lambda command, reason: self.manual.append((command, reason)))
        from core import executor
        monkeypatch.setattr(executor, "with_name", lambda text, name: text)  # "Done, <name>." now and then: off
        monkeypatch.setattr(j, "_answer", self._answer)

    # --- processes: /proc/<pid>/stat and os.kill, for pretend processes only ---
    def add_process(self, pid, name, cpu=0.0, rss_mb=100, state="S", ignores_term=False):
        """A pretend process. cpu: percent it uses (its CPU ticks grow by that between two reads half a second
        apart). ignores_term: SIGTERM doesn't end it (a frozen app), SIGKILL does."""
        self.procs[pid] = {"name": name, "cpu": cpu, "rss": rss_mb * 256, "state": state, "ticks": 1000,
                           "ignores_term": ignores_term}

    def fake_file(self, path):
        """Text for a file the test pretends (jv.files, or /proc/<pid>/stat of a pretend process); None to read the
        real one. A real process's /proc entry reads as missing: tests never see the computer's own processes."""
        if path in self.files:
            return self.files[path]
        m = re.match(r"^/proc/(\d+)/(stat|status|cmdline)$", str(path))
        if not m:
            return None
        p = self.procs.get(int(m.group(1)))
        if not p:
            raise FileNotFoundError(path)
        p["ticks"] += round(p["cpu"] * os.sysconf("SC_CLK_TCK") * 0.5 / 100)
        if m.group(2) == "cmdline":
            return p["name"] + "\0"
        return "%s (%s) %s 1 1 1 0 -1 0 0 0 0 0 %d 0 0 0 20 0 1 0 100 1000 %d\n" % (
            m.group(1), p["name"], p["state"], p["ticks"], p["rss"])

    def proc_ticks(self):
        """For slow_computer._cpu_ticks: {pid: (name, cpu ticks, rss kB)} of the pretend processes."""
        out = {}
        for pid, p in self.procs.items():
            p["ticks"] += round(p["cpu"] * os.sysconf("SC_CLK_TCK") * 0.5 / 100)
            out[pid] = (p["name"], p["ticks"], p["rss"] * 4)
        return out

    def kill(self, pid, sig):
        """os.kill: recorded; only pretend processes can be signalled (others: ProcessLookupError)."""
        self.signals.append((pid, sig))
        p = self.procs.get(pid)
        if not p:
            raise ProcessLookupError(pid)
        if sig == signal.SIGCONT:
            p["state"] = "S"
            for w in self.desktop.wins:
                if w.pid == pid:
                    w.frozen = False
        elif sig == signal.SIGKILL or (sig == signal.SIGTERM and not p["ignores_term"]):
            del self.procs[pid]
            self.desktop.wins = [w for w in self.desktop.wins if w.pid != pid]

    def grab(self, geometry):
        """verify.grab(): what that part of the screen looks like. screen_changes None: can't be read; False: the
        same picture every time; True: a different one each time (the click did something)."""
        if self.screen_changes is None or not geometry:
            return None
        from PIL import Image
        self._grabs += 1
        shade = (self._grabs * 60) % 256 if self.screen_changes else 90
        return Image.new("RGB", (max(1, geometry[2]), max(1, geometry[3])), (shade, shade, shade)), geometry[:2]

    def _trash(self, line):
        """gio trash PATH: moves it into ~/.local/share/Trash/files and writes its .trashinfo, as gio does.
        jv.trash_fails = "message" makes it refuse instead."""
        if self.trash_fails:
            return "", 1  # (gio's message goes to stderr; see Shell.on(err=) for that)
        import shutil
        from urllib.parse import quote
        path = shlex.split(line)[2]
        if not os.path.lexists(path):
            return "", 1
        trash = os.path.expanduser("~/.local/share/Trash")
        os.makedirs(os.path.join(trash, "files"), exist_ok=True)
        os.makedirs(os.path.join(trash, "info"), exist_ok=True)
        name, n = os.path.basename(path), 1
        while os.path.lexists(os.path.join(trash, "files", name)):
            n += 1
            name = "%s.%d" % (os.path.basename(path), n)
        shutil.move(path, os.path.join(trash, "files", name))
        with open(os.path.join(trash, "info", name + ".trashinfo"), "w") as f:
            f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (quote(os.path.abspath(path)),
                                                                time.strftime("%Y-%m-%dT%H:%M:%S")))
        self.trashed.append(path)
        return ""

    def _screenshot(self, line):
        """gnome-screenshot -f PATH (-a/-w too): writes a small grey picture there, as a real one would."""
        from PIL import Image
        path = shlex.split(line)[shlex.split(line).index("-f") + 1]
        Image.new("RGB", (320, 200), (90, 90, 90)).save(path)
        self.screenshots.append(path)
        return ""

    def _answer(self, question):
        self.questions.append(question if isinstance(question, str) else question[0])
        _say(self.j.speaker, question)
        return self.answers.pop(0) if self.answers else None

    def do(self, text, *answers, wait=2.0):
        """Handle `text` (answering JARVIS's questions with `answers`), then wait for the threads it started."""
        self.answers.extend(answers)
        before = set(threading.enumerate())
        result = self.j.handle(text)
        self.settle(before, wait)
        return result

    def settle(self, before=(), wait=2.0):
        deadline = time.time() + wait
        for t in threading.enumerate():
            if t in before or t is threading.current_thread() or isinstance(t, threading.Timer):
                continue
            t.join(max(0.0, deadline - time.time()))

    # --- what came of it ---
    @property
    def said(self):
        """The lines JARVIS said (the first of each set of alternatives)."""
        return [alts[0] for alts in self.spoken]

    def says(self, *fragments):
        """True if one line JARVIS said (any of its alternatives) holds every fragment (case-insensitive)."""
        return any(all(f.lower() in alt.lower() for f in fragments) for alts in self.spoken for alt in alts)

    def clear(self):
        """Forget what was said and run so far (e.g. after setting something up through JARVIS itself)."""
        del self.spoken[:], self.questions[:], self.shell.calls[:], self.shell.inputs[:], self.claude.calls[:]
        del self.desktop.actions[:], self.ui.calls[:], self.net.calls[:], self.manual[:]

    @property
    def cards(self):
        return self.ui.cards

    def card_text(self):
        return "\n".join("%s\n%s" % (c.get("title") or "", c.get("text") or "") for c in self.cards)


current = None


def _say(speaker, text):
    alts = [str(t) for t in text] if isinstance(text, (list, tuple)) else [str(text)] if text else []
    if alts and current is not None:
        current.spoken.append(alts)
        speaker.last = alts[0]
        if speaker.on_say:
            speaker.on_say(alts[0])
