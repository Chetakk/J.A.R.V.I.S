"""Browser tabs (skills/browser_tabs.py: key presses in the browser), the screen (skills/screen.py: the window's own
text when the app shares it, else a screenshot for Claude) and window layouts (skills/layouts.py: arranged, then
checked)."""
import json
import os

import pytest

from harness import Clock
from skills import browser_tabs, layouts

CHROME = "google-chrome Google-chrome"
TERM = "gnome-terminal-server Gnome-terminal"
CODE = "code Code"


@pytest.fixture
def fast(monkeypatch):
    for module in (browser_tabs, layouts):
        monkeypatch.setattr(module, "time", Clock())


# --- tabs ---

@pytest.mark.parametrize("text, keys", [
    ("close this tab", "ctrl+w"), ("new tab", "ctrl+t"), ("reopen the last tab", "ctrl+shift+t"),
    ("next tab", "ctrl+Tab"), ("go back", "alt+Left"), ("reload the page", "F5"), ("scroll down", "Page_Down"),
    ("zoom in", "ctrl+plus"), ("reset zoom", "ctrl+0"),
])
def test_tab_keys_go_to_the_browser(jv, fast, text, keys):
    jv.desktop.add("Inbox - Gmail - Google Chrome", CHROME)
    jv.do(text)
    assert jv.desktop.did("keys") == [keys]


def test_the_browser_is_brought_to_the_front_first(jv, fast):
    jv.desktop.add("Inbox - Gmail - Google Chrome", CHROME)
    jv.desktop.add("biztelai@jetson: ~", TERM)  # in front
    jv.do("close this tab")
    assert jv.desktop.actions[0] == ("focus", "Inbox - Gmail - Google Chrome")
    assert jv.desktop.did("keys") == ["ctrl+w"]  # never sent to the terminal (ctrl+w deletes a word there)


def test_go_to_a_tab_by_name(jv, fast, monkeypatch):
    chrome = jv.desktop.add("Inbox - Gmail - Google Chrome", CHROME)
    titles = iter(["Pull requests - GitHub - Google Chrome", "lofi - YouTube - Google Chrome"])

    def keys(combo):  # each ctrl+Tab shows the next tab's title
        jv.desktop.actions.append(("keys", combo))
        chrome.title = next(titles)
    from system import desktop
    monkeypatch.setattr(desktop, "keys", keys)
    jv.do("switch to the youtube tab")
    assert jv.desktop.did("keys") == ["ctrl+Tab", "ctrl+Tab"]
    assert jv.says("Here it is.") or jv.says("Switched.")


# --- failure cases ---

def test_no_browser_open(jv, fast):
    jv.desktop.add("biztelai@jetson: ~", TERM)
    jv.do("close this tab")
    assert jv.said == ["I don't see a browser open."]
    assert not jv.desktop.did("keys")


def test_a_tab_that_isnt_there_stops_after_one_round(jv, fast, monkeypatch):
    chrome = jv.desktop.add("Inbox - Gmail - Google Chrome", CHROME)
    titles = iter(["GitHub - Google Chrome", "Inbox - Gmail - Google Chrome"] * 30)

    def keys(combo):
        jv.desktop.actions.append(("keys", combo))
        chrome.title = next(titles)
    from system import desktop
    monkeypatch.setattr(desktop, "keys", keys)
    jv.do("switch to the netflix tab")
    assert jv.said == ["I couldn't find a netflix tab."]
    assert len(jv.desktop.did("keys")) == 2  # back where it started: not 40 presses


# --- screen ---

def test_read_a_terminal_aloud_from_its_own_text(jv):
    jv.desktop.add("biztelai@jetson: ~", TERM, pid=4242, text="$ make\nBuild finished with 0 errors and 2 warnings")
    jv.do("read this window aloud")
    assert jv.said == ["$ make\nBuild finished with 0 errors and 2 warnings"]
    assert not jv.claude.calls and not jv.screenshots


def test_a_long_terminal_says_its_end_and_puts_the_rest_on_a_card(jv):
    text = "\n".join("line %d of the build output" % i for i in range(200))
    jv.desktop.add("build", TERM, pid=1, text=text)
    jv.do("read the terminal out loud")
    assert jv.said[0].startswith("The last bit says:") and "line 199" in jv.said[0]
    assert "line 0 of" in jv.card_text()


def test_an_app_without_text_is_read_from_a_screenshot(jv):
    jv.desktop.add("main.py - J.A.R.V.I.S - Visual Studio Code", CODE, pid=7)  # VS Code shares no text
    jv.do("read this window aloud")
    assert jv.screenshots and jv.claude.asked("Read out the main text")


