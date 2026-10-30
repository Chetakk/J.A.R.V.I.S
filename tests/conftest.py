"""Seals the tests off from this computer before any JARVIS code is imported (see harness.py for the fakes).

HOME is a temp folder, wiped before each test; only the models folder is linked in (read-only use), so the
meaning matcher works. DISPLAY and the session bus are unset, so a call the fakes missed fails instead of
reaching your desktop or music player. Writing, moving or deleting a file outside the temp HOME and /tmp
(config/, your real ~/.local/share/jarvis) raises PermissionError, so the test fails instead.

  python3 -m pytest tests/        # all capability tests
  python3 -m pytest tests/test_skill_maths.py -q
"""
import copy
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types

REAL_HOME = os.path.expanduser("~")
REAL_DATA = os.path.join(REAL_HOME, ".local/share/jarvis")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="jarvis-test-home-")
DATA = os.path.join(HOME, ".local/share/jarvis")

os.environ["HOME"] = HOME
os.environ["XDG_RUNTIME_DIR"] = os.path.join(HOME, "run")
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"
for var in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "PULSE_SERVER"):
    os.environ.pop(var, None)
sys.path.insert(0, HERE)


def _fresh_home():
    for name in os.listdir(HOME):
        path = os.path.join(HOME, name)
        shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.unlink(path)
    os.makedirs(DATA)
    os.makedirs(os.environ["XDG_RUNTIME_DIR"])
    os.makedirs(os.path.join(HOME, "Projects"))
    os.makedirs(os.path.join(HOME, "Downloads"))
    os.makedirs(os.path.join(HOME, "jarvis-workspace"))
    models = os.path.join(REAL_DATA, "models")
    if os.path.isdir(models):
        os.symlink(models, os.path.join(DATA, "models"))


_fresh_home()

# pynput connects to X when imported; JARVIS only needs its names.
_pynput = types.ModuleType("pynput")
_keyboard = types.ModuleType("pynput.keyboard")


class _Any:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Any()


_keyboard.Listener = _keyboard.Controller = _keyboard.KeyCode = _Any
_keyboard.Key = _Any()
_pynput.keyboard = _keyboard
sys.modules.setdefault("pynput", _pynput)
sys.modules.setdefault("pynput.keyboard", _keyboard)

# Building a JARVIS reads ~1 MB of YAML, compiles ~1,700 regexes (more than re's cache of 512) and loads the
# ONNX model: do each once per run, not once per test.
import re  # noqa: E402

re._MAXCACHE = 20000
import yaml  # noqa: E402

_load = yaml.safe_load
_yaml_cache = {}


def _cached_load(stream):
    name = getattr(stream, "name", None)
    if not isinstance(name, str) or not os.path.isfile(name):
        return _load(stream)
    key = (name, os.path.getmtime(name), os.path.getsize(name))
    if key not in _yaml_cache:
        _yaml_cache[key] = _load(stream)
    return copy.deepcopy(_yaml_cache[key])


yaml.safe_load = _cached_load

try:
    import onnxruntime  # noqa: E402

    _Session = onnxruntime.InferenceSession
    _sessions = {}

    def _cached_session(path, *a, **k):
        if path not in _sessions:
            _sessions[path] = _Session(path, *a, **k)
        return _sessions[path]

    onnxruntime.InferenceSession = _cached_session
except ImportError:
    pass

import pytest  # noqa: E402

import harness  # noqa: E402


_open = open
_SAFE = (HOME, tempfile.gettempdir(), "/dev/")


def _guarded(path):
    """Writing, moving or deleting anything outside the temp HOME (or /tmp) is a test bug: raise, don't do it."""
    if isinstance(path, int):
        return
    real = os.path.realpath(os.fspath(path))
    if not real.startswith(_SAFE) or real.startswith(os.path.realpath(os.path.join(DATA, "models"))):
        raise PermissionError("a test tried to change %s (outside its temp HOME)" % real)


def _guarded_open(file, mode="r", *a, **k):
    if any(c in mode for c in "wax+"):
        _guarded(file)
    elif harness.current is not None:
        text = harness.current.fake_file(file)  # jv.files, pretend /proc/<pid>/stat
        if text is not None:
            return io.BytesIO(text.encode()) if "b" in mode else io.StringIO(text)
    return _open(file, mode, *a, **k)


