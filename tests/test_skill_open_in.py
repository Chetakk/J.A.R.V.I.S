"""Opening a folder or file in a named app (skills/dev.py open_in, skills/files.py place / open_folder):
"open the projects folder in vs code" opens that folder in VS Code, not just VS Code. Launches are recorded,
never made."""
import os
import shlex

import pytest

import conftest


@pytest.fixture
def launched(jv, monkeypatch):
    """[(app name, [paths])] that JARVIS launched."""
    out = []
    monkeypatch.setattr(jv.j.apps, "launch", lambda app, paths=(): out.append((app["name"], list(paths))) or True)
    return out


def need(jv, name):
    if not jv.j.apps.find(name):
        pytest.skip("%s isn't installed here" % name)


def test_projects_folder_in_vs_code(jv, launched):
    need(jv, "vs code")
    jv.do("Open Projects folder in VS Code")
    assert launched == [("Visual Studio Code", [os.path.join(conftest.HOME, "Projects")])]
    assert jv.says("Opening the Projects folder in Visual Studio Code")


def test_downloads_in_vs_code(jv, launched):
    need(jv, "vs code")
    jv.do("open the downloads folder in vs code")
    assert launched == [("Visual Studio Code", [os.path.join(conftest.HOME, "Downloads")])]


def test_a_file_in_an_editor(jv, launched):
    need(jv, "text editor")
    path = os.path.join(conftest.HOME, "Downloads", "todo-list.txt")
    with open(path, "w") as f:
        f.write("x")
    jv.do("open todo-list.txt in gedit")
    assert launched == [("Text Editor", [path])]


def test_a_folder_in_your_home_by_name(jv, launched):
    need(jv, "files")
    os.makedirs(os.path.join(conftest.HOME, "spike-test"))
    jv.do("open the spike test folder in files")
    assert launched == [("Files", [os.path.join(conftest.HOME, "spike-test")])]


def test_terminal_either_way_round(jv, launched):
    for said in ("open the projects folder in terminal", "open a terminal in the projects folder"):
        jv.clear()
        jv.do(said)
        ran = jv.shell.ran(r"^gnome-terminal ")
        assert ran and shlex.split(ran[0])[1] == "--working-directory=%s" % os.path.join(conftest.HOME, "Projects")
        assert jv.says("Opening a terminal in the Projects folder")
    assert launched == []


def test_project_in_claude(jv, launched, monkeypatch):
    os.makedirs(os.path.join(conftest.HOME, "Projects", "J.A.R.V.I.S"))
    jv.j.projects.refresh()
    cwd = []
    monkeypatch.setattr(jv.j.claude, "open_terminal", lambda **k: cwd.append(k.get("cwd")))
    jv.do("open jarvis in claude")
    assert cwd == [os.path.join(conftest.HOME, "Projects", "J.A.R.V.I.S")]


def test_websites_stay_the_browsers(jv, launched):
    jv.do("open youtube in chrome")
    assert launched == [] and jv.shell.ran(r"youtube\.com")


def test_not_a_folder_here_isnt_opened(jv, launched):
    need(jv, "vs code")
    jv.do("open the unicorn folder in vs code")
    assert launched == []


def test_bare_folder(jv, launched):
    jv.do("open projects folder")
    assert jv.shell.ran(r"^xdg-open %s$" % shlex.quote(os.path.join(conftest.HOME, "Projects")))


def test_music_folder_isnt_the_music_player(jv, launched):
    os.makedirs(os.path.join(conftest.HOME, "Music"))
    jv.do("open the music folder")
    assert jv.shell.ran(r"^xdg-open .*/Music$") and launched == []
