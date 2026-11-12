"""A command that stops mid-sentence ("set a timer for") waits a moment for the rest of it."""
import threading
import time

import numpy as np

from voice import wake
from voice.wake import WhisperWake, unfinished


def test_what_sounds_unfinished():
    for text in ("I come with my", "Ask Claude to force quit it and.", "set a timer for", "open the",
                 "turn on the lights,", "play something by..."):
        assert unfinished(text), text
    # Real endings from the log, and short commands that must go at once
    for text in ("Remember that.", "zoom in", "call her", "what is it", "open Chrome", "pause", "volume up",
                 "set a timer for five minutes."):
        assert not unfinished(text), text


class _Whisper:
    def __init__(self, texts):
        self.texts = list(texts)

    def transcribe(self, pcm, command=False, plain=False):
        return self.texts.pop(0)


def _wake(texts, monkeypatch, hold=1.6):
    """A wake loop already waiting for a command (after "Jarvis" alone), hearing each of `texts` in turn."""
    monkeypatch.setattr(wake, "beep", lambda *a: None)  # no sound from the test
    monkeypatch.setattr(wake.threading, "Timer", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    w = WhisperWake.__new__(WhisperWake)
    w.names, w.whisper, w.max_seconds, w.hold_seconds, w.held = ["jarvis"], _Whisper(texts), 8, hold, ""
    w.vad = type("Vad", (), {"reset": lambda self: None, "is_speech_detected": lambda self: False})()
    w.is_busy = w.is_dictating = w.is_deaf = lambda: False
    w.is_echo = lambda text, started: False
    w.is_echo_sound = lambda seg, started: False
    w.heard, w.passed = [], []
    w.on_heard = lambda text, outcome: w.heard.append(outcome)
    w.on_text = lambda command, follow_up: w.passed.append((command, follow_up))
    w.on_wake = w.on_wait = lambda *a: None
    w.on_stop = None
    w.waiting_until, w.last_request, w.following = time.time() + 8, 0.0, False
    w._segments = lambda mic: iter([np.full(16000, 0.2, dtype=np.float32)] * len(texts))
    return w


def test_the_rest_after_a_pause_is_joined_on(monkeypatch):
    w = _wake(["Set a timer for.", "Five minutes."], monkeypatch)
    w._listen(None)
    assert w.passed == [("Set a timer for Five minutes.", False)]
    assert w.heard[0].startswith("sounds unfinished")


def test_if_nothing_more_comes_it_goes_as_it_is(monkeypatch):
    w = _wake(["Open the"], monkeypatch)
    w._listen(None)
    assert w.passed == []  # still waiting for the rest
    w._expire(w.waiting_until)
    assert w.passed == [("Open the", False)]


def test_a_finished_command_goes_at_once(monkeypatch):
    w = _wake(["Open Chrome."], monkeypatch)
    w._listen(None)
    assert w.passed == [("Open Chrome.", False)]


def test_never_mind_drops_the_unfinished_bit(monkeypatch):
    w = _wake(["Send an email to", "Never mind."], monkeypatch)
    w._listen(None)
    assert w.passed == [] and w.held == ""


def test_hold_off_in_settings_sends_it_at_once(monkeypatch):
    w = _wake(["Open the"], monkeypatch, hold=0)
    w._listen(None)
    assert w.passed == [("Open the", False)]


def test_the_rest_and_the_timeout_never_both_send_it(monkeypatch):
    w = _wake(["Open the"], monkeypatch)
    w._listen(None)
    w.held = "Open the"
    took = []
    threads = [threading.Thread(target=lambda: took.append(w._take_held()[0])) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(took)[-1] == "Open the" and took.count("Open the") == 1
