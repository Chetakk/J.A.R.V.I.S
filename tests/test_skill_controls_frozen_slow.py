"""Clicking and pressing in the front window (skills/controls.py: the app's shortcut, then its accessibility
tree, then a spot Claude finds, clicked only after a yes; then checked), frozen apps (skills/frozen.py) and
"what's slowing my computer" (skills/slow_computer.py). Only pretend processes are ever signalled."""
import signal

import pytest

from harness import Clock
from skills import controls, frozen, slow_computer
from system import accessibility

GEDIT = "gedit Gedit"
CHROME = "google-chrome Google-chrome"


class Pressable:
    def __init__(self, works=True):
        self.works, self.pressed = works, 0

    def do_action(self, i):
        self.pressed += 1
        return self.works


@pytest.fixture
def fast(monkeypatch):
    for module in (controls, frozen, slow_computer):
        monkeypatch.setattr(module, "time", Clock())


def with_controls(monkeypatch, *items):
    """The front app's accessibility tree: (name, menu, Pressable)."""
    found = [accessibility.Control(n, "push button", menu, not menu, acc) for n, menu, acc in items]
    monkeypatch.setattr(accessibility, "controls", lambda pid, title="", seconds=2.0, limit=300: found)


# --- keys and clicks ---

def test_press_spoken_keys(jv, fast):
    jv.desktop.add("notes.txt - gedit", GEDIT)
    jv.do("press control shift t")
    assert jv.desktop.did("keys") == ["ctrl+shift+t"]


def test_click_uses_the_apps_own_shortcut_first(jv, fast):
    jv.desktop.add("notes.txt - gedit", GEDIT)
    jv.screen_changes = True
    jv.do("click save")
    assert jv.desktop.did("keys") == ["ctrl+s"]
    assert jv.says("Done.") or jv.says("Clicked it.")


def test_a_terminal_only_gets_its_own_keys(jv, fast, monkeypatch):
    jv.desktop.add("biztelai@jetson: ~", "gnome-terminal-server Gnome-terminal")
    with_controls(monkeypatch)
    jv.do("click copy")  # ctrl+c would stop what's running in a terminal
    assert "ctrl+c" not in jv.desktop.did("keys")


def test_click_through_the_accessibility_tree(jv, fast, monkeypatch):
    jv.desktop.add("Settings", "gnome-control-center Gnome-control-center", pid=500)
    wifi = Pressable()
    with_controls(monkeypatch, ("Wi-Fi", "", wifi), ("Bluetooth", "", Pressable()))
    jv.screen_changes = True
    jv.do("click wifi")
    assert wifi.pressed == 1 and not jv.desktop.did("click")


def test_risky_buttons_ask_first(jv, fast, monkeypatch):
    jv.desktop.add("Mail", "evolution Evolution", pid=501)
    send = Pressable()
    with_controls(monkeypatch, ("Send", "", send))
    jv.do("click send", "no")
    assert jv.questions == ["Click Send?"] and send.pressed == 0


def test_nothing_changing_after_a_click_is_said(jv, fast, monkeypatch):
    jv.desktop.add("Settings", "gnome-control-center Gnome-control-center", pid=500)
    with_controls(monkeypatch, ("Wi-Fi", "", Pressable()))
    jv.screen_changes = False
    jv.do("click wifi")
    assert jv.said == ["I clicked Wi-Fi, but nothing changed that I can see."]


def test_a_spot_claude_finds_is_clicked_only_after_yes(jv, fast):
    jv.desktop.add("Docs - Google Chrome", CHROME, geometry=(0, 0, 800, 600))
    jv.screen_changes = True
    jv.claude.reply(((400, 300), "blue Sign in button"))
    jv.do("click sign in", "yes")
    assert jv.questions == ["I think it's the blue Sign in button, marked on the card. Shall I click it?"]
    assert jv.cards[-1]["title"] == "Click here?" and jv.cards[-1]["image"]
    assert jv.desktop.did("click") == [(400, 300, 1)]


def test_a_spot_claude_finds_is_not_clicked_after_no(jv, fast):
    jv.desktop.add("Docs - Google Chrome", CHROME, geometry=(0, 0, 800, 600))
    jv.screen_changes = True
    jv.claude.reply(((400, 300), "blue Sign in button"))
    jv.do("click sign in", "no")
    assert not jv.desktop.did("click")


def test_the_window_moving_meanwhile_cancels_the_click(jv, fast):
    win = jv.desktop.add("Docs - Google Chrome", CHROME, geometry=(0, 0, 800, 600))
    jv.screen_changes = True
    jv.claude.reply(((400, 300), "blue Sign in button"))

    def answer(question):
        jv.questions.append(question)
        win.geometry = (100, 100, 800, 600)  # you moved it while JARVIS was asking
        return "yes"
    jv.j._answer = answer
    jv.do("click sign in")
    assert jv.says("The window moved") and not jv.desktop.did("click")


# --- failure cases ---

def test_claude_not_finding_it(jv, fast):
    jv.desktop.add("Docs - Google Chrome", CHROME)
    jv.screen_changes = True
    jv.claude.reply((None, "there's no such button here"))
    jv.do("click sign in")
    assert jv.says("I can't find sign in in this window")
    assert not jv.desktop.did("click")


