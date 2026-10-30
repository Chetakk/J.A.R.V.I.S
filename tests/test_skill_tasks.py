"""Multi-step tasks (skills/tasks.py, core/tasks.py): Claude's plan runs one step at a time, each step checked
and reported as done, failed or not checked; risky steps you didn't ask for are asked about first; a safe step
that failed is tried once more, then you choose; Claude can never have a task turn JARVIS itself off."""
import json

import pytest

from core import tasks
from harness import Clock
from skills import tasks as task_skill

TERM = "gnome-terminal-server Gnome-terminal"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(task_skill, "time", Clock())


def plan(*steps, title="Get set up", say="Okay, a few steps."):
    return {"say": say, "task": {"title": title, "steps": list(steps)}}


def pump(jv, turns=20):
    """The main loop: each ("task", id) runs the next step; ("say", line) is spoken. Returns what was said."""
    said = []
    for _ in range(turns):
        if jv.j.inbox.empty():
            break
        kind, value = jv.j.inbox.get_nowait()
        if kind == "task":
            jv.j.skills.tasks.step(value)
            jv.settle(wait=2)
        elif kind == "say":
            said.append(value)
    return said


def last_task():
    return (tasks.past() or [None])[-1]


def opens_terminal(jv):
    jv.shell.on(r"^gnome-terminal$", out=lambda line: jv.desktop.add("Terminal", TERM) and "")


def test_safe_steps_run_and_each_is_checked(jv):
    opens_terminal(jv)
    jv.shell.on(r"^df -h /$", out="/dev/nvme0n1p1  57G  48G  9G  85% /")
    jv.claude.reply(plan({"do": "open terminal"}, {"run": "df -h /"}))
    jv.do("prepare things for my demo")
    pump(jv)
    t = last_task()
    assert [s["status"] for s in t["steps"]] == ["done", "done"] and t["state"] == "done"
    assert t["steps"][0]["result"] == "a window opened: Terminal"
    assert jv.cards[0]["title"] == "Task: Get set up"


def test_a_step_nothing_showed_for_is_not_called_done(jv):
    jv.claude.reply(plan({"do": "remember that the demo is on friday"}, {"do": "what's 2 plus 2"}))
    jv.do("prepare things for my demo")
    pump(jv)
    assert [s["status"] for s in last_task()["steps"]] == ["unchecked", "unchecked"]
    assert jv.says("though I couldn't check them")


def test_risky_steps_you_didnt_ask_for_ask_first(jv):
    jv.claude.reply(plan({"do": "open terminal"}, {"do": "delete old.txt"}))
    jv.do("prepare things for my demo", "no")
    assert jv.questions[0].startswith("That's 2 steps, on your screen. It will delete old.txt.")
    assert not jv.shell.ran(r"gnome-terminal") and last_task()["state"] == "stopped"
    assert jv.says("Okay, I won't.") or jv.says("cancelled")


def test_a_risky_step_you_asked_for_yourself_isnt_asked_again(jv):
    jv.claude.reply(plan({"do": "what's 2 plus 2"}, {"do": "delete old.txt"}))
    jv.do("work out a sum, then delete old.txt please, thanks")
    assert jv.claude.calls and not [q for q in jv.questions if q.startswith("That's 2 steps")]


def test_a_failed_safe_step_is_tried_once_more_then_you_choose(jv):
    jv.shell.missing.add("firefox")
    jv.claude.reply(plan({"do": "open firefox"}, {"do": "what's 2 plus 2"}))
    jv.do("prepare things for my demo", "skip it")
    pump(jv)
    s = last_task()["steps"]
    assert s[0]["tries"] == 2 and s[0]["status"] == "skipped" and s[1]["status"] == "unchecked"
    assert any(q.startswith("Step 1, open firefox, didn't work") for q in jv.questions)
    assert jv.says("Done, apart from step 1")


def test_no_answer_when_stuck_pauses_and_continue_picks_it_up(jv):
    jv.shell.missing.add("firefox")
    jv.claude.reply(plan({"do": "open firefox"}, {"do": "what's 2 plus 2"}))
    jv.do("prepare things for my demo")
    pump(jv)
    assert jv.j.skills.tasks.task["state"] == "paused"
    assert jv.says("I've paused the task")
    jv.shell.missing.discard("firefox")
    jv.do("how's the task going")
    assert jv.says("It's paused at step 1 of 2")
    jv.do("continue the task")
    pump(jv)
    assert last_task()["state"] == "done"


def test_a_manual_command_step_is_left_to_you_and_pauses(jv):
    jv.claude.reply(plan({"run": "df -h /"}, {"run": "rm -rf ~/build"}, {"do": "what's 2 plus 2"}))
    jv.do("prepare things for my demo", "yes")
    pump(jv)
    assert not jv.shell.ran(r"^rm ") and jv.manual[0][0] == "rm -rf ~/build"
    assert jv.j.skills.tasks.task["state"] == "paused"
    assert jv.says("I've paused the task there")


def test_stop_the_task(jv):
    jv.claude.reply(plan({"do": "what's 2 plus 2"}, {"do": "what's 3 plus 3"}, {"do": "what's 4 plus 4"}))
    jv.do("prepare things for my demo")
    jv.do("stop the task")
    assert jv.says("Okay, stopped.") and last_task()["state"] == "stopped"
    assert pump(jv) == [] and not jv.says("4 plus 4") 


def test_a_task_survives_a_restart(jv):
    jv.claude.reply(plan({"do": "what's 2 plus 2"}, {"do": "what's 3 plus 3"}))
    jv.do("prepare things for my demo")
    jv.do("pause the task")
    again = tasks.load()
    assert again and again["state"] == "paused" and again["request"] == "prepare things for my demo"


def test_a_running_step_cut_off_by_a_restart_runs_again_only_if_safe(jv):
    jv.claude.reply(plan({"do": "what's 2 plus 2"}, {"do": "what's 3 plus 3"}))
    jv.do("prepare things for my demo")
    t = jv.j.skills.tasks.task
    t["steps"][1]["status"], t["state"] = "running", "running"
    tasks.save(t)
    back = tasks.load()
    assert back["state"] == "paused" and back["steps"][1]["status"] == "pending"


# --- failure cases ---

@pytest.mark.parametrize("phrase", ["restart yourself", "close jarvis", "forget everything"])
def test_claude_cant_have_a_task_turn_jarvis_off_or_wipe_it(jv, phrase):
    jv.claude.reply(plan({"do": "what's 2 plus 2"}, {"do": phrase}))
    jv.do("prepare things for my demo", "yes")
    pump(jv)
    assert jv.j.exit_code == 0
    assert jv.j.memory.data.get("history") is not None
    s = last_task() and last_task()["steps"][-1]
    assert not s or s["status"] != "done"


def test_a_step_jarvis_doesnt_understand_is_a_failure_not_done(jv):
    jv.claude.reply(plan({"do": "what's 2 plus 2"}, {"do": "frobnicate the quantum flux capacitor"}))
    jv.do("prepare things for my demo", "stop")
    pump(jv)
    assert last_task()["steps"][1]["status"] == "failed"


def test_continue_with_no_task(jv):
    jv.do("continue the task")
    assert jv.said == ["There's no task to continue."]


def test_a_big_plan_is_offered_to_claude_code(jv):
    steps = [{"do": "what's %d plus 1" % i} for i in range(14)]
    jv.claude.reply(plan(*steps))
    jv.do("prepare things for my demo", "yes")
    assert jv.questions[0].startswith("That's a big job") or jv.questions[0].startswith("That's a lot of steps")
    assert jv.claude.calls[-1][0] == "open_terminal"
