""""Where was I" (skills/where_was_i.py), clipboard history (skills/clipboard_history.py: in memory only) and the
Jetson Nano (skills/nano.py: status, its keyboard link, commands run on it under the same safety rules)."""
import time

import pytest

from harness import Clock
from skills import clipboard_history, helpers, nano


# --- where was I ---

def test_where_was_i_says_your_last_request(jv):
    jv.do("what's 12 times 7")
    jv.clear()
    jv.do("where was i")
    assert jv.says("The last thing you asked me, just now, was: what's 12 times 7.")


def test_where_was_i_offers_jarvis_workspace_windows(jv):
    jv.do("what's 12 times 7")
    jv.desktop.add("biztel run · study-buddy", "gnome-terminal-server Gnome-terminal", workspace=5, front=False)
    jv.clear()
    jv.do("where was i", "yes")
    assert jv.questions[0].endswith("You've got 1 window open on my workspace. Take you there?")
    assert jv.desktop.did("workspace") == [5]


def test_where_was_i_fresh(jv):
    jv.do("where was i")
    assert jv.said == ["I don't have anything from before, we're starting fresh."]


def test_private_requests_are_left_out(jv):
    jv.do("remember my locker code is 4512")
    jv.clear()
    jv.do("where was i")
    assert not jv.says("locker")


# --- clipboard history ---

@pytest.fixture
def copies(jv, monkeypatch):
    monkeypatch.setattr(helpers, "time", Clock())
    hist = jv.j.skills.clipboard_history.copies
    hist.extend(["first copy", "second copy", "third copy"])
    jv.desktop.add("notes.txt - gedit", "gedit Gedit")
    return hist


def test_paste_the_one_before(jv, copies):
    jv.do("paste the thing I copied before")
    assert ("xclip -selection clipboard", "second copy") in jv.shell.inputs
    assert jv.desktop.did("keys") == ["ctrl+v"] and list(copies)[-1] == "second copy"


def test_paste_number_three(jv, copies):
    jv.do("paste number 3")
    assert ("xclip -selection clipboard", "first copy") in jv.shell.inputs


def test_clipboard_history_card(jv, copies):
    jv.do("clipboard history")
    assert jv.cards[-1]["text"].startswith("1. third copy")


def test_paste_more_than_remembered(jv, copies):
    jv.do("paste number 7")
    assert jv.said == ["I only remember 3 things you copied."] and not jv.desktop.did("keys")


def test_nothing_copied_yet(jv):
    jv.do("clipboard history")
    assert jv.said == ["I haven't seen you copy anything yet."]


def test_history_is_never_written_to_disk(jv, copies):
    import os
    import conftest
    jv.do("clipboard history")
    found = [f for r, _, fs in os.walk(conftest.DATA) for f in fs if "clip" in f.lower()]
    assert not found


# --- nano ---

@pytest.fixture(autouse=False)
def fast_nano(monkeypatch):
    monkeypatch.setattr(nano, "time", Clock())


def test_nano_up_and_linked(jv):
    jv.do("is the nano up")
    assert jv.said == ["The Nano is up, and the keyboard link is running."]


def test_nano_up_but_link_down(jv):
    jv.shell.on(r"^systemctl --user is-active", code=3)
    jv.do("nano status")
    assert jv.says("the keyboard link isn't running")


def test_nano_unreachable(jv):
    jv.shell.on(r"^ping ", code=1)
    jv.do("check on the nano")
    assert jv.said == ["I can't reach the Nano. It may be off, or the cable is unplugged."]


def test_restart_the_link_asks_first(jv, fast_nano):
    jv.do("restart the nano link")
    assert not jv.shell.ran(r"systemctl --user restart")
    jv.do("restart the nano link", "yes")
    assert jv.shell.ran(r"^systemctl --user restart nano-kvm.service$")


def test_a_command_on_the_nano_asks_then_says_its_output(jv):
    jv.shell.on(r"^ssh .* uptime$", out=" 10:01 up 3 days\n")
    jv.do("run uptime on the nano", "yes")
    assert jv.questions == ["Run uptime on the Nano?"] and jv.said[-1] == "10:01 up 3 days"


def test_a_risky_command_on_the_nano_goes_to_your_terminal(jv):
    jv.do("run rm -rf ~/logs on the nano", "yes")
    assert not jv.shell.ran(r"^ssh ") and jv.manual[0][0].startswith("ssh ")
    assert jv.says("ready in your terminal")


def test_no_connection_to_the_nano(jv):
    jv.shell.on(r"^ssh ", code=255, err="ssh: connect to host 192.168.50.10 port 22: No route to host")
    jv.do("run uptime on the nano", "yes")
    assert jv.said[-1] == "I couldn't connect to the Nano."


def test_a_command_that_fails_silently_isnt_called_done(jv):
    jv.shell.on(r"^ssh ", code=1)
    jv.do("run ls /nonexistent on the nano", "yes")
    assert jv.said[-1] == "That failed on the Nano (exit 1)."


def test_a_failing_command_says_its_error(jv):
    jv.shell.on(r"^ssh ", code=2, err="ls: cannot access '/x': No such file or directory")
    jv.do("run ls /x on the nano", "yes")
    assert jv.said[-1] == "That failed on the Nano (exit 2): ls: cannot access '/x': No such file or directory"


def test_hand_the_keyboard_to_the_nano_without_the_script_is_handed_on(jv):
    jv.do("switch to the nano")
    assert not jv.shell.ran(r"nano-focus")
