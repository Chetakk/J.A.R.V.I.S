"""Status (skills/status.py), "did that work?" (skills/checks.py + core/verify.py: what JARVIS saw after its last
action, said honestly) and the clipboard (skills/clipboard.py)."""
import os
from collections import namedtuple

import pytest

from harness import Pulse

MEMINFO = "MemTotal:       15950000 kB\nMemFree:          900000 kB\nMemAvailable:    5500000 kB\n"
Statvfs = namedtuple("Statvfs", "f_bavail f_frsize")


@pytest.fixture
def healthy(jv, monkeypatch):
    p = Pulse(jv.shell)
    p.add("sink", "alsa_output.usb-x.analog-stereo", "USB Audio").add("source", "alsa_input.usb-x.mono", "USB Mic",
                                                                      volume=100)
    jv.net.on(r"127\.0\.0\.1:8178/", "")
    jv.files["/proc/meminfo"] = MEMINFO
    monkeypatch.setattr(os, "statvfs", lambda path: Statvfs(9 * 10 ** 9 // 4096, 4096))
    jv.j.rest = "idle"
    jv.j.claude.bin = "/usr/bin/claude"
    return p


def test_status_all_good(jv, healthy):
    jv.do("status")
    assert jv.said == ["All good. I'm listening for my name, and there's 9 gigabytes of disk free."]
    assert "Memory: 5.6 GB free." in jv.card_text()


def test_status_says_what_is_broken(jv, healthy):
    jv.net.on(r"127\.0\.0\.1:8178/", raises=OSError("refused"))
    healthy.get("alsa_input.usb-x.mono")["mute"] = True
    jv.do("health check")
    assert jv.says("Mostly fine, but", "speech recognition is NOT running", "microphone is muted")
    assert not jv.says("All good")


def test_status_flags_a_nearly_full_disk(jv, healthy, monkeypatch):
    monkeypatch.setattr(os, "statvfs", lambda path: Statvfs(1 * 10 ** 9 // 4096, 4096))
    jv.do("status")
    assert jv.says("Disk is nearly full")


def test_stop_stops_speech(jv):
    jv.do("stop talking")
    assert jv.shell.ran(r"^spd-say -S") or not jv.j.speaker._proc  # Piper isn't running in tests: spd-say is
    assert not jv.claude.calls


# --- "did that work?" ---

def launches_window(jv, title):
    """The launched app opens a window a moment later, as a real one would."""
    def run(line):
        jv.desktop.add(title, "gnome-terminal-server Gnome-terminal")
        return ""
    return run


def test_did_it_work_after_a_window_opened(jv):
    jv.shell.on(r"^gnome-terminal$", out=launches_window(jv, "Terminal"))
    jv.do("open terminal")
    jv.clear()
    jv.do("did that work")
    assert jv.said == ["Yes, a window opened: Terminal."]


def test_a_failed_app_is_reported_as_failed(jv):
    jv.shell.on(r"^gnome-terminal$", code=1)  # (a window app's stderr isn't kept: it can write to it for days)
    jv.do("open terminal")
    kind, line = jv.j.inbox.get(timeout=1)  # said from the checking thread, through the main loop
    assert (kind, line) == ("say", "Hmm, that didn't work.")
    jv.clear()
    jv.do("did that work")
    assert jv.said == ["No, it failed (exit 1)."]


def test_a_failed_quick_command_says_its_error(jv):
    jv.shell.on(r"^gsettings set org.gnome.desktop.interface", code=1, err="No such schema “org.gnome.desktop”\n")
    jv.do("dark mode")
    kind, line = jv.j.inbox.get(timeout=1)
    assert kind == "say" and "didn't work" in line and "No such schema" in line


def test_nothing_on_screen_is_couldnt_tell_not_yes(jv):
    jv.do("open terminal")  # exits fine but no window shows up
    jv.clear()
    jv.do("did that work", "no")
    assert jv.questions == ["I couldn't tell: nothing new showed up on screen. Want me to look at the screen?"]
    assert not jv.says("Yes")
    assert not jv.claude.calls


def test_couldnt_tell_then_yes_asks_claude_with_a_screenshot(jv):
    jv.do("open terminal")
    jv.clear()
    jv.do("did that work", "yes")
    assert jv.shell.ran(r"gnome-screenshot|import -window|scrot")
    assert jv.claude.asked("did it work")


def test_did_it_work_with_nothing_done(jv):
    jv.do("did it work")
    assert jv.said == ["I haven't done anything lately that I could check."]


def test_a_missing_program_is_said_not_launched(jv):
    jv.shell.missing.add("gnome-terminal")
    jv.do("open terminal")
    assert jv.said == ["Hmm, I can't find gnome-terminal on this computer."]
    assert not jv.shell.ran(r"^gnome-terminal$")


# --- clipboard ---

def test_reads_a_short_clipboard(jv):
    jv.shell.on(r"^xclip -o", out="meeting at 4 in room 2\n")
    jv.do("what's on my clipboard")
    assert jv.said == ["You copied: meeting at 4 in room 2"]


def test_long_clipboard_goes_on_a_card(jv):
    jv.shell.on(r"^xclip -o", out="word " * 400)
    jv.do("read my clipboard")
    assert jv.cards and "word word" in jv.card_text()


def test_summarise_what_i_copied_says_claudes_summary(jv):
    jv.shell.on(r"^xclip -o", out="A long article about the Jetson Orin NX ...")
    jv.claude.reply("It's about the Jetson Orin NX.")
    jv.do("summarise what I copied")
    assert jv.claude.calls[0][0] == "rewrite"
    assert jv.says("It's about the Jetson Orin NX.")
    assert not jv.shell.inputs  # a summary is said, the clipboard is left alone


def test_fix_the_clipboard_puts_the_result_back(jv):
    jv.shell.on(r"^xclip -o", out="teh quick brwon fox")
    jv.claude.reply("the quick brown fox")
    jv.do("fix the grammar in my clipboard")
    assert ("xclip -selection clipboard", "the quick brown fox") in jv.shell.inputs
    assert jv.says("it's on your clipboard") or jv.says("Paste it")


def test_empty_clipboard(jv):
    jv.do("what's on my clipboard")
    assert jv.said == ["Your clipboard is empty."]


def test_job_on_an_empty_clipboard_doesnt_call_claude(jv):
    jv.do("translate my clipboard to hindi")
    assert jv.said == ["Your clipboard is empty. Copy something first."]
    assert not jv.claude.calls


def test_claude_failing_leaves_the_clipboard_alone(jv):
    jv.shell.on(r"^xclip -o", out="teh quick brwon fox")
    jv.claude.fail("Claude took too long to answer")
    jv.do("fix the grammar in my clipboard")
    assert jv.says("didn't work", "took too long")
    assert not jv.shell.inputs


def test_copy_our_conversation(jv):
    jv.do("what's the time")
    jv.do("copy our conversation")
    assert "User: what's the time" in jv.clipboard.text and "JARVIS:" in jv.clipboard.text
    assert "copy our conversation" not in jv.clipboard.text
    assert jv.says("Copied our conversation")


def test_copy_conversation_leaves_out_forgotten(jv):
    jv.do("what's the time")
    jv.do("forget this conversation")
    jv.do("copy the whole chat")
    assert "what's the time" not in jv.clipboard.text


def test_copy_what_you_said_and_above_without_a_card(jv):
    jv.j.speaker.last = "The answer is 42"
    jv.do("copy what you said")
    assert jv.clipboard.text == "The answer is 42"
    jv.j.speaker.last = "Another line"
    jv.do("copy above text")
    assert jv.clipboard.text == "Another line"


def test_repeat_yourself_repeats_without_claude(jv):
    jv.j.speaker.last = "The answer is 42"
    jv.do("repeat yourself")
    assert jv.said[-1] == "The answer is 42"
    assert not jv.claude.calls