def _guard(fn, *which):
    def wrapper(*a, **k):
        for i in which:
            if i < len(a):
                _guarded(a[i])
        return fn(*a, **k)
    return wrapper


# Commands and the network are faked for the whole run, not per test: a thread a test left behind (a
# watcher, a live card) must never reach the real shell or the Day Tracker hub after its test ended. They go to
# the running test's fakes; with none running, they fail.
def _world():
    if harness.current is None:
        raise PermissionError("a command or network call outside a test (a leftover thread?)")
    return harness.current


def _which(prog, *a, **k):
    if harness.current is None or os.path.basename(prog) in harness.current.shell.missing:
        return None
    return "/usr/bin/" + os.path.basename(prog)


def _no_socket(*a, **k):
    raise OSError("no network in tests")


import socket  # noqa: E402
import urllib.request  # noqa: E402

subprocess.run = lambda *a, **k: _world().shell.run(*a, **k)
subprocess.Popen = lambda *a, **k: _world().shell.popen(*a, **k)
subprocess.check_output = lambda *a, **k: _world().shell.check_output(*a, **k)
subprocess.call = lambda *a, **k: _world().shell.call(*a, **k)
subprocess.check_call = lambda *a, **k: _world().shell.check_call(*a, **k)
os.system = lambda line: _world().shell.system(line)
os.popen = lambda line, *a, **k: _world().shell.popen(line, shell=True, text=True).stdout
os.execv = os.execvp = os.execvpe = os.execl = os.execlp = lambda *a, **k: _world() and None
shutil.which = _which
os.kill = lambda pid, sig: _world().kill(pid, sig)  # never a real process
os.killpg = lambda pgid, sig: _world().kill(pgid, sig)
urllib.request.urlopen = lambda *a, **k: _world().net.urlopen(*a, **k)
socket.create_connection = _no_socket


@pytest.fixture
def jv(monkeypatch):
    """A JARVIS in the fake world. See harness.JV."""
    _fresh_home()
    from core import verify
    from skills import helpers, todos
    monkeypatch.setattr(verify, "last", None)  # module state: one test's last action isn't the next one's
    monkeypatch.setattr(verify, "_watching", 0)
    monkeypatch.setattr(helpers, "typed_until", 0.0)
    from skills import files
    monkeypatch.setattr(files, "last", {"paths": [], "at": 0.0})
    monkeypatch.setattr(files, "_names", {"key": None, "paths": [], "matrix": None})
    monkeypatch.setattr(verify, "LAUNCH_SECONDS", 1.0)
    monkeypatch.setattr(verify, "QUICK_SECONDS", 0.5)
    monkeypatch.setattr(verify, "grab", lambda geometry: harness.current.grab(geometry) if harness.current else None)
    monkeypatch.setattr(todos.Todos, "_watch", lambda self: None)  # an endless loop; tests run one round of it

    import queue
    import jarvis as J

    import builtins
    monkeypatch.setattr(builtins, "open", _guarded_open)
    for name, which in (("replace", (0, 1)), ("rename", (0, 1)), ("remove", (0,)), ("unlink", (0,)),
                        ("rmdir", (0,)), ("makedirs", (0,)), ("mkdir", (0,)), ("symlink", (1,)),
                        ("truncate", (0,)), ("utime", (0,)), ("chmod", (0,))):
        monkeypatch.setattr(os, name, _guard(getattr(os, name), *which))
    monkeypatch.setattr(shutil, "rmtree", _guard(shutil.rmtree, 0))
    monkeypatch.setattr(shutil, "move", _guard(shutil.move, 0, 1))
    monkeypatch.setattr(shutil, "copy", _guard(shutil.copy, 1))
    monkeypatch.setattr(shutil, "copy2", _guard(shutil.copy2, 1))
    monkeypatch.setattr(shutil, "copyfile", _guard(shutil.copyfile, 1))
    world = harness.JV(monkeypatch, lambda: J.Jarvis(queue.Queue()), harness.Shell(), harness.Net())
    world.clear()  # what JARVIS ran while starting up isn't part of the test
    yield world
    harness.current = None
