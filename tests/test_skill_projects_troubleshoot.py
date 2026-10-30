"""(A folder with no project.json is said as its folder name: "study-buddy".)

Your projects (skills/projects.py, system/projects.py: ~/Projects, biztel run) and "why isn't my app running"
(skills/troubleshoot.py: from the run state, its ports, its log and terminal, before any Claude)."""
import json
import os
import signal

import pytest

import conftest
from system import ports
from system import projects as sysprojects

H = conftest.HOME


def project(jv, name, readme="# %s\n\nA voice assistant for the Jetson.\n"):
    path = os.path.join(H, "Projects", name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write(readme % name if "%s" in readme else readme)
    jv.j.projects.refresh()
    return path


def running(jv, name, pid=60001, links=(("frontend", "http://localhost:5173"), ("backend", "http://localhost:8000")),
            services=((60002, "frontend"), (60003, "backend"))):
    """biztel run's state file for `name`, with its processes alive."""
    os.makedirs(sysprojects.RUNS, exist_ok=True)
    jv.add_process(pid, "node")
    for spid, sname in services:
        jv.add_process(spid, sname)
    with open(os.path.join(sysprojects.RUNS, name + ".json"), "w") as f:
        json.dump({"name": name, "pid": pid, "started": 0,
                   "links": [{"name": n, "url": u} for n, u in links],
                   "services": [{"name": n, "pid": p} for p, n in services]}, f)


def log(name, text):
    os.makedirs(sysprojects.RUNS, exist_ok=True)
    with open(os.path.join(sysprojects.RUNS, name + ".log"), "w") as f:
        f.write(text)


def test_about_a_project_from_its_readme(jv):
    project(jv, "study-buddy", "# Study Buddy\n\nFlashcards that quiz you by voice.\n")
    jv.do("what's the study buddy project about")
    assert jv.says("Flashcards that quiz you by voice") and not jv.claude.calls


def test_run_a_project(jv):
    project(jv, "study-buddy")
    jv.shell.on(r"biztel run .* --plan$", out="frontend: npm run dev\nbackend: python3 app.py")
    jv.do("run study buddy", wait=0.2)
    assert jv.says("Starting study-buddy") or jv.says("running study-buddy")
    assert jv.shell.ran(r"^gnome-terminal --title 'biztel run · study-buddy'")


def test_run_what_biztel_cant_run_says_how(jv):
    project(jv, "study-buddy")
    jv.shell.on(r"biztel run .* --plan$", out="nothing to run")
    jv.do("run study buddy")
    assert jv.says("I don't know how to run study-buddy yet") and not jv.shell.ran(r"^gnome-terminal")


def test_already_running(jv):
    project(jv, "study-buddy")
    running(jv, "study-buddy")
    jv.do("run study buddy")
    assert jv.says("already running") and not jv.shell.ran(r"--plan")


def test_whats_running_and_links(jv):
    project(jv, "study-buddy")
    running(jv, "study-buddy")
    jv.do("what's running")
    assert jv.says("study-buddy is running")
    jv.do("open study buddy in the browser")
    assert jv.shell.ran(r"^xdg-open http://localhost:5173$")


def test_stop_a_project(jv):
    project(jv, "study-buddy")
    running(jv, "study-buddy")
    jv.do("stop study buddy")
    assert (60001, signal.SIGTERM) in jv.signals and jv.says("study-buddy is stopped.")


def test_stop_all_asks_first(jv):
    project(jv, "study-buddy")
    running(jv, "study-buddy")
    jv.do("stop all projects")  # no answer
    assert jv.questions and not [s for s in jv.signals if s[1]]  # (signal 0 only checks it's alive)


def test_project_notes(jv):
    project(jv, "avtec-hs")
    jv.do("note for avtec hs: camera 2 needs recalibrating")
    jv.do("what's pending")
    assert jv.says("camera 2 needs recalibrating")


# --- failure cases ---

def test_stop_one_that_isnt_running(jv):
    project(jv, "study-buddy")
    jv.do("stop study buddy")
    assert jv.said == ["study-buddy isn't running."] and not [s for s in jv.signals if s[1]]


def test_start_the_timer_isnt_a_project(jv):
    project(jv, "study-buddy")
    jv.do("start the timer", "never mind")
    assert not jv.shell.ran(r"biztel")


def test_links_with_nothing_running(jv):
    project(jv, "study-buddy")
    jv.do("what are the links")
    assert jv.said == ["Nothing's running, so there are no links yet."]


def test_two_similar_projects_ask_which(jv):
    project(jv, "upe-station-a")
    project(jv, "upe-station-b")
    jv.do("run upe station")
    assert jv.said[0].startswith("Which one:") and not jv.shell.ran(r"--plan")


# --- troubleshooting ---

def test_never_started(jv):
    project(jv, "study-buddy")
    jv.do("why isn't study buddy working")
    assert jv.said == ["study-buddy isn't running, and I have no log of it. Say run study-buddy to start it."]
    assert not jv.claude.calls


def test_port_taken_names_who_has_it(jv, monkeypatch):
    project(jv, "study-buddy")
    log("study-buddy", "frontend │ Error: listen EADDRINUSE: address already in use :::5173\nfrontend │ exited (1)\n")
    jv.shell.on(r"^ss -ltnpH$", out='LISTEN 0 511 *:5173 *:* users:(("vite",pid=60050,fd=20))\n')
    monkeypatch.setattr(ports, "_cwd", lambda pid: os.path.join(H, "Projects/other-app"))
    jv.do("why did study buddy crash")
    assert jv.says("Its port 5173 is already taken: it's used by vite in ~/Projects/other-app")
    assert not jv.claude.calls


def test_stopped_with_an_error_says_the_error(jv):
    project(jv, "study-buddy")
    log("study-buddy", "backend │ Starting\nbackend │ ModuleNotFoundError: No module named 'flask'\nbackend │ exited (1)\n")
    jv.do("why isn't study buddy running")
    assert jv.says("its backend exited with code 1", "No module named 'flask'")


def test_running_fine_says_so(jv):
    project(jv, "study-buddy")
    running(jv, "study-buddy")
    jv.shell.on(r"^ss -ltnpH$", out='LISTEN 0 511 *:5173 *:* users:(("node",pid=60002,fd=20))\n')
    monkeypatch_cwd = os.path.join(H, "Projects/study-buddy")
    ports._cwd, old = (lambda pid: monkeypatch_cwd), ports._cwd
    try:
        jv.do("what's wrong with study buddy")
    finally:
        ports._cwd = old
    assert jv.says("study-buddy looks fine", "port 5173")


def test_unclear_goes_to_claude_with_the_facts(jv):
    project(jv, "study-buddy")
    log("study-buddy", "frontend │ compiled with 2 warnings\n")
    jv.do("why isn't study buddy loading")
    assert jv.claude.asked("Its log") and jv.claude.asked("compiled with 2 warnings")


def test_whats_using_a_port(jv, monkeypatch):
    jv.shell.on(r"^ss -ltnpH$", out='LISTEN 0 511 127.0.0.1:8178 0.0.0.0:* users:(("whisper-server",pid=60060,fd=3))\n')
    monkeypatch.setattr(ports, "_cwd", lambda pid: "")
    jv.do("what's using port 8178")
    assert jv.said == ["Port 8178 is used by whisper-server."]
    jv.do("is port 3000 free")
    assert jv.says("Nothing's using port 3000; it's free.")


def test_wifi_isnt_a_project(jv):
    project(jv, "study-buddy")
    jv.do("why isn't my wifi working")
    assert not jv.says("isn't running, and I have no log")
