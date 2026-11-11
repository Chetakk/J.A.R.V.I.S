"""Ears (voice/ears.py): the wake model decides whether there's a name, Parakeet writes the words."""
import contextlib
import io
import struct
import time

import numpy as np

from test_wake_names import _waiting_wake
from voice import ears as E
from voice.ears import Ears

LOUD = b"\xff\x7f" * 32000


def ears(monkeypatch, name_at, heard=("open youtube", -0.1)):
    e = Ears({})
    e.parakeet_calls = []
    monkeypatch.setattr(e, "name_end", lambda pcm, cutoff: name_at)
    monkeypatch.setattr(e, "_hear", lambda pcm, plain=False: e.parakeet_calls.append(pcm) or heard)
    monkeypatch.setattr(e, "_load", lambda: None)  # (the name alone starts loading Parakeet, on a thread)
    return e


def test_speech_with_no_name_never_reaches_parakeet(monkeypatch):
    e = ears(monkeypatch, None)
    assert e.transcribe(LOUD) == "" and not e.parakeet_calls


def test_a_command_with_no_name_is_written_down(monkeypatch):
    assert ears(monkeypatch, None).transcribe(LOUD, command=True) == "open youtube"


def test_the_name_alone_is_just_the_name(monkeypatch):
    e = ears(monkeypatch, 16000 * 2 - 100)  # almost nothing after it
    assert e.transcribe(LOUD) == "Jarvis." and not e.parakeet_calls


def test_the_name_and_a_command_in_one_breath(monkeypatch):
    e = ears(monkeypatch, 8000)
    assert e.transcribe(LOUD) == "Jarvis, open youtube"
    assert e.parakeet_calls == [LOUD[16000:]]  # only what came after the name


def test_too_quiet_is_nothing(monkeypatch):
    e = ears(monkeypatch, 0)
    assert e.transcribe(b"\x01\x00" * 16000, command=True) == "" and not e.parakeet_calls


def test_typing_writes_everything_without_looking_for_the_name(monkeypatch):
    e = ears(monkeypatch, None)
    assert e.transcribe(LOUD, plain=True) == "open youtube"


def test_doubt_comes_from_parakeet(monkeypatch):
    e = ears(monkeypatch, None, ("Lourdes discard.", -0.8))
    e.transcribe(LOUD, command=True)
    assert e.unsure("Lourdes discard.") and not e.unsure("typed this") and not e.unsure("")


def test_name_end_is_where_the_first_hit_window_ends(monkeypatch):
    e = Ears({})
    e.embedder = lambda audio: np.zeros((100, 96), dtype=np.float32)
    monkeypatch.setattr(e, "_score", lambda windows: np.array([0.1] * 30 + [0.99] + [0.1] * (len(windows) - 31)))
    lead = E.RATE + E.RATE // 5
    want = (8 * (30 + 15) + 76) * 160 - lead - int(E.BEFORE_CUT * E.RATE)
    assert e.name_end(b"\0\0" * 80000, 0.98) == want
    monkeypatch.setattr(e, "_score", lambda windows: np.zeros(len(windows)))
    assert e.name_end(b"\0\0" * 80000, 0.98) is None


def test_scores_are_a_small_network_with_no_torch():
    e = Ears({})
    e.layers = [(np.eye(2, 4), np.zeros(2)), (np.eye(2), np.zeros(2)), (np.array([[1.0, 1.0]]), np.array([0.0]))]
    got = e._score(np.array([[[1.0, 1.0, 0.0, 0.0]], [[-1.0, -1.0, 0.0, 0.0]]]))
    assert got[0] > 0.8 and got[1] == 0.5


def test_it_talks_to_parakeet_in_samples_and_reads_a_line():
    e = Ears({})

    class Proc:
        stdin, stdout = io.BytesIO(), io.BytesIO(b"-0.2500\tOpen YouTube.\n")
        poll = staticmethod(lambda: None)
    e._proc = Proc()
    assert e._hear(b"\x01\x00\x02\x00") == ("Open YouTube.", -0.25)
    assert Proc.stdin.getvalue() == struct.pack("<I", 2) + b"\x01\x00\x02\x00"


class FakeWhisper:
    server, model = "/x/whisper-server", "/x/m.bin"
    min_confidence = -0.6

    def __init__(self):
        self.up, self.starts, self.heard, self.typed, self.names, self.words = True, 0, [], [], None, None

    def start(self):
        self.starts += 1
        self.up = True
        return True

    def stop(self):
        self.up = False

    def _ping(self):
        return self.up

    def set_names(self, names):
        self.names = names

    def set_vocabulary(self, words):
        self.words = words

    def listen(self, pcm, command=False):
        self.heard.append((pcm, command))
        return "open youtube", 0.1, -0.8, 0.0

    def _request(self, pcm):
        self.typed.append(pcm)
        return "typed words"


