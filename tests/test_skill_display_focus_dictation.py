"""Screen modes (skills/display.py, system/display.py: a change asks first and goes back by itself unless you say
you can see it), focus mode (skills/focus_mode.py) and dictation (skills/dictation.py)."""
import json
import os
import time
import types

import pytest

from skills import display as display_skill
from skills import focus_mode
from system import display


class Xrandr:
    """Pretend xrandr: one HDMI screen; --mode/--rate changes it (unless refused)."""

    MODES = {"1920x1080": ["60.00", "59.94", "50.00"], "1680x1050": ["59.95"], "1280x720": ["60.00", "50.00"]}

    def __init__(self, shell, mode="1920x1080", rate="60.00"):
        self.mode, self.rate, self.applied, self.refuse = mode, rate, [], False
        shell.on(r"^xrandr --query$", out=self._query)
        shell.on(r"^xrandr --output ", out=self._apply)

    def _query(self, line):
        rows = ["Screen 0: minimum 8 x 8, current 1920 x 1080, maximum 32767 x 32767",
                "HDMI-0 connected primary %s+0+0 (normal left inverted right x axis y axis) 527mm x 296mm" % self.mode]
        for mode, rates in self.MODES.items():
            rows.append("   %-12s  %s" % (mode, "  ".join(
                "%s%s%s" % (r, "*" if (mode, r) == (self.mode, self.rate) else " ",
                            "+" if (mode, r) == ("1920x1080", "60.00") else " ") for r in rates)))
        rows.append("DP-0 disconnected (normal left inverted right x axis y axis)")
        return "\n".join(rows) + "\n"

    def _apply(self, line):
        parts = line.split()
        mode, rate = parts[parts.index("--mode") + 1], parts[parts.index("--rate") + 1]
        self.applied.append((mode, rate))
        if self.refuse:
            return "", 1
        self.mode, self.rate = mode, rate
        return ""


@pytest.fixture
def screen(jv, monkeypatch):
    monkeypatch.setattr(display_skill, "time", types.SimpleNamespace(sleep=lambda s: None, time=time.time))
    return Xrandr(jv.shell)


def test_what_resolution(jv, screen):
    jv.do("what's my screen resolution")
    assert jv.said == ["Your screen is at 1920 by 1080, 60 hertz."]
    assert "1280x720: 60.00, 50.00 Hz" in jv.card_text()


def test_change_and_keep(jv, screen):
    jv.do("set the resolution to 1280 by 720", "yes", "yes")
    assert jv.questions[0].startswith("Change the screen to 1280 by 720 at 60 hertz?")
    assert jv.questions[1] == "Can you see the screen properly? Say yes to keep it."
    assert (screen.mode, screen.rate) == ("1280x720", "60.00")
    assert jv.says("Kept.")


def test_undo_after_keeping(jv, screen):
    jv.do("set the resolution to 720p", "yes", "yes")
    jv.do("undo that")
    assert (screen.mode, screen.rate) == ("1920x1080", "60.00")


@pytest.mark.parametrize("answers", [("yes", "no"), ("yes",)])  # a no, or no answer at all (screen black)
def test_not_seeing_it_puts_it_back(jv, screen, answers):
    jv.do("set the resolution to 1280 by 720", *answers)
    assert screen.applied == [("1280x720", "60.00"), ("1920x1080", "60.00")]
    assert (screen.mode, screen.rate) == ("1920x1080", "60.00")
    assert jv.says("I've put the screen back to 1920 by 1080")


def test_declined_changes_nothing(jv, screen):
    jv.do("set the resolution to 1280 by 720", "no")
    assert screen.applied == []


def test_the_timer_puts_it_back_even_if_nobody_answers(jv, screen):
    """The safety net when JARVIS itself is stuck: the Trial's own timer reverts."""
    s = display.screens()[0]
    trial = display.Trial(s, "1280x720", "60.00", 0.2)
    assert screen.mode == "1280x720"
    time.sleep(0.5)
    assert (screen.mode, screen.rate) == ("1920x1080", "60.00")
    assert trial.keep() is False  # too late to keep it


def test_refresh_rate(jv, screen):
    jv.do("change the refresh rate to 50", "yes", "yes")
    assert (screen.mode, screen.rate) == ("1920x1080", "50.00")


def test_recommended_when_already_there(jv, screen):
    jv.do("use the recommended resolution")
    assert jv.said == ["It's already at 1920 by 1080, 60 hertz."]
    assert not jv.questions


# --- failure cases ---

def test_a_mode_the_screen_cant_do(jv, screen):
    jv.do("set the resolution to 3840 by 2160")
    assert jv.said[0].startswith("HDMI-0 can't do 3840 by 2160. It can do 1920 by 1080")
    assert screen.applied == []