def test_claude_down_while_looking(jv, fast):
    jv.desktop.add("Docs - Google Chrome", CHROME)
    jv.screen_changes = True
    jv.claude.fail()
    jv.do("click sign in")
    assert jv.says("Sorry, I couldn't ask Claude") and not jv.desktop.did("click")


def test_no_window_to_click_in(jv, fast):
    jv.do("click save")
    assert jv.said == ["I can't see a window to click in."]


def test_click_it_without_a_name_is_handed_on(jv, fast):
    jv.desktop.add("notes.txt - gedit", GEDIT)
    jv.do("click it")
    assert not jv.desktop.did("keys") and not jv.desktop.did("click")


# --- frozen apps ---

def test_everything_responding(jv, fast):
    jv.desktop.add("notes.txt - gedit", GEDIT, pid=50010)
    jv.do("is anything frozen")
    assert jv.said == ["Everything's responding."]


def test_a_frozen_app_is_closed_only_after_yes(jv, fast):
    jv.add_process(50020, "chrome", cpu=0, ignores_term=True)
    jv.desktop.add("Docs - Google Chrome", CHROME, pid=50020, frozen=True)
    jv.do("chrome isn't responding", "yes")
    assert jv.questions[0].startswith("Google Chrome isn't responding: it's stuck waiting")
    assert jv.signals == [(50020, signal.SIGTERM), (50020, signal.SIGKILL)]  # the polite way didn't do it
    assert jv.says("Closed Google Chrome.")


def test_a_frozen_app_left_alone_after_no(jv, fast):
    jv.add_process(50020, "chrome")
    jv.desktop.add("Docs - Google Chrome", CHROME, pid=50020, frozen=True)
    jv.do("is anything frozen", "no")
    assert jv.signals == []


def test_a_busy_app_is_said_to_maybe_come_back(jv, fast):
    jv.add_process(50021, "code", cpu=95)
    jv.desktop.add("main.py - Visual Studio Code", "code Code", pid=50021, frozen=True)
    jv.do("is anything frozen")
    assert "it's busy" in jv.questions[0] and "may come back by itself" in jv.questions[0]


def test_a_paused_app_is_resumed_not_killed(jv, fast):
    jv.add_process(50022, "gedit", state="T")
    jv.desktop.add("notes.txt - gedit", GEDIT, pid=50022, frozen=True)
    jv.do("is anything frozen", "yes")
    assert jv.signals == [(50022, signal.SIGCONT)]
    assert jv.says("It's running again.")


def test_the_desktop_itself_is_never_closed(jv, fast):
    jv.add_process(50023, "gnome-shell")
    jv.desktop.add("Top Bar", "gnome-shell Gnome-shell", pid=50023, frozen=True)
    jv.do("is anything frozen", "yes")
    assert jv.signals == [] and jv.says("part of the desktop")


def test_force_quit_a_responding_app_closes_it_normally(jv, fast):
    jv.add_process(50024, "gedit")
    jv.desktop.add("notes.txt - gedit", GEDIT, pid=50024)
    jv.do("force quit gedit", "yes")
    assert jv.signals == [] and jv.desktop.did("close") == ["notes.txt - gedit"]


def test_the_build_is_stuck_is_not_a_window(jv, fast):
    jv.do("the build is stuck")
    assert not jv.says("responding")


# --- slow computer ---

def test_whats_slowing_my_computer_offers_the_worst_one(jv, fast, monkeypatch):
    monkeypatch.setattr(slow_computer, "_cpu_ticks", jv.proc_ticks)
    jv.files["/proc/meminfo"] = "MemAvailable:    6000000 kB\n"
    jv.add_process(50030, "chrome", cpu=80, rss_mb=1800)
    jv.add_process(50031, "gedit", cpu=1, rss_mb=60)
    jv.do("what's slowing my computer", "yes")
    assert jv.questions[0].startswith("chrome is using the most CPU, 80 percent")
    assert jv.signals == [(50030, signal.SIGTERM)] and jv.says("Closed chrome.")


def test_jarvis_and_the_desktop_are_never_offered(jv, fast, monkeypatch):
    monkeypatch.setattr(slow_computer, "_cpu_ticks", jv.proc_ticks)
    jv.files["/proc/meminfo"] = "MemAvailable:    6000000 kB\n"
    jv.add_process(50032, "whisper-server", cpu=90, rss_mb=900)
    jv.add_process(50033, "gnome-shell", cpu=40, rss_mb=500)
    jv.do("what's slowing my computer")
    assert not jv.questions and jv.signals == []
    assert jv.says("whisper-server is using the most CPU")


def test_quiet_computer_offers_nothing(jv, fast, monkeypatch):
    monkeypatch.setattr(slow_computer, "_cpu_ticks", jv.proc_ticks)
    jv.files["/proc/meminfo"] = "MemAvailable:    6000000 kB\n"
    jv.add_process(50034, "gedit", cpu=1, rss_mb=60)
    jv.do("why is my computer so slow")
    assert jv.says("Nothing's working the CPU hard right now") and not jv.questions
