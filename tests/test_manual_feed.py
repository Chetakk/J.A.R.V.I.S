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
