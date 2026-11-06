"""Waking by name: a short name ("EV") only at the start; right after the name alone, the next words are yours."""
import time

import numpy as np

import voice.wake as wake
from voice.wake import WhisperWake, called_by_name

NAMES = ["jarvis", "ev", "e v", "evie", "eevee"]


def test_a_short_name_wakes_only_at_the_start():
    for said in ("EV, open YouTube.", "Hey EV.", "EV.", "Hey EV, show chat.", "OK Evie, what time is it"):
        assert called_by_name(said, NAMES), said
    for said in ("How was that EV?", "I'm moving by EV.", "Online on EV."):  # heard in the log, woke it wrongly
        assert not called_by_name(said, NAMES), said


def test_jarvis_still_wakes_anywhere():
    for said in ("What do you think, Jarvis?", "open YouTube, Jarvis", "Hey Jarvis.", "Please Jarvis, open YouTube"):
        assert called_by_name(said, NAMES), said


class _Whisper:
    def __init__(self, text):
        self.text = text

    def transcribe(self, pcm, command=False):
        return self.text


def _waiting_wake(text, monkeypatch):
    monkeypatch.setattr(wake, "beep", lambda *a: None)  # no sound from the test
    w = WhisperWake.__new__(WhisperWake)
    w.names, w.whisper, w.max_seconds = NAMES, _Whisper(text), 8
    w.vad = type("Vad", (), {"reset": lambda self: None})()
    w.is_busy = lambda: False
    w.is_dictating = lambda: False
    w.is_echo = lambda text, started: True  # sounds like something JARVIS said earlier
    w.heard, w.passed = [], []
    w.on_heard = lambda text, outcome: w.heard.append(outcome)
    w.on_text = lambda command, follow_up: w.passed.append(command)
    w.on_wake = lambda listening: None
    w.waiting_until, w.last_request = time.time() + 8, 0.0
    w._segments = lambda mic: iter([np.full(16000, 0.2, dtype=np.float32)])
    return w


def test_the_command_after_the_name_alone_is_not_taken_for_an_echo(monkeypatch):
    w = _waiting_wake("Do a speed test.", monkeypatch)
    w.following = False  # woken by "Jarvis." alone: JARVIS only beeped
    w._listen(None)
    assert w.passed == ["Do a speed test."]


def test_a_follow_up_can_still_be_an_echo(monkeypatch):
    w = _waiting_wake("Running a speed test now.", monkeypatch)
    w.following = True  # after an answer, its own voice can come back
    w._listen(None)
    assert w.passed == [] and w.heard == ["ignored (my own voice)"]


def test_while_dictating_words_without_the_name_are_passed_on(monkeypatch):
    w = _waiting_wake("Delete all of them.", monkeypatch)
    w.waiting_until, w.following = 0.0, True  # no name said, no follow-up window open
    w.is_echo = lambda text, started: False
    w.is_dictating = lambda: True
    w._listen(None)
    assert w.passed == ["Delete all of them."]
