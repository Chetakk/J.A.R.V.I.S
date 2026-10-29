"""The fake world itself: if these fail, no other test can be trusted not to touch this computer."""
import os
import subprocess
import urllib.request

import pytest

import conftest


def test_home_is_a_temp_folder(jv):
    assert os.path.expanduser("~") == conftest.HOME != conftest.REAL_HOME
    from skills import notes
    assert notes.NOTES_FILE.startswith(conftest.HOME)


def test_no_display_and_no_session_bus(jv):
    assert "DISPLAY" not in os.environ
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "disabled:"


def test_commands_are_recorded_not_run(jv):
    out = subprocess.run(["rm", "-rf", "/nonexistent-jarvis-test"], capture_output=True, text=True)
    assert out.returncode == 0 and jv.shell.ran(r"^rm -rf")
    jv.shell.on(r"^pactl", out="40%", code=3)
    assert subprocess.run("pactl get-sink-volume", shell=True, capture_output=True, text=True).stdout == "40%"
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.check_output(["pactl", "x"])


def test_the_network_is_unreachable_unless_faked(jv):
    with pytest.raises(OSError):
        urllib.request.urlopen("http://127.0.0.1:7313/api/state", timeout=1)
    jv.net.on(r":7313/", {"ok": True})
    assert urllib.request.urlopen("http://127.0.0.1:7313/api/state").read() == b'{"ok": true}'


def test_writing_outside_the_temp_home_is_refused(jv):
    with pytest.raises(PermissionError):
        open(os.path.join(conftest.HERE, "config", "settings.yaml"), "a")
    with pytest.raises(PermissionError):
        os.remove(os.path.join(conftest.REAL_DATA, "notes.json"))
    with open(os.path.join(conftest.HOME, "ok.txt"), "w") as f:
        f.write("fine")


def test_the_desktop_is_pretend(jv):
    from system import desktop
    jv.desktop.add("Inbox - Gmail - Google Chrome", "google-chrome Google-chrome")
    assert desktop.find_window("chrome").title.startswith("Inbox")
    desktop.keys("ctrl+w")
    assert jv.desktop.did("keys") == ["ctrl+w"]


def test_speech_and_claude_are_recorded(jv):
    jv.do("tell me a joke about penguins")
    assert jv.claude.asked("penguins")
    assert jv.says("(claude answer)")
    assert not jv.shell.ran(r"^claude ")
