"""Waking by name: a short name ("EV") only at the start; right after the name alone, the next words are yours."""
import collections
import time

import numpy as np

import voice.wake as wake
from voice.speech import Speaker
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
    w.is_deaf = lambda: False
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


def test_speech_still_being_transcribed_when_the_mic_is_muted_is_dropped(monkeypatch):
    """Seen in the log: a sentence said just before "mute the mic" was passed on after JARVIS said it wasn't listening."""
    w = _waiting_wake("In Township, we actually have a light.", monkeypatch)
    w.following, w.is_echo = True, lambda text, started: False
    w.is_deaf = lambda: True  # muted while Whisper was busy with it
    w._listen(None)
    assert w.passed == [] and w.heard == ["ignored (not listening: the mic is muted)"]


def test_a_follow_up_window_does_not_open_while_muted():
    w = WhisperWake.__new__(WhisperWake)
    w.is_busy, w.is_deaf, w.follow_seconds = (lambda: False), (lambda: True), 8
    w.woke = []
    w.on_wake = w.woke.append
    w._follow_up()
    assert w.woke == []


def _speaker(finished_ago, said):
    s = Speaker.__new__(Speaker)
    s._busy_until, s._audio_until, s.output_delay = time.time() - finished_ago, 0.0, 0.8
    s.history = collections.deque([(time.time() - finished_ago - 2, said)])
    return s


def test_what_you_say_after_jarvis_finished_is_not_its_echo():
    """Seen in the log: "Close Discord", "Stop" and "Do a speed test" were dropped as JARVIS's own voice."""
    s = _speaker(20, "Should I set a timer? Say yes or no.")
    for said in ("yes", "no", "set a timer"):
        assert not s.is_echo(said, time.time() - 2), said


def test_speech_that_began_while_jarvis_talked_can_be_its_echo():
    s = _speaker(0.2, "Should I set a timer? Say yes or no.")
    assert s.is_echo("set a timer", time.time() - 2)


def _name_again(monkeypatch):
    w = _waiting_wake("Jarvis.", monkeypatch)
    w.following, w.wait_seconds, w.waiting_until = True, 8, time.time() + 1  # the window is nearly over
    w.waits = []
    w._wait = w.waits.append
    w.on_text = lambda command, follow_up: w.passed.append((command, follow_up))
    return w


def test_the_name_during_a_follow_up_starts_a_fresh_wait(monkeypatch):
    """Seen in the log: "Put in to-do list" was lost after "Jarvis.", as the old window ran out."""
    w = _name_again(monkeypatch)
    w._listen(None)
    assert w.waits == [8] and w.passed == []


def test_the_command_after_the_name_is_not_a_follow_up(monkeypatch):
    w = _name_again(monkeypatch)
    w._listen(None)
    w.whisper.text = "Put in to-do list."
    w._listen(None)
    assert w.passed == [("Put in to-do list.", False)]


def test_the_name_while_dictating_opens_no_window(monkeypatch):
    w = _name_again(monkeypatch)
    w.is_dictating = lambda: True
    w._listen(None)
    assert w.waits == []


def test_its_own_reply_queued_in_the_mic_pipe_is_still_an_echo(monkeypatch):
    """Seen in the log: "Opening the projects folder." came back from the speaker, sat in the mic pipe while
    JARVIS was busy, and was taken as a new command because it was dated by when JARVIS got to it."""
    s = Speaker.__new__(Speaker)
    s._busy_until, s._audio_until, s.output_delay = time.time() - 3, 0.0, 0.8  # stopped talking 3 s ago
    s.history = collections.deque([(time.time() - 5, "Opening the Projects folder.")])
    w = _waiting_wake("Opening the projects folder.", monkeypatch)
    w.following, w.is_echo, w.lag = True, s.is_echo, 4.0  # 4 s of audio still waited in the pipe
    w._listen(None)
    assert w.passed == [] and w.heard == ["ignored (my own voice)"]
    w = _waiting_wake("Opening the projects folder.", monkeypatch)
    w.following, w.is_echo = True, s.is_echo  # no backlog: said well after JARVIS stopped, so it's you
    w._listen(None)
    assert w.passed == ["Opening the projects folder."]


def test_queued_counts_bytes_waiting_in_a_pipe():
    import os
    r, wfd = os.pipe()
    os.write(wfd, b"x" * 640)
    with os.fdopen(r, "rb") as pipe:
        assert wake._queued(pipe) == 640
    os.close(wfd)
    assert wake._queued(None) == 0


