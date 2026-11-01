"""Every card JARVIS can show on its own comes up with Claude out of reach: Claude's every call fails here, and each
test checks the card is there and Claude was never asked. (Cards that are Claude's own answers, like a document
summary or a task plan, aren't here; skills/web.py's and skills/lookup.py's Claude cards are tested in their files.)"""
import json
import os
import time

import pytest

import conftest
from claude.bridge import ClaudeError
from harness import Hub
from skills import layouts, slow_computer
from system import accessibility, packages, ports
from test_skill_controls_frozen_slow import fast, with_controls  # noqa: F401 (fixture)
from test_skill_display_focus_dictation import screen  # noqa: F401 (fixture)
from test_skill_notifications_briefing import notes  # noqa: F401 (fixture)
from test_skill_projects_troubleshoot import log, project, running
from test_skill_status_checks_clipboard import healthy  # noqa: F401 (fixture)

H = conftest.HOME


@pytest.fixture(autouse=True)
def no_claude(jv):
    def down(default):
        raise ClaudeError("Claude is off in this test")
    jv.claude._next = down
    yield
    for stop in list(jv.j.cards.timers.values()):
        stop.set()


def shown(jv, title):
    """The card titled `title` came up, and Claude wasn't asked for anything."""
    titles = [c.get("title") for c in jv.cards]
    assert title in titles, "no %r card; shown: %s, said: %s" % (title, titles, jv.said)
    assert not jv.claude.calls, "Claude was asked: %s" % jv.claude.calls
    return next(c for c in jv.cards if c.get("title") == title)


