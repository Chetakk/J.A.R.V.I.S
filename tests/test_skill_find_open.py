"""Find something, then open it where you want (skills/files.py listing / place / which, skills/dev.py open_in).

The chain this is here for: "what folders do we have in projects" -> "open that" -> "the first folder" ->
"open it in vs code". JARVIS keeps the list it just read out, so the next thing you say can point into it by
number instead of by name. Launches are recorded, never made.
"""
import os
import shlex

import pytest

import conftest
from skills import files


@pytest.fixture
def launched(jv, monkeypatch):
    """[(app name, [paths])] that JARVIS launched."""
    out = []
    monkeypatch.setattr(jv.j.apps, "launch", lambda app, paths=(): out.append((app["name"], list(paths))) or True)
    return out


@pytest.fixture
def temp(jv):
    """~/Projects/temp with CVRDE, zebra and a loose file in it, as in the real session."""
    base = os.path.join(conftest.HOME, "Projects", "temp")
    for name in ("CVRDE", "zebra"):
        os.makedirs(os.path.join(base, name))
    with open(os.path.join(base, "notes.txt"), "w") as f:
        f.write("x")
    files.remember_listing([])
    return base


def need(jv, name):
    if not jv.j.apps.find(name):
        pytest.skip("%s isn't installed here" % name)


# --- what's in a folder ---

def test_what_folders_are_in_projects(jv, temp):
    os.makedirs(os.path.join(conftest.HOME, "Projects", "J.A.R.V.I.S"))
    jv.do("what folders do we have in projects")
    assert jv.says("2 folders", "J.A.R.V.I.S", "temp")
    assert "J.A.R.V.I.S" in jv.card_text()


def test_whats_in_a_folder_lists_folders_then_files(jv, temp):
    jv.do("what's in the temp folder")
    assert jv.says("3 things", "CVRDE", "zebra")
    assert files.recent_listing() == [os.path.join(temp, "CVRDE"), os.path.join(temp, "zebra"),
                                      os.path.join(temp, "notes.txt")]


def test_only_the_files_when_you_ask_for_files(jv, temp):
    jv.do("list the files in the temp folder")
    assert files.recent_listing() == [os.path.join(temp, "notes.txt")]


def test_an_empty_folder_says_so(jv):
    os.makedirs(os.path.join(conftest.HOME, "Projects", "nothing-here"))
    jv.do("what's in the nothing-here folder")
    assert jv.says("nothing in")


def test_a_menu_isnt_a_folder(jv):
    """"what's in the view menu" is a window's menu: it goes on to the controls skill."""
    assert not jv.says("nothing in") and jv.do("what's in the view menu") is not None


# --- pointing into the list ---

def test_the_first_folder_is_one_of_the_ones_just_listed(jv, temp):
    jv.do("what's in the temp folder")
    jv.clear()
    jv.do("open the first folder")
    assert jv.shell.ran(r"^xdg-open %s$" % shlex.quote(os.path.join(temp, "CVRDE")))


def test_opening_a_folder_makes_what_is_inside_it_the_list(jv, temp):
    """The real session: open temp, then "the first folder" means CVRDE, inside it."""
    jv.do("open the temp folder")
    jv.clear()
    jv.do("open the first folder in vs code")
    need(jv, "vs code")
    assert jv.says("Opening the CVRDE folder in Visual Studio Code")


def test_the_second_one_in_vs_code(jv, temp, launched):
    need(jv, "vs code")
    jv.do("what's in the temp folder")
    jv.clear()
    jv.do("open the second one in vs code")
    assert launched == [("Visual Studio Code", [os.path.join(temp, "zebra")])]


def test_the_last_one(jv, temp, launched):
    jv.do("what folders are in the temp folder")
    jv.clear()
    jv.do("open the last one")
    assert jv.shell.ran(r"^xdg-open %s$" % shlex.quote(os.path.join(temp, "zebra")))


def test_a_file_from_the_list_in_the_default_app(jv, temp):
    jv.do("what's in the temp folder")
    jv.clear()
    jv.do("open the third one in the default app")
    assert jv.shell.ran(r"^xdg-open %s$" % shlex.quote(os.path.join(temp, "notes.txt")))


def test_asking_for_a_folder_skips_the_files_in_the_list(jv, temp):
    """"the third folder" when only two of the three things listed are folders."""
    jv.do("what's in the temp folder")
    jv.clear()
    jv.do("open the third folder")
    assert not jv.shell.ran(r"notes\.txt")


def test_the_list_only_counts_while_it_is_fresh(jv, temp):
    jv.do("what's in the temp folder")
    files.listing["at"] = 0  # a while later
    jv.clear()
    jv.do("open the first folder")
    assert not jv.shell.ran(r"^xdg-open")


def test_an_ordinal_with_no_list_is_not_a_folder(jv):
    files.remember_listing([])
    jv.clear()
    jv.do("open the first folder")
    assert not jv.shell.ran(r"^xdg-open")


# --- find a file, then open it where you want ---

def test_a_found_file_in_an_editor(jv, launched):
    need(jv, "text editor")
    path = os.path.join(conftest.HOME, "Documents", "quarter-plan.txt")
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("x")
    jv.do("find my quarter plan file", "yes")
    jv.clear()
    jv.do("open it in gedit")
    assert launched == [("Text Editor", [path])]


def test_the_second_file_found_in_the_default_app(jv):
    docs = os.path.join(conftest.HOME, "Documents")
    os.makedirs(docs)
    for name in ("report-a.pdf", "report-b.pdf"):
        with open(os.path.join(docs, name), "w") as f:
            f.write("x")
    jv.do("find pdfs in documents")
    jv.clear()
    jv.do("open the second one in the default app")
    assert jv.shell.ran(r"^xdg-open .*report-")