def with_whisper(monkeypatch, name_at=None):
    fake = FakeWhisper()
    monkeypatch.setattr(E, "Whisper", lambda cfg: fake)
    e = Ears({"words": "whisper", "idle_minutes": 10}, {})
    monkeypatch.setattr(e, "name_end", lambda pcm, cutoff: name_at)
    return e, fake


def test_parakeet_is_the_default(monkeypatch):
    assert Ears({}).whisper is None


def test_with_whisper_no_name_means_whisper_isnt_used(monkeypatch):
    e, w = with_whisper(monkeypatch)
    assert e.transcribe(LOUD) == "" and not w.heard


def test_with_whisper_only_the_words_after_the_name_go_to_it_with_its_hint(monkeypatch):
    e, w = with_whisper(monkeypatch, 8000)
    assert e.transcribe(LOUD) == "Jarvis, open youtube"
    assert w.heard == [(LOUD[16000:], True)]


def test_with_whisper_typing_gets_no_hint(monkeypatch):
    e, w = with_whisper(monkeypatch)
    assert e.transcribe(LOUD, plain=True) == "typed words" and w.typed == [LOUD]


def test_with_whisper_its_own_confidence_cutoff_is_used(monkeypatch):
    e, w = with_whisper(monkeypatch)
    e.transcribe(LOUD, command=True)
    assert e.min_confidence == -0.6 and e.unsure("open youtube")  # -0.8 is under Whisper's cutoff


def test_with_whisper_names_and_vocabulary_reach_it(monkeypatch):
    e, w = with_whisper(monkeypatch)
    e.set_names(["jarvis"])
    e.set_vocabulary(["Timer"])
    assert w.names == ["jarvis"] and w.words == ["Timer"]


def test_with_whisper_it_unloads_when_idle_and_comes_back(monkeypatch):
    e, w = with_whisper(monkeypatch)
    e.used_at -= 601
    e._sleep_if_idle()
    assert not w.up and e._ping()
    e.transcribe(LOUD, command=True)
    assert w.up and w.starts == 1 and not e._asleep


def test_with_whisper_a_dead_server_is_unhealthy(monkeypatch):
    e, w = with_whisper(monkeypatch)
    w.up = False
    assert not e._ping()


class FakeParakeet:
    started = []

    def __init__(self, *a, **k):
        FakeParakeet.started.append(self)
        self.stdin, self.stdout, self.alive = io.BytesIO(), io.BytesIO(b"ready\n0.0\t\n-0.1\tOpen YouTube.\n"), True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self):
        return 0


def sleepy(monkeypatch, minutes=10):
    FakeParakeet.started = []
    monkeypatch.setattr(E.subprocess, "Popen", FakeParakeet)
    e = Ears({"idle_minutes": minutes})
    e.ready = True
    e._load()
    return e


def test_it_unloads_after_being_idle_and_still_counts_as_healthy(monkeypatch):
    e = sleepy(monkeypatch)
    e.used_at -= 601
    e._sleep_if_idle()
    assert e._proc is None and not FakeParakeet.started[0].alive and e._ping()


def test_it_stays_loaded_while_in_use(monkeypatch):
    e = sleepy(monkeypatch)
    e._sleep_if_idle()
    assert e._proc is FakeParakeet.started[0]


def test_zero_minutes_means_always_loaded(monkeypatch):
    e = sleepy(monkeypatch, minutes=0)
    e.used_at -= 100000
    e._sleep_if_idle()
    assert e._proc is FakeParakeet.started[0]


def test_it_comes_back_by_itself_when_needed(monkeypatch):
    e = sleepy(monkeypatch)
    e.used_at -= 601
    e._sleep_if_idle()
    assert e._hear(b"\x01\x00" * 100) == ("Open YouTube.", -0.1)
    assert len(FakeParakeet.started) == 2 and not e._asleep


def test_a_dead_parakeet_is_unhealthy_so_the_watchdog_restarts_it(monkeypatch):
    e = sleepy(monkeypatch)
    FakeParakeet.started[0].alive = False
    assert not e._ping()


def test_hearing_the_name_alone_starts_loading_it_for_the_command(monkeypatch):
    e = ears(monkeypatch, 16000 * 2 - 100)
    loaded = []
    monkeypatch.setattr(e, "_load", lambda: loaded.append(True))
    e.transcribe(LOUD)
    import time
    for _ in range(50):
        if loaded:
            break
        time.sleep(0.01)
    assert loaded


