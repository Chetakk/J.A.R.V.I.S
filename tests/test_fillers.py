"""JARVIS's wait lines keep his grammar, are all known as fillers, and a list never gives the same line twice running."""
import re

from core.persona import LINES, lines
from voice.speech import FILLER, pick

WAITS = ["thinking", "asking_claude", "queued", "working", "still_working", "slow"]


def test_every_wait_line_is_a_filler():
    """speech.py holds a pending apology back from fillers ("Sorry about that. Thinking." sounded odd)."""
    for key in WAITS:
        for name in ("sir", None):
            for line in lines(key, name):
                assert FILLER.match(line), line


def test_wait_lines_keep_his_grammar():
    for key in WAITS + ["attention"]:
        for line in LINES[key]:
            assert not re.search(r"\blet me\b|right away|at once|\bsure\b|sorry|!", line, re.I), line
            assert all(re.match(r"[.?]", line[m.end():]) for m in re.finditer(r", \{name\}", line)), line  # last


def test_no_line_twice_running():
    options = ["a", "b", "c"]
    picked = [pick(options) for _ in range(200)]
    assert all(x != y for x, y in zip(picked, picked[1:]))
    assert pick(["only"]) == "only" and pick("text") == "text"
