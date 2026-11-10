"""'Stop' ends a Claude job that's still thinking, and Whisper's "Tanks." ends a follow-up like "thanks"."""
import subprocess
import threading
import time

import pytest

from claude.bridge import Claude, Stopped
from voice.wake import STOP


@pytest.mark.parametrize("said", ["Tanks.", "Thanks.", "thank you", "Okay, thanks.", "Thank you so much.", "Stop."])
def test_ends_follow_up(said):
    assert STOP.match(said)


def test_not_a_thanks():
    assert not STOP.match("there are two tanks for it")


class SlowClaude:
    """A Claude that never answers until it's killed (the harness's fake shell answers at once)."""
    def __init__(self, *a, **k):
        self.killed = threading.Event()

    def communicate(self, timeout=None):
        self.killed.wait(timeout)
        return "", ""

    def kill(self):
        self.killed.set()


def test_cancel_stops_a_running_claude(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", SlowClaude)
    c = Claude.__new__(Claude)
    c.bin, c.model, c.effort, c.timeout = "claude", "m", "low", 30
    c.running, c.stopped = set(), set()
    got = []

    def run():
        try:
            c._once("hi", "sys")
        except Exception as e:
            got.append(e)
    t = threading.Thread(target=run)
    t.start()
    while not c.running:
        time.sleep(0.01)
    assert c.cancel()
    t.join(2)
    assert isinstance(got[0], Stopped)
    assert not c.running and not c.stopped and not c.cancel()