def make(rel, text="x", when=None):
    path = os.path.join(H, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if when:
        os.utime(path, (when, when))
    return path


# --- timers, live cards, the dashboard, tasks ---

def test_dashboard(jv):
    jv.do("show my dashboard")
    assert "**Running**" in shown(jv, "Dashboard")["text"]


def test_dashboard_with_a_project_running(jv):
    project(jv, "study-buddy")
    running(jv, "study-buddy")
    jv.do("what are you working on")
    assert "study" in shown(jv, "Dashboard")["text"].lower()


def test_timer_done(jv):
    jv.do("set a timer for 1 second")
    time.sleep(1.6)
    shown(jv, "Timer")
    assert jv.cards[-1]["text"].startswith("# Done")


def test_live_card(jv):
    jv.j.show({"title": "Uptime", "text": "{output}", "refresh": {"command": "uptime", "every": 2, "for": 3}})
    time.sleep(0.5)
    assert shown(jv, "Uptime")


def test_recent_tasks(jv):
    from core import tasks
    os.makedirs(os.path.dirname(tasks.HISTORY_FILE), exist_ok=True)
    with open(tasks.HISTORY_FILE, "w") as f:
        json.dump([{"title": "tidy my downloads", "state": "done", "at": time.time(), "request": "tidy my downloads",
                    "steps": [{"do": "tidy my downloads", "status": tasks.DONE}]}], f)
    jv.do("what tasks did you do")
    assert "tidy my downloads" in shown(jv, "Recent tasks")["text"]


# --- your day ---

def test_briefing(jv):
    Hub(jv.net, todos=[{"id": "a", "text": "Buy milk", "done": False, "created": 1}])
    jv.do("brief me")
    assert any(c["title"].startswith("Briefing: ") for c in jv.cards) and not jv.claude.calls


def test_where_you_left_off(jv):
    jv.do("what's 12 times 7")
    jv.clear()
    jv.do("where was i")
    assert "12 times 7" in shown(jv, "Where you left off")["text"]


def test_everything_about_a_project(jv):
    project(jv, "study-buddy", "# Study Buddy\n\nFlashcards that quiz you by voice.\n")
    jv.do("everything about study buddy")
    assert any(c["title"].startswith("Everything: ") for c in jv.cards) and not jv.claude.calls


def test_recent_alerts(jv):
    jv.j.settings["quiet_hours"] = {"enabled": False}
    jv.j.skills.proactive.tell("cpu", "Heads up, CPU busy.", "CPU over 90.")
    jv.do("show my recent alerts")
    assert shown(jv, "What I brought up today")


# --- notes and memory ---

def test_your_notes(jv):
    jv.do("remember that the spare key is under the mat")
    jv.do("what did I ask you to remember")
    assert "spare key" in shown(jv, "Your notes")["text"]


def test_what_i_know_about_you(jv):
    jv.do("remember that the spare key is under the mat")
    jv.do("what do you know about me")
    assert shown(jv, "What I know about you")["choices"]


def test_a_secret_shows_then_hides(jv):
    jv.do("remember my wifi password is blue-ocean-42")
    jv.clear()
    jv.do("what's my wifi password")
    card = next((c for c in jv.cards if c.get("id") == "secret"), None)
    assert card and "blue-ocean-42" in card["text"] and not jv.claude.calls, (jv.said, jv.cards)


# --- files ---

def test_whats_in_a_folder(jv):
    make("Documents/lease.pdf")
    make("Documents/Taxes/2025.pdf")
    jv.do("what's in the documents folder")
    assert shown(jv, "In ~/Documents")["choices"]


def test_files_changed_today(jv):
    make("Documents/notes.md", when=time.time() - 60)
    jv.do("what files did I change today")
    assert any(c["title"].startswith("Files you changed") for c in jv.cards) and not jv.claude.calls, jv.said


def test_find_a_file(jv):
    make("Documents/Resume-2026.pdf")
    make("Documents/archive/resume-old.pdf")
    jv.do("find my resume", "no")
    assert any(c["title"].startswith("Found ") for c in jv.cards) and not jv.claude.calls, jv.said


def test_tidy_plan(jv):
    make("Downloads/cat.png")
    make("Downloads/paper.pdf")
    jv.do("tidy my downloads", "no")
    assert shown(jv, "Tidy Downloads")


def test_duplicates(jv):
    make("Downloads/photo.jpg", "same")
    make("Downloads/photo (1).jpg", "same")
    jv.do("find duplicate files in downloads", "no")
    assert any(c["title"].startswith("Duplicates in") for c in jv.cards) and not jv.claude.calls, jv.said


# --- the computer ---

def test_status(jv, healthy):  # noqa: F811
    jv.do("status")
    assert shown(jv, "JARVIS status")


def test_system_usage(jv):
    jv.files["/proc/meminfo"] = "MemTotal:       8000000 kB\nMemAvailable:    3000000 kB\n"
    jv.do("show the system usage")
    assert shown(jv, "System usage")


def test_whats_slowing_the_computer(jv, fast, monkeypatch):  # noqa: F811
    monkeypatch.setattr(slow_computer, "_cpu_ticks", jv.proc_ticks)
    jv.files["/proc/meminfo"] = "MemAvailable:    6000000 kB\n"
    jv.add_process(50030, "chrome", cpu=80, rss_mb=1800)
    jv.do("what's slowing my computer", "no")
    assert shown(jv, "What's using the computer")


def test_screens(jv, screen):  # noqa: F811
    jv.do("what's my screen resolution")
    assert "1920x1080" in shown(jv, "Screens")["text"]


def test_ports(jv, monkeypatch):
    monkeypatch.setattr(ports, "listening", lambda: [ports.Listener(5173, "127.0.0.1", "node", 60001)])
    jv.do("what ports are open")
    assert "5173" in shown(jv, "Ports in use")["text"]


def test_a_projects_log(jv):
    project(jv, "study-buddy")
    log("study-buddy", "backend │ ModuleNotFoundError: No module named 'flask'\nbackend │ exited (1)\n")
    jv.do("why isn't study buddy working")
    assert any(c["title"].endswith(": log") for c in jv.cards) and not jv.claude.calls, jv.said


def test_layouts(jv):
    os.makedirs(os.path.dirname(layouts.SAVED), exist_ok=True)
    with open(layouts.SAVED, "w") as f:
        json.dump({"coding": {"windows": [{"app": "code", "place": "left"}, {"app": "chrome", "place": "right"}]}}, f)
    jv.do("list my layouts")
    assert "coding" in shown(jv, "Window layouts")["text"]


def test_install(jv, monkeypatch):
    monkeypatch.setattr(packages, "installed", lambda pkg: None)
    monkeypatch.setattr(packages, "info", lambda pkg: {"name": "htop", "version": "3.0.5", "summary": "process viewer"})
    jv.do("install htop")
    assert "sudo apt install htop" in shown(jv, "Install htop")["text"]


def test_install_with_a_choice(jv, monkeypatch):
    monkeypatch.setattr(packages, "installed", lambda pkg: None)
    monkeypatch.setattr(packages, "info", lambda pkg: None)
    monkeypatch.setattr(packages, "search", lambda words, limit=3: [("evince", "document viewer"),
                                                                    ("okular", "universal document viewer")])
    jv.do("install a pdf viewer")
    assert any(c["title"].startswith("Packages for") for c in jv.cards) and not jv.claude.calls, jv.said


def test_notifications(jv, notes):  # noqa: F811
    notes.add("Slack", "Raj", "lunch at 1?")
    jv.do("read my notifications")
    assert any("otifications" in c["title"] for c in jv.cards) and not jv.claude.calls, jv.said


def test_what_can_i_click(jv, monkeypatch):
    jv.desktop.add("Files", "org.gnome.Nautilus Org.gnome.Nautilus", pid=500)
    found = [accessibility.Control("Back", "push button", "", True, None),
             accessibility.Control("Show Hidden Files", "menu item", "View", False, None)]
    monkeypatch.setattr(accessibility, "controls", lambda pid, title="", seconds=2.0, limit=300: found)
    jv.do("what can I click here")
    assert shown(jv, "What you can click in Files")


def test_a_menu(jv, monkeypatch):
    jv.desktop.add("Files", "org.gnome.Nautilus Org.gnome.Nautilus", pid=500)
    found = [accessibility.Control("Show Hidden Files", "menu item", "View", False, None)]
    monkeypatch.setattr(accessibility, "controls", lambda pid, title="", seconds=2.0, limit=300: found)
    jv.do("what's in the view menu")
    assert shown(jv, "View menu")


# --- Study Buddy (its API faked) ---

def test_study_suggestion(jv):
    jv.net.on(r"7314/api/suggestions", [{"text": "12 flashcards are due.", "href": "#/review", "action": "Review"}])
    jv.do("what should i do in study buddy")
    assert shown(jv, "Study Buddy")


def test_study_progress(jv):
    jv.net.on(r"7314/api/groups", [{"name": "Graphs", "total": 10, "done": 4, "durationS": 0, "watchedS": 0}])
    jv.net.on(r"7314/api/today", {"studiedTodayS": 2700, "streak": 6})
    jv.do("how far am i overall")
    assert "Graphs: 40%" in shown(jv, "Study progress")["text"]


def test_show_my_password_without_me(jv):
    jv.do("remember my wifi password is blue-ocean-42")
    jv.clear()
    jv.do("show my wifi password")
    assert any(c.get("id") == "secret" for c in jv.cards) and not jv.claude.calls, jv.said