def test_a_rate_the_screen_cant_do(jv, screen):
    jv.do("set the refresh rate to 144")
    assert jv.says("can't do 144 hertz")
    assert screen.applied == []


def test_screen_refusing_the_mode_says_nothing_changed(jv, screen):
    screen.refuse = True
    jv.do("set the resolution to 1280 by 720", "yes")
    assert jv.says("The screen didn't take it, so nothing changed.")
    assert len(jv.questions) == 1


def test_no_xrandr(jv):
    jv.shell.on(r"^xrandr", raises=FileNotFoundError)
    jv.do("what's my screen resolution")
    assert jv.said == ["I can't read the screen settings."]


# --- focus mode ---

def banners(jv):
    return [c.split()[-1] for c in jv.shell.ran(r"show-banners")]


def test_focus_for_a_while_holds_notifications(jv):
    jv.do("focus for 25 minutes")
    assert jv.said == ["Focus mode for 25 minutes. I'll keep quiet and hold notifications."]
    assert banners(jv) == ["false"]
    assert jv.j.skills.focusing()
    with open(focus_mode.FOCUS_FILE) as f:
        assert abs(json.load(f)["until"] - (time.time() + 1500)) < 5
    jv.do("how long is left on my focus")
    assert jv.says("25 minutes left.")


def test_stop_focusing_turns_notifications_back_on(jv):
    jv.do("do not disturb")
    jv.do("stop focusing")
    assert banners(jv) == ["false", "true"]
    assert not jv.j.skills.focusing() and not os.path.exists(focus_mode.FOCUS_FILE)


def test_focus_carries_on_after_a_restart(jv):
    with open(focus_mode.FOCUS_FILE, "w") as f:
        json.dump({"until": time.time() + 600}, f)
    jv.j.skills.focus_mode._restore_focus()
    assert jv.j.skills.focusing()


def test_focus_that_ran_out_while_off_turns_banners_back_on(jv):
    with open(focus_mode.FOCUS_FILE, "w") as f:
        json.dump({"until": time.time() - 60}, f)
    jv.j.skills.focus_mode._restore_focus()
    assert banners(jv) == ["true"] and not os.path.exists(focus_mode.FOCUS_FILE)


def test_stop_focusing_when_not_focusing(jv):
    jv.do("stop focusing")
    assert jv.said == ["You weren't in focus mode."]
    assert not banners(jv)


def test_focus_without_gsettings_says_the_banners_are_not_held(jv):
    jv.shell.on(r"^gsettings ", raises=FileNotFoundError)
    jv.do("focus for 25 minutes")
    assert jv.said == ["Focus mode for 25 minutes. I'll keep quiet, but I couldn't hold your notification banners."]
    assert jv.j.skills.focusing()  # JARVIS itself is quiet; it doesn't claim more than that


def test_focus_when_gsettings_fails_says_so_too(jv):
    jv.shell.on(r"^gsettings ", code=1)
    jv.do("focus for 25 minutes")
    assert jv.says("couldn't hold your notification banners")


# --- dictation ---

def pasted(jv):
    return [text for line, text in jv.shell.inputs if line == "xclip -selection clipboard"]


def test_type_into_the_front_window_then_put_the_clipboard_back(jv):
    jv.desktop.add("Untitled - gedit", "gedit Gedit")
    jv.shell.on(r"^xclip -o", out="what you had copied")
    jv.do("type: see you at five")
    assert pasted(jv) == ["see you at five", "what you had copied"]
    assert jv.desktop.did("keys") == ["ctrl+v"]


def test_typing_into_a_terminal_uses_its_paste_key(jv):
    jv.desktop.add("biztelai@jetson: ~", "gnome-terminal-server Gnome-terminal")
    jv.do("type ls -la")
    assert jv.desktop.did("keys") == ["ctrl+shift+v"]


def test_dictation_types_what_you_say_until_stopped(jv):
    jv.desktop.add("Untitled - gedit", "gedit Gedit")
    jv.do("start dictation")
    jv.do("dear raj")
    jv.do("new line")
    jv.do("thanks for the update")
    jv.do("stop dictation")
    jv.do("what time is it")
    typed = [t for t in pasted(jv) if t]
    assert typed == ["dear raj ", "thanks for the update "]
    assert jv.desktop.did("keys") == ["ctrl+v", "Return", "ctrl+v"]
    assert jv.says("Dictation off.")


def test_typing_with_jarvis_in_front_asks_you_to_click_first(jv):
    jv.desktop.add("JARVIS", "jarvis Jarvis")
    jv.do("type hello")
    assert jv.said == ["Click where you want it typed first."]
    assert not jv.desktop.did("keys") and not pasted(jv)