def test_whats_on_my_screen_sends_a_screenshot(jv):
    jv.claude.reply("You're looking at a terminal with a build log.")
    jv.do("what's on my screen")
    assert len(jv.screenshots) == 1
    assert jv.says("terminal with a build log")


def test_jarvis_own_window_in_front_reads_the_one_under_it(jv):
    jv.desktop.add("biztelai@jetson: ~", TERM, pid=3, text="error: permission denied while opening /dev/video0 for the camera")
    jv.desktop.add("JARVIS", "jarvis Jarvis")
    jv.do("read this window aloud")
    assert jv.says("permission denied")


def test_no_window_to_read(jv):
    jv.do("read this window aloud")
    assert jv.said == ["I can't see a window to read."]


def test_screenshot_failing_is_said(jv):
    jv.shell.on(r"^gnome-screenshot ", raises=FileNotFoundError)
    jv.do("what's on my screen")
    assert jv.says("didn't work")
    assert not jv.claude.calls


# --- layouts ---

def arranged(jv):
    return {w.title: (w.workspace, w.geometry) for w in jv.desktop.wins}


def test_arrange_coding_windows(jv, fast):
    jv.desktop.add("main.py - Visual Studio Code", CODE, workspace=2)
    jv.desktop.add("biztelai@jetson: ~", TERM, workspace=3)
    jv.do("arrange my coding windows")  # coding has no workspace of its own: the one you're on (1)
    assert jv.says("coding")
    assert not jv.says("but")
    got = arranged(jv)
    assert got["main.py - Visual Studio Code"] == (1, (0, 0, 960, 1080))
    assert got["biztelai@jetson: ~"] == (1, (960, 0, 960, 1080))


def test_a_missing_app_is_opened(jv, fast):
    jv.desktop.add("main.py - Visual Studio Code", CODE)
    jv.shell.on(r"^gnome-terminal$", out=lambda line: jv.desktop.add("Terminal", TERM, front=False) and "")
    jv.do("coding layout")
    assert jv.shell.ran(r"^gnome-terminal$")
    assert jv.says("coding")
    assert not jv.says("couldn't find")


def test_save_list_and_use_your_own_layout(jv, fast):
    jv.desktop.add("Docs - Google Chrome", CHROME, geometry=(0, 0, 960, 1080))
    jv.desktop.add("notes.md - gedit", "gedit Gedit", geometry=(960, 0, 960, 1080))
    jv.do("save this layout as reading")
    assert jv.said == ["Saved 2 windows as your reading layout. Say reading layout to bring it back."]
    with open(layouts.SAVED) as f:
        assert [w["place"] for w in json.load(f)["reading"]["windows"]] == ["left", "right"]
    jv.do("list my layouts")
    assert jv.says("coding and reading") or jv.says("reading")


def test_deleting_a_saved_layout_needs_a_yes(jv, fast):
    jv.desktop.add("Docs - Google Chrome", CHROME)
    jv.do("save this layout as reading")
    jv.do("delete the reading layout")  # no answer
    assert "reading" in json.load(open(layouts.SAVED))
    jv.do("delete the reading layout", "yes")
    assert "reading" not in json.load(open(layouts.SAVED))


# --- failure cases ---

def test_an_app_that_never_opens_is_named_not_hidden(jv, fast):
    jv.desktop.add("main.py - Visual Studio Code", CODE)
    jv.do("arrange my coding windows")  # the terminal is launched but no window ever shows
    assert jv.says("I arranged your coding windows, but", "couldn't find terminal")


def test_a_window_that_wont_move_is_reported(jv, fast):
    with open(layouts.SAVED, "w") as f:
        json.dump({"study": {"workspace": 2, "windows": [{"app": "chrome", "place": "left"},
                                                          {"app": "gedit", "place": "right"}]}}, f)
    chrome = jv.desktop.add("Docs - Google Chrome", CHROME, workspace=1)
    jv.desktop.add("notes.md - gedit", "gedit Gedit", workspace=1)
    jv.desktop.stuck.add(chrome.id)
    jv.do("study layout")
    assert jv.said == ["I arranged your study windows, but Chrome stayed on workspace 1."]


def test_unknown_layout_says_which_there_are(jv, fast):
    jv.do("arrange my gaming windows")
    assert jv.said == ["I don't have a gaming layout. I know coding. Arrange the windows and say: "
                       "save this layout as gaming."]


def test_save_with_nothing_on_screen(jv, fast):
    jv.do("save this layout as empty")
    assert jv.said == ["There are no windows on this workspace to save."]
    assert not os.path.exists(layouts.SAVED)


def test_the_config_layout_cant_be_deleted_by_voice(jv, fast):
    jv.do("delete the coding layout")
    assert jv.said == ["That one's in config/layouts.yaml, so it stays. You can edit it there."]
