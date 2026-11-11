"""JARVIS knows its own voice by its sound: the mic is compared with the audio it just sent to the speaker, so an
echo Whisper garbles past its words ("Got it. The new Aced2D is work2") still isn't taken for you."""
import collections
import io
import time

import numpy as np

import voice.wake as wake
from voice.speech import Speaker, echo_match
from voice.wake import WhisperWake

RATE = 16000


def _voice(sec, seed, pitch=120):
    """Speech-like sound: syllables of a gliding pitch with its harmonics, gaps between them."""
    r = np.random.default_rng(seed)
    t = np.arange(int(sec * RATE)) / RATE
    on = np.repeat(r.random(int(sec * 6) + 1) > 0.35, RATE // 6 + 1)[:len(t)]
    phase = 2 * np.pi * np.cumsum(pitch * (1 + 0.15 * np.sin(2 * np.pi * r.uniform(0.5, 1.5) * t + r.uniform(0, 6)))) / RATE
    return (sum(np.sin(k * phase) / k for k in range(1, 12)) * on * 3000).astype(np.float32)


def _room(x):
    return np.convolve(x, [0.5, 0, 0, 0.3, 0, 0.15, 0, 0.08])[:len(x)]


PLAYED = _voice(6, 1)
# What the mic heard 1.5-4.5 s in, through a speaker half a second late and a small room, with a little noise.
ECHO = (_room(np.concatenate([np.zeros(RATE // 2), PLAYED]))[int(1.5 * RATE):int(4.5 * RATE)] * 0.8
        + np.random.default_rng(3).normal(0, 20, 3 * RATE)).astype(np.float32)
YOU = _voice(3, 9, pitch=95)


def test_its_echo_is_its_voice_and_yours_is_not():
    score, extra = echo_match(ECHO, PLAYED)
    assert score >= Speaker.SOUND_ECHO and extra < Speaker.SOUND_EXTRA
    assert echo_match(YOU * 0.2, PLAYED)[0] < Speaker.SOUND_ECHO  # you, while it talked
    score, extra = echo_match(ECHO + YOU * 1.5, PLAYED)  # you, louder than it, over it
    assert score < Speaker.SOUND_ECHO or extra >= Speaker.SOUND_EXTRA


def _speaker():
    s = Speaker.__new__(Speaker)
    s.played = collections.deque()
    s._busy_until = s._audio_until = s._interrupted = 0.0
    s._proc = s._player = None
    s.engine = "piper"
    return s


def test_it_compares_only_when_it_played_something_then():
    s = _speaker()
    now = time.time()
    s._keep_played(now, PLAYED.astype(np.int16).tobytes(), RATE)
    assert s.is_echo_sound(ECHO / 32767, now + 1.5)  # (the wake's bits are -1 to 1)
    assert not s.is_echo_sound(YOU * 0.2 / 32767, now + 1.5)
    assert s.sound_match(ECHO, now + 60) is None  # long after: nothing to compare, no work


def test_what_it_plays_is_kept_at_the_mic_rate_with_its_times():
    """Piper's 22050 Hz chunks, some ending in half a sample, come out as one 16 kHz track at the right times."""
    s = _speaker()
    tone = (np.sin(np.arange(22050) * 2 * np.pi * 440 / 22050) * 8000).astype(np.int16).tobytes()  # 1 s
    piper = type("P", (), {"stdout": io.BufferedReader(io.BytesIO(tone), buffer_size=4095)})()
    player = type("A", (), {"stdin": io.BytesIO()})()
    player.stdin.close = lambda: None
    started = time.time()
    s._pump(piper, player, 22050 * 2)
    assert player.stdin.getvalue() == tone  # the speaker got every byte
    track = s.played_audio(started - 0.5, started + 1.5)
    heard = np.flatnonzero(np.abs(track) > 100)
    assert abs(heard[0] / RATE - 0.5) < 0.05 and abs((heard[-1] - heard[0]) / RATE - 1.0) < 0.05
    # (resampling may round each chunk up a sample; each is placed by its own time, so nothing drifts)
    assert 0 <= len(np.concatenate([x for _, x in s.played])) - RATE <= len(s.played)


def test_audio_cut_off_by_stop_is_forgotten():
    s = _speaker()
    now = time.time()
    s._keep_played(now - 1, PLAYED[:RATE].astype(np.int16).tobytes(), RATE)
    s._keep_played(now + 5, PLAYED[:RATE].astype(np.int16).tobytes(), RATE)  # queued, would play in 5 s
    s.stop()
    assert [w for w, _ in s.played] == [now - 1]


class _Whisper:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def transcribe(self, pcm, command=False):
        self.calls += 1
        return self.text


def _wake(text, monkeypatch, echo_sound):
    monkeypatch.setattr(wake, "beep", lambda *a: None)  # no sound from the test
    w = WhisperWake.__new__(WhisperWake)
    w.names, w.whisper, w.max_seconds = ["jarvis"], _Whisper(text), 8
    w.vad = type("Vad", (), {"reset": lambda self: None})()
    w.is_busy = w.is_talking = lambda: False
    w.is_dictating = w.is_deaf = lambda: False
    w.is_echo = lambda text, started: False  # its words didn't match: Whisper garbled them
    w.is_echo_sound = lambda seg, started: echo_sound
    w.heard, w.passed, w.stopped = [], [], []
    w.on_heard = lambda text, outcome: w.heard.append(outcome)
    w.on_text = lambda command, follow_up: w.passed.append(command)
    w.on_wake = lambda listening: None
    w.on_stop = w.on_cut = lambda: w.stopped.append(True)
    w.following, w.waiting_until, w.last_request = True, time.time() + 8, time.time()
    w.barge_in = "any"
    w._segments = lambda mic: iter([np.full(16000, 0.2, dtype=np.float32)])
    return w


def test_its_echo_after_it_stopped_never_reaches_whisper(monkeypatch):
    w = _wake("Got it. The new Aced2D is work2. What with the market done?", monkeypatch, True)
    w._listen(None)
    assert w.passed == [] and w.whisper.calls == 0 and w.heard == ["ignored (my own voice, by its sound; 1.0 s)"]
    w = _wake("Mark the second one done.", monkeypatch, False)  # you: passed on as before
    w._listen(None)
    assert w.passed == ["Mark the second one done."]


def _barge_in(w):
    # shortcut: passes `started` only where _barge_in takes it; plain 3 args once that change is committed
    args = (np.full(16000, 0.2, dtype=np.float32), 1.0, time.time())
    return w._barge_in(*args[:w._barge_in.__code__.co_argcount - 1])


def test_its_echo_by_sound_does_not_interrupt_it(monkeypatch):
    """Seen in the log: its own line, garbled, cut it off as if you had talked over it, and went to Claude."""
    w = _wake("Got it. The new Aced2D is work2. What with the market done?", monkeypatch, True)
    assert _barge_in(w)
    assert w.stopped == [] and w.passed == []
    w = _wake("Show me the list.", monkeypatch, False)  # you, in the conversation: it stops for you
    assert _barge_in(w)
    assert w.stopped == [True]
