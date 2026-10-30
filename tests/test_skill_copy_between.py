"""Copying from one app into another (skills/copy_between.py): copied with the app's own key, pasted where the
other window's cursor is; nothing is sent or run, a multi-line paste into a terminal asks first, and when
nothing changed JARVIS says so. The text ends up on your clipboard either way."""
import pytest

from harness import Clock
from skills import copy_between, helpers

GEDIT = "gedit Gedit"
SLACK = "slack Slack"
TERM = "gnome-terminal-server Gnome-terminal"
CHROME = "google-chrome Google-chrome"


@pytest.fixture(autouse=True)
def fast(jv, monkeypatch):
    for module in (copy_between, helpers):
        monkeypatch.setattr(module, "time", Clock())
    jv.screen_changes = True


def pasted_into(jv):
    """(window title it was focused, then pasted with) for each paste."""
    return [k for k in jv.desktop.did("keys") if k in ("ctrl+v", "ctrl+shift+v")]


def test_copy_the_selection_into_slack(jv):
    jv.desktop.add("general | Slack", SLACK, front=False)
    jv.desktop.add("notes.txt - gedit", GEDIT, selected="the build is green")
    jv.do("copy this into slack")
    assert jv.desktop.did("keys")[0] == "ctrl+c"
    assert ("focus", "general | Slack") in jv.desktop.actions and pasted_into(jv) == ["ctrl+v"]
    assert jv.clipboard.text == "the build is green"
    assert jv.says("Done, I pasted it.") or jv.says("Done, it's in.")
    assert "Return" not in jv.desktop.did("keys")  # never sent


def test_send_says_it_wasnt_sent(jv):
    jv.desktop.add("general | Slack", SLACK, front=False)
    jv.desktop.add("notes.txt - gedit", GEDIT, selected="hi")
    jv.do("send this to slack")
    assert jv.says("Done, I pasted it. I haven't sent it.") and "Return" not in jv.desktop.did("keys")


def test_the_page_link_into_vs_code(jv):
    jv.desktop.add("main.py - Visual Studio Code", "code Code", front=False)
    jv.desktop.add("Docs - Google Chrome", CHROME, url="https://example.com/docs")
    jv.do("copy the link into vs code")
    assert jv.desktop.did("keys")[:3] == ["ctrl+l", "ctrl+c", "Escape"]
    assert jv.clipboard.text == "https://example.com/docs"


def test_paste_the_clipboard_into_a_terminal_strips_the_last_newline(jv):
    jv.clipboard.text = "git status\n"
    jv.desktop.add("biztelai@jetson: ~", TERM, front=False)
    jv.desktop.add("notes.txt - gedit", GEDIT)
    jv.do("paste my clipboard into the terminal")
    assert pasted_into(jv) == ["ctrl+shift+v"] and jv.clipboard.text == "git status"


def test_several_lines_into_a_terminal_ask_first(jv):
    jv.clipboard.text = "rm -rf build\nmake\nmake install"
    jv.desktop.add("biztelai@jetson: ~", TERM, front=False)
    jv.desktop.add("notes.txt - gedit", GEDIT)
    jv.do("paste my clipboard into the terminal", "no")
    assert jv.questions[0].startswith("That's 3 lines, and a terminal runs each one")
    assert not pasted_into(jv) and jv.says("It's on your clipboard.")


def test_summarise_and_put_it_in_gmail(jv):
    jv.desktop.add("Compose - Gmail - Google Chrome", CHROME, front=False)
    jv.desktop.add("report.txt - gedit", GEDIT, selected="a very long report " * 30)
    jv.claude.reply("Short summary.")
    jv.do("summarise this and paste it into gmail")
    assert jv.claude.calls[0][0] == "rewrite" and jv.clipboard.text == "Short summary."


def test_the_app_not_open_asks_before_opening(jv):
    jv.desktop.add("notes.txt - gedit", GEDIT, selected="hello")
    monkey = []
    jv.j.apps.launch = lambda app: monkey.append(app["name"]) or True
    jv.j.app_for = lambda pattern, text: ({"name": "Slack"}, None)
    jv.do("copy this into slack", "no")
    assert jv.questions == ["Slack isn't open. Open it and paste it there?"]
    assert not monkey and jv.clipboard.text == "hello" and jv.says("It's on your clipboard")


# --- failure cases ---

def test_nothing_selected_puts_your_clipboard_back(jv):
    jv.clipboard.text = "what you had"
    jv.desktop.add("general | Slack", SLACK, front=False)
    jv.desktop.add("notes.txt - gedit", GEDIT)  # nothing selected
    jv.do("copy this into slack")
    assert jv.said == ["Nothing's selected there. Select the text first, then ask me again."]
    assert jv.clipboard.text == "what you had" and not pasted_into(jv)


def test_a_link_from_something_that_isnt_a_browser(jv):
    jv.desktop.add("general | Slack", SLACK, front=False)
    jv.desktop.add("notes.txt - gedit", GEDIT)
    jv.do("copy the link into slack")
    assert jv.says("Links only come from a browser")


def test_empty_clipboard(jv):
    jv.desktop.add("general | Slack", SLACK)
    jv.do("paste my clipboard into slack")
    assert jv.said == ["Your clipboard is empty. Copy something first."]


def test_nothing_changing_after_the_paste_is_said(jv):
    jv.screen_changes = False
    jv.desktop.add("general | Slack", SLACK, front=False)
    jv.desktop.add("notes.txt - gedit", GEDIT, selected="hi")
    jv.do("copy this into slack")
    assert jv.says("I pasted it, but nothing changed in that window") and not jv.says("Done")


def test_claude_down_pastes_nothing(jv):
    jv.desktop.add("general | Slack", SLACK, front=False)
    jv.desktop.add("report.txt - gedit", GEDIT, selected="text")
    jv.claude.fail()
    jv.do("translate this to hindi and paste it into slack")
    assert jv.says("Sorry, that didn't work") and not pasted_into(jv)


def test_send_this_to_my_phone_isnt_a_window(jv):
    jv.desktop.add("notes.txt - gedit", GEDIT, selected="hi")
    jv.j.app_for = lambda pattern, text: (None, None)
    jv.do("send this to my phone")
    assert not pasted_into(jv)


def test_an_error_from_the_terminal_with_none_there(jv):
    jv.desktop.add("general | Slack", SLACK, front=False)
    jv.desktop.add("biztelai@jetson: ~", TERM, text="all good here, the build finished and every test passed fine")
    jv.do("copy the error from the terminal into slack")
    assert jv.said == ["I can't see an error in that window."]
