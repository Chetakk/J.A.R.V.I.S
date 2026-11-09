"""The transcribe key: hold Right Alt, speak, let go, and the words are typed at the cursor."""
import io
import json
import os
import threading
import time
import types

import yaml

import voice.listen as listen
import voice.stt as stt
from voice.listen import PushToTalk
from voice.stt import Whisper

LOUD = b"\x00\x40" * 16000


class _Recorder:
    def start(self):
        pass

    def stop(self):
        return LOUD


def _key(monkeypatch, cfg=None, window="gedit", title=""):
    monkeypatch.setattr(listen, "beep", lambda *a: None)  # no sound from the test
    monkeypatch.setattr(listen, "active_window_class", lambda: (window,))
    monkeypatch.setattr(listen, "active_window_title", lambda: title)
    monkeypatch.setattr(listen.keyboard, "Key", types.SimpleNamespace(alt_r="alt_r", alt_l="alt_l", tab="tab",
                                                                      ctrl_r="ctrl_r"))
    got, done = [], threading.Event()
    p = PushToTalk(cfg or {"key": "right_alt"}, lambda pcm: (got.append(pcm), done.set()))
    p.recorder = _Recorder()
    return p, got, done


def _settings(section):
    with open(os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")) as f:
        return yaml.safe_load(f)[section]


VS_CODE = "com.microsoft.VSCode"


def test_right_ctrl_is_always_jarvis_even_with_claude_code_open(monkeypatch):
    p, got, done = _key(monkeypatch, _settings("push_to_talk"), VS_CODE, "Claude Code - J.A.R.V.I.S")
    p._press("ctrl_r")
    assert p.recording
    p._release("ctrl_r")
    assert done.wait(2) and got == [LOUD]


def test_right_alt_in_vs_code_works_claude_codes_mic(monkeypatch):
    p, got, _ = _key(monkeypatch, _settings("transcribe"), VS_CODE)
    mic = []
    p._toggle_dictation = lambda: mic.append(p._dictating)
    p._press("alt_r")
    assert mic == [True] and not p.recording and p.held  # the wake word stays out while Claude's mic is open
    p._release("alt_r")
    assert mic == [True, False] and got == [] and p.held


def test_right_alt_outside_vs_code_types_at_the_cursor(monkeypatch):
    for window, title in (("gedit", "notes"), ("gnome-terminal-server", "claude"), ("google-chrome", "Claude - Chrome")):
        p, got, done = _key(monkeypatch, _settings("transcribe"), window, title)
        p._press("alt_r")
        assert p.recording, window
        p._release("alt_r")
        assert done.wait(2) and got == [LOUD], window


def test_holding_right_alt_records_and_letting_go_hands_it_over(monkeypatch):
    p, got, done = _key(monkeypatch)
    p._press("alt_r")
    assert p.recording
    p._release("alt_r")
    assert done.wait(2) and got == [LOUD] and not p.recording


def test_left_alt_and_alt_tab_do_not_type_anything(monkeypatch):
    p, got, done = _key(monkeypatch)
    p._press("alt_l")
    assert not p.recording
    p._press("alt_r")
    p._press("tab")  # a shortcut, not speech
    p._release("alt_r")
    assert not done.wait(0.1) and got == []


def test_the_wake_word_stays_out_just_after_the_key_is_let_go(monkeypatch):
    p, _, done = _key(monkeypatch)
    assert not p.held
    p._press("alt_r")
    p._release("alt_r")
    assert done.wait(2) and p.held
    p.ended = time.time() - 2
    assert not p.held


def test_transcribing_to_type_sends_no_hint():
    w = Whisper({})
    w.hint, w.command_hint = "Jarvis.", "Jarvis. Rhythmbox."
    prompts = []
    w._request = lambda pcm, prompt="": prompts.append(prompt) or "Hello there."
    assert w.transcribe(LOUD, plain=True) == "Hello there."
    assert prompts == [""]


def _sent(monkeypatch, seconds):
    """The form Whisper's server gets for a clip this long."""
    bodies = []

    def urlopen(req, timeout=None):
        bodies.append(req.data)
        return io.BytesIO(json.dumps({"text": "hi"}).encode())

    monkeypatch.setattr(stt.urllib.request, "urlopen", urlopen)
    Whisper({"audio_ctx": 512})._request(b"\x00\x40" * (16000 * seconds))
    return bodies[0]


def test_a_long_clip_gets_the_full_window_or_whisper_stalls(monkeypatch):
    assert b'name="audio_ctx"' in _sent(monkeypatch, 11)


def test_a_short_clip_keeps_the_fast_small_window(monkeypatch):
    assert b'name="audio_ctx"' not in _sent(monkeypatch, 5)