class FakeEars:
    ears = True

    def __init__(self, text):
        self.text, self.calls = text, []

    def transcribe(self, pcm, command=False, plain=False):
        self.calls.append((command, plain))
        return self.text


def wake_with(text, monkeypatch):
    w = _waiting_wake(text, monkeypatch)
    w.whisper = FakeEars(text)
    w.waiting_until, w.following = 0.0, False
    w.is_echo = lambda text, started: False
    return w


def test_one_breath_commands_are_not_heard_twice(monkeypatch):
    w = wake_with("Jarvis, open youtube", monkeypatch)
    w._listen(None)
    assert w.passed == ["open youtube"] and w.whisper.calls == [(False, False)]


def test_dictation_is_written_down_without_the_name(monkeypatch):
    w = wake_with("buy some milk", monkeypatch)
    w.is_dictating = lambda: True
    w._listen(None)
    assert w.passed == ["buy some milk"] and w.whisper.calls == [(False, True)]


def test_stop_is_heard_while_jarvis_is_talking(monkeypatch):
    w = wake_with("stop", monkeypatch)
    w.barge_in, stopped = "named", []
    w.on_stop = lambda: stopped.append(True)
    assert w._barge_in(np.full(8000, 0.2, dtype=np.float32), 0.5, time.time()) and stopped
    assert w.whisper.calls == [(True, False)]


def test_each_wake_up_is_kept_with_what_was_heard(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "DATA", str(tmp_path))
    e = ears(monkeypatch, 8000)
    e.keep_hits = 2
    for _ in range(3):
        e.transcribe(LOUD)
    hits = tmp_path / "hits"
    assert len(list(hits.glob("*.wav"))) == 2  # only the newest keep_hits
    lines = (hits / "hits.jsonl").read_text().splitlines()
    assert len(lines) == 3 and '"heard": "Jarvis, open youtube"' in lines[0]


def test_commands_and_speech_without_the_name_are_not_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "DATA", str(tmp_path))
    ears(monkeypatch, None).transcribe(LOUD)
    ears(monkeypatch, 8000).transcribe(LOUD, command=True)
    assert not (tmp_path / "hits").exists()


class FakeArecord:
    runs = []

    def __init__(self, args, **k):
        self.path, self.done, self.signals = args[-1], False, []
        FakeArecord.runs.append(self)
        with open(self.path, "wb") as f:
            f.write(b"\0" * (44 + 2 * E.RATE * 60))  # a minute of audio

    def poll(self):
        return 0 if self.done else None

    def send_signal(self, sig):
        self.signals.append(sig)
        self.done = True

    def wait(self):
        return 0


def test_it_records_only_while_something_plays_and_stops_at_the_limit(monkeypatch, tmp_path):
    FakeArecord.runs = []
    monkeypatch.setattr(E.subprocess, "Popen", FakeArecord)
    states = iter([False, False, True, True, False, True, False])
    rec = E.MediaRecorder(str(tmp_path), minutes=2, playing=lambda: next(states, False), poll=0)
    rec.run()
    assert len(FakeArecord.runs) == 2  # once per time it was playing; two minutes kept, then it's done
    assert all(r.signals == [E.signal.SIGINT] for r in FakeArecord.runs)  # stopped when the show paused


def test_recording_resumes_counting_after_a_restart(monkeypatch, tmp_path):
    (tmp_path / "old.wav").write_bytes(b"\0" * (44 + 2 * E.RATE * 120))
    FakeArecord.runs = []
    monkeypatch.setattr(E.subprocess, "Popen", FakeArecord)
    E.MediaRecorder(str(tmp_path), minutes=2, playing=lambda: True, poll=0).run()
    assert FakeArecord.runs == []


def test_nothing_is_recorded_while_the_mic_is_muted(monkeypatch):
    import skills.media
    monkeypatch.setattr(skills.media, "something_playing", lambda: True)
    e = Ears({"collect_media_minutes": 1, "idle_minutes": 0})
    monkeypatch.setattr(e, "_load", lambda: None)
    monkeypatch.setattr(E.MediaRecorder, "start", lambda self: None)
    saved = dict(("%s%d" % (k, i), 0) for k in "wb" for i in range(3))
    monkeypatch.setattr(E.np, "load", lambda path: contextlib.nullcontext(saved))
    monkeypatch.setattr(E, "Embedder", lambda threads: None)
    monkeypatch.setattr(Ears, "available", property(lambda self: True))
    e.start()
    assert e.recorder.playing()
    e.is_deaf = lambda: True
    assert not e.recorder.playing()


