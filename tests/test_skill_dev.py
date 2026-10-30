"""Coding helpers (skills/dev.py): git status, opening a project in VS Code, yesterday's project (from the Day
Tracker's VS Code time, read only), running tests and project jobs through Claude Code. ("open X in Y" is in
tests/test_skill_open_in.py.)"""
import os
import time

import conftest

H = conftest.HOME


def project(jv, name, git=True):
    path = os.path.join(H, "Projects", name)
    os.makedirs(os.path.join(path, ".git") if git else path, exist_ok=True)
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("# %s\n\nA project.\n" % name)
    jv.j.projects.refresh()
    return path


def test_git_status_with_changes(jv):
    project(jv, "avtec-hs")
    jv.shell.on(r"^git -C .*avtec-hs status", out="## main...origin/main\n M app.py\n M ui.py\n?? notes.txt\n")
    jv.do("git status of avtec hs")
    assert jv.said == ["avtec-hs on branch main: 2 changed files and 1 new."]
    assert "M app.py" in jv.card_text()


def test_git_status_clean(jv):
    project(jv, "avtec-hs")
    jv.shell.on(r"^git -C .* status", out="## dev\n")
    jv.do("git status of avtec hs")
    assert jv.said == ["avtec-hs is clean, on branch dev."]


def test_open_a_project_in_vs_code(jv):
    path = project(jv, "avtec-hs")
    jv.do("open the avtec hs project")
    assert jv.shell.ran("^code %s$" % path)


def test_yesterdays_project_from_the_day_tracker(jv):
    project(jv, "avtec-hs")
    other = project(jv, "study-buddy")
    yday = (time.time() - 86400) * 1000
    jv.net.on(r"localhost:7313/api/state$", {"autoEntries": [
        {"start": yday, "end": yday + 40 * 60000, "label": "VS Code · study-buddy"},
        {"start": yday + 3600000, "end": yday + 3600000 + 10 * 60000, "label": "VS Code · avtec-hs"}]})
    jv.do("open the project I was working on yesterday")
    assert jv.shell.ran("^code %s$" % other)
    assert jv.says("You spent about 40 minutes on it yesterday")
    assert all(method == "GET" for method, _, _ in jv.net.calls)  # the tracker is only read


def test_run_the_tests_goes_to_claude_code(jv):
    path = project(jv, "avtec-hs")
    jv.do("run the tests in avtec hs")
    assert jv.claude.calls[-1][0] == "open_terminal" and jv.says("I've asked Claude to run the tests for avtec-hs")
    assert path


# --- failure cases ---

def test_not_a_git_project(jv):
    project(jv, "notes", git=False)
    jv.do("git status of notes")
    assert jv.said == ["notes isn't a git project."] and not jv.shell.ran(r"^git ")


def test_yesterdays_project_with_the_tracker_down_uses_the_last_changed_files(jv):
    project(jv, "avtec-hs")
    newest = project(jv, "study-buddy")
    os.utime(os.path.join(H, "Projects/avtec-hs/README.md"), (time.time() - 99999, time.time() - 99999))
    jv.do("open the project I was working on yesterday")  # :7313 doesn't answer
    assert jv.shell.ran("^code %s$" % newest)


def test_no_projects_at_all(jv):
    jv.do("open the project I was working on yesterday")
    assert not jv.shell.ran(r"^code ")


def test_an_unknown_project_is_handed_on(jv):
    project(jv, "avtec-hs")
    jv.do("open the zebra project")
    assert not jv.shell.ran(r"^code ")


# --- the focused window: "push", "git status" and Claude's fallback mean the project you're looking at ---

def focus_vscode(jv, name, other="main.py"):
    jv.desktop.add("%s — %s — Visual Studio Code" % (other, name), "code Code")


def test_git_status_without_a_name_is_the_focused_vs_code_project(jv):
    project(jv, "avtec-hs")
    focus_vscode(jv, "avtec-hs")
    jv.shell.on(r"^git -C .*avtec-hs status", out="## main...origin/main\n")
    jv.do("git status")
    assert jv.said == ["avtec-hs is clean, on branch main."]


def test_push_goes_to_the_focused_project_after_asking(jv):
    path = project(jv, "avtec-hs")
    focus_vscode(jv, "avtec-hs")
    jv.do("push", "yes")
    assert jv.shell.ran("^git -C %s push$" % path)


def test_push_declined_runs_nothing(jv):
    project(jv, "avtec-hs")
    focus_vscode(jv, "avtec-hs")
    jv.do("push", "no")
    assert not jv.shell.ran("push")


def test_push_with_no_project_in_focus_says_so(jv):
    jv.desktop.add("Document — LibreOffice Writer", "soffice Soffice")
    jv.j.projects.current = None
    jv.do("push")
    assert not jv.shell.ran("push") and jv.says("can't tell which project")


def test_terminal_title_with_a_projects_folder_counts(jv):
    path = project(jv, "avtec-hs")
    jv.desktop.add("biztel@jetson: %s/src" % path, "gnome-terminal-server Gnome-terminal")
    jv.do("which project is this")
    assert jv.says("You're in")


def test_claude_fallback_is_told_the_focused_window_and_project(jv):
    path = project(jv, "avtec-hs")
    focus_vscode(jv, "avtec-hs")
    jv.do("deploy this to staging")
    sent = jv.claude.asked("Focused window")
    assert sent and "avtec-hs" in sent[-1] and path in sent[-1]
