"""Moving windows between workspaces: by number, word, ordinal, "next", and the Nth window on this workspace."""
import pytest


@pytest.fixture
def wins(jv):
    """Oldest opened first, all on workspace 1; the terminal is in front."""
    jv.desktop.add("Inbox - Gmail - Google Chrome", "google-chrome Google-chrome", front=False)
    jv.desktop.add("main.py - Visual Studio Code", "code Code", front=False)
    jv.desktop.add("J.A.R.V.I.S", "jarvis Jarvis", front=False)
    jv.desktop.add("Spotify", "spotify Spotify", front=False, workspace=2)
    jv.desktop.add("biztelai@jetson: ~", "gnome-terminal-server Gnome-terminal")
    return jv


@pytest.mark.parametrize("text, moved", [
    ("move 1st to next workspace", ("Inbox - Gmail - Google Chrome", 2)),
    ("move 2nd to next workspace", ("main.py - Visual Studio Code", 2)),  # JARVIS's own window isn't counted
    ("move the third window to the next workspace", ("biztelai@jetson: ~", 2)),  # Spotify is on workspace 2
    ("move this to next workspace", ("biztelai@jetson: ~", 2)),
    ("move to workspace 2", ("biztelai@jetson: ~", 2)),
    ("move to workspace three", ("biztelai@jetson: ~", 3)),
    ("move to the 2nd workspace", ("biztelai@jetson: ~", 2)),
    ("move to second workspace", ("biztelai@jetson: ~", 2)),
    ("move chrome to the 3rd workspace", ("Inbox - Gmail - Google Chrome", 3)),
    ("move chrome to workspace third", ("Inbox - Gmail - Google Chrome", 3)),
    ("move spotify to the next workspace", ("Spotify", 3)),  # next after the one it's on
    ("put chrome on workspace 3", ("Inbox - Gmail - Google Chrome", 3)),
    ("send chrome to workspace 3", ("Inbox - Gmail - Google Chrome", 3)),
    ("move the terminal to workspace 2", ("biztelai@jetson: ~", 2)),
])
def test_moves_the_window(wins, text, moved):
    wins.do(text)
    assert wins.desktop.did("to_workspace") == [moved]
    assert wins.said == ["Moved it to workspace %d." % moved[1]]
    assert not wins.claude.calls


def test_fourth_when_there_are_three_says_so(wins):
    wins.do("move forth to next workspace")
    assert not wins.desktop.did("to_workspace")
    assert wins.said == ["There are only 3 windows on this workspace."]
    assert not wins.claude.calls


def test_an_app_that_isnt_open_says_so(wins):
    wins.do("move slack to workspace 2")
    assert not wins.desktop.did("to_workspace")
    assert wins.said == ["I don't see slack open."]
    assert not wins.claude.calls


def test_previous_from_the_first_workspace_moves_nothing(wins):
    wins.do("move this to the previous workspace")
    assert not wins.desktop.did("to_workspace")