def test_false_wake_moves_the_last_wake_up_but_not_the_call_that_said_so(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "DATA", str(tmp_path))
    e = ears(monkeypatch, 8000)
    assert e.mark_false("false wake") == 0  # nothing kept yet
    e.transcribe(LOUD)  # the false one ("Jarvis, open youtube" in the fake)
    time.sleep(0.002)  # (clips are named to the millisecond; real wake-ups are seconds apart)
    e.keep_hit(b"\0\0" * 1600, "Jarvis, that was a false wake.")  # you, telling it: woke it too
    assert e.mark_false("that was a false wake") == 1
    assert len(list((tmp_path / "false").glob("*.wav"))) == 1 and len(list((tmp_path / "hits").glob("*.wav"))) == 1
    assert "open youtube" in (tmp_path / "false" / "false.jsonl").read_text()
    assert e.mark_false("that was a false wake") is None  # said twice: still just the one


def test_a_yes_said_over_the_question_answers_it(monkeypatch):
    """Seen in the log: "Want me to fix it?" ... "Yeah." was dropped as talking over JARVIS, then "I'll leave it"."""
    w = wake_with("Yeah.", monkeypatch)
    w.barge_in, stopped = "named", []
    w.on_stop = lambda: stopped.append(True)
    w.is_echo = lambda text, started: False
    w.is_asking = lambda: True
    assert w._barge_in(np.full(8000, 0.2, dtype=np.float32), 0.5, time.time()) and stopped
    assert w.passed == ["Yeah."]
    w = wake_with("Yeah, the music is loud.", monkeypatch)  # no question: still chatter
    w.barge_in, w.is_echo, w.on_stop = "named", (lambda text, started: False), (lambda: None)
    w._barge_in(np.full(8000, 0.2, dtype=np.float32), 0.5, time.time())
    assert w.passed == []


class Checker:
    """Stands in for the Whisper name check."""
    def __init__(self, heard, up=True):
        self.heard, self.up, self.calls, self.started = heard, up, 0, 0

    def _ping(self):
        return self.up

    def start(self):
        self.started += 1
        return True

    def transcribe(self, pcm):
        self.calls += 1
        return self.heard


def checked(monkeypatch, heard, up=True):
    e = ears(monkeypatch, 8000)
    e.names, e.keep_hits, e.checker = ["jarvis", "ev"], 0, Checker(heard, up)
    return e


def test_a_wake_up_whisper_hears_no_name_in_is_not_a_call(monkeypatch):
    e = checked(monkeypatch, "Yeah, I think this one could be the most successful thing.")
    assert e.transcribe(LOUD) == "" and not e.parakeet_calls


def test_a_wake_up_whisper_hears_the_name_in_goes_through(monkeypatch):
    assert checked(monkeypatch, "Jarvis, open YouTube.").transcribe(LOUD) == "Jarvis, open youtube"


def test_a_checker_that_is_not_up_never_loses_a_call(monkeypatch):
    e = checked(monkeypatch, "nothing", up=False)
    loads = []
    monkeypatch.setattr(e, "_load", lambda: loads.append(1))
    monkeypatch.setattr(E.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    assert e.transcribe(LOUD) == "Jarvis, open youtube" and loads and not e.checker.calls


def test_the_check_uses_the_one_whisper_that_writes_the_words():
    e = Ears({"words": "whisper", "name_check": True})
    assert e.checker is e.whisper and Ears({"words": "parakeet", "name_check": True}).checker is None


def test_the_command_itself_is_not_checked_for_the_name(monkeypatch):
    e = checked(monkeypatch, "open youtube")
    assert e.transcribe(LOUD, command=True) == "Jarvis, open youtube" and not e.checker.calls


def test_a_new_request_over_jarvis_keeps_claude_going(monkeypatch):
    """"Jarvis, Bios" over "Thinking." used to stop Claude too: the first question was lost without a word.
    Now it only cuts JARVIS's speech; "stop" is what drops Claude's question."""
    import inspect
    for said, called in (("Jarvis, open youtube", ["cut"]), ("stop", ["stop"])):
        w = wake_with(said, monkeypatch)
        w.barge_in, calls = "named", []
        w.on_stop, w.on_cut = (lambda: calls.append("stop")), (lambda: calls.append("cut"))
        args = [np.full(8000, 0.2, dtype=np.float32), 0.5]
        if "started" in inspect.signature(w._barge_in).parameters:  # (when the speech began, where it's passed in)
            args.append(time.time())
        assert w._barge_in(*args) and calls == called
    assert w.passed == [] and wake_with("x", monkeypatch).on_cut is None