def test_a_long_line_of_its_own_heard_late_is_still_its_echo():
    """Seen in the log: its replies came back after it had finished and it answered itself in a loop."""
    s = _speaker(5, "Sounds like that got cut off, sir. What did you want to ask?")
    assert s.is_echo("Sounds like that got cuddle, sir. What did you want to ask?", time.time() - 2)
    s = _speaker(5, "Opening the project builder. Answer its questions in the terminal.")
    assert s.is_echo("Opening the project builder. Answer its questions in the terminal.", time.time() - 2)
    s = _speaker(5, "Did you mean open the Projects folder?")
    assert not s.is_echo("open the projects folder", time.time() - 2)  # short: you, answering it


def test_a_misheard_echo_while_it_is_still_talking_is_its_own_voice():
    """Seen in the log: Whisper misheard its own voice, so these didn't match word for word and got through."""
    for heard, said in (("Clones Text Editor.", "Closing Text Editor."),
                        ("Forgive me, CK, could you repeat that?", "Forgive me, Ckay, could you repeat that?"),
                        ("One moment's thinking.", "One moment, thinking."),
                        ("Opening the project folder and", "Opening the Projects folder.")):  # live, 20:11
        assert _speaker(-1, said).is_echo(heard, time.time() - 0.5), heard


def test_your_answer_while_it_is_still_talking_is_not_its_echo():
    """Near-matching words mustn't swallow what you say over it: letters alone ("close it" in "Closing Text
    Editor") would; a different number or a different last word keeps it yours."""
    for heard, said in (("close it", "Closing Text Editor."), ("set volume to 40", "Setting the volume to 45."),
                        ("turn it down", "Turning it up."), ("open spotify", "Opening YouTube."),
                        ("open the downloads folder", "Opening the Projects folder."), ("yes", "Close Visual Studio Code?"),
                        ("what's the weather", "What's new with you?"), ("play some music", "Playing your focus playlist.")):
        assert not _speaker(-1, said).is_echo(heard, time.time() - 0.5), heard


def test_a_misheard_word_only_counts_while_it_is_talking():
    assert not _speaker(5, "Closing Text Editor.").is_echo("Clones Text Editor.", time.time() - 2)


def test_its_own_line_with_its_name_in_it_is_still_an_echo(monkeypatch):
    """Seen in the log: the brief said "the newest is JARVIS wake 2", came back through the mic, and the
    name in it let it through as a command, so Claude answered JARVIS's own brief."""
    said = "15 open to-dos, the newest is JARVIS wake 2. It's on your screen."
    w = _waiting_wake("15 open to DOS. The newest is Jarvis. Wake 2. It's on your screen.", monkeypatch)
    w.following, w.said_lately = True, lambda: [said]
    w._listen(None)
    assert w.passed == [] and w.heard == ["ignored (my own voice)"]
    s = _speaker(-1, said)  # over that same line, the name and words of your own are you
    w = _waiting_wake("Jarvis. Take me to Chrome.", monkeypatch)
    w.following, w.said_lately, w.is_echo = True, s.said_lately, s.is_echo
    w._listen(None)
    assert w.passed == ["Take me to Chrome"]
    w = _waiting_wake("Jarvis, do a speed test.", monkeypatch)  # it didn't say its name: the name is yours
    w.following, w.said_lately = True, lambda: ["Running a speed test now."]
    w._listen(None)
    assert w.passed == ["do a speed test"]


def test_a_misheard_echo_just_after_it_stopped_is_still_its_voice():
    """Seen in the log: the speaker's lag made this begin just after JARVIS's estimated end, so it was taken
    word for word, "They" and "news" didn't match, and it went to Claude as your answer."""
    s = _speaker(0.5, "I didn't catch that, sir. Do you want me to mark the newest to-do, Web 2, done?")
    assert s.is_echo("I didn't catch that. They want you to mark the news to do.", time.time() - 0.2)


def test_the_tail_of_a_long_answer_is_still_its_echo():
    """A long Claude answer said 40 s ago and still playing: its tail mustn't go back to Claude as your words."""
    s = _speaker(-2, "Here is the plan. " * 3 + "First we measure the delay, then we feed the echo canceller.")
    s.history = collections.deque([(time.time() - 40, s.history[0][1])])
    assert s.is_echo("then we feed the echo canceller", time.time() - 1)
    s._busy_until = time.time() - 35  # finished long ago: words like it are yours
    assert not s.said_lately()



def test_the_name_and_thanks_in_one_breath_ends_it(monkeypatch):
    """Seen in the log: "Jarvis, Tanks." went to Claude ("Thinking.") instead of ending."""
    w = _waiting_wake("Jarvis, Tanks.", monkeypatch)
    w.waiting_until, w.following = 0.0, False  # not listening for a command: the name wakes it
    w.is_echo = lambda text, started: False
    w._listen(None)
    assert w.passed == [] and w.heard == ["done; back to waiting for the name"]
