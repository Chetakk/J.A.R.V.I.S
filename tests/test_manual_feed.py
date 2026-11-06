"""The manual terminal types commands at the prompt; it must never press Enter or Tab for the user."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from manual_feed import at_prompt, keys  # noqa: E402


def test_one_line_command_gets_reason_as_comment():
    assert keys("ls -la", "risky") == "\x05\x15ls -la  # ⚠ risky".encode()


def test_newlines_and_tabs_are_inserted_not_pressed():
    k = keys("cat <<EOF\nhi\tx\nEOF", "writes")
    assert k.startswith("\x05\x15# ⚠ writes\x16\ncat".encode())
    assert b"\n" not in k.replace(b"\x16\n", b"") and b"\t" not in k.replace(b"\x16\t", b"")


def test_at_prompt_reads_proc():
    assert at_prompt(os.getpid()) in (True, False)


def _mark(s):
    return b"\x1b]7777;" + s.encode() + b"\x07"


def test_results_parse_command_exit_and_clean_output():
    import base64
    b64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731
    log = (b"\x1b[1mprompt junk" + _mark("start;%s;%s" % (b64("ls /x"), b64("/home"))) + b"\r\n\x1b[31mls: no\x1b[0m\r\n"
           + _mark("end;2") + b"more prompt" + _mark("start;%s;%s" % (b64("sleep 9"), b64("/"))) + b"partial")
    from manual_feed import results
    done, rest = results(log)
    assert done == [{"command": "ls /x", "cwd": "/home", "exit": 2, "output": "ls: no"}]
    assert rest.startswith(b"\x1b]7777;start;") and rest.endswith(b"partial")  # still running: kept for later
    assert results(b"no marks")[1] == b""


def _ran(monkeypatch, tmp_path, rows):
    import json
    from system import terminal
    path = tmp_path / "results.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "{broken\n")
    monkeypatch.setattr(terminal, "RESULTS", str(path))
    return path


def test_did_that_work_reads_the_manual_terminal(monkeypatch, tmp_path):
    from core import verify
    _ran(monkeypatch, tmp_path, [{"command": "rm -r old", "cwd": "/", "exit": 0, "output": "", "at": 50},
                                 {"command": "sudo rm -r old", "cwd": "/", "exit": 1, "output": "x\nPermission denied",
                                  "at": 150}])
    assert verify._terminal_state("sudo rm -r old", 100) == (
        False, "you ran it and it failed with exit code 1: Permission denied")
    assert verify._terminal_state("sudo rm -rf old", 100)[0] is False  # edited before running: same program
    assert verify._terminal_state("rm -r old", 100)[0] is None  # ran before it was handed over: not this one


def test_claude_hears_what_was_run(monkeypatch, tmp_path):
    from claude import hook
    monkeypatch.setattr(hook, "WAITING", str(tmp_path / "wait_%s"))
    monkeypatch.setattr(hook, "results_since", lambda since: rows)
    rows = []
    assert hook.terminal_news("s1") == ""  # nothing left there by this session
    hook.left_in_terminal("s1")
    assert "hasn't been run" in hook.terminal_news("s1")
    rows = [{"command": "make install", "cwd": "/src", "exit": 2, "output": "error: no rule", "at": 1}]
    news = hook.terminal_news("s1")
    assert "$ make install\n(in /src, exit code 2)\nerror: no rule" in news
    assert hook.terminal_news("s1") == ""  # told once
