"""Speech Whisper doubted gets "didn't catch that" instead of a Claude call; commands and typed text are untouched."""
import pytest

from voice.stt import Whisper, _clean

GARBLED = "Lourdes discard wobble"


def whisper(text, confidence, monkeypatch):
    w = Whisper({})
    monkeypatch.setattr(w, "_request", lambda *a, **k: {"text": text, "segments": [{"avg_logprob": confidence}]})
    return w, b"\xff\x7f" * 8000


@pytest.mark.parametrize("confidence, doubted", [(-0.83, True), (-0.59, False), (-0.05, False)])
def test_confidence_decides_doubt(confidence, doubted, monkeypatch):
    w, pcm = whisper(GARBLED, confidence, monkeypatch)
    assert w.transcribe(pcm, command=True) == GARBLED
    assert w.unsure(GARBLED) is doubted


def test_the_command_after_the_name_is_found_in_what_was_heard(monkeypatch):
    w, pcm = whisper("Hey Jarvis, Lourdes discard.", -0.9, monkeypatch)
    w.transcribe(pcm, command=True)
    assert w.unsure("Lourdes discard")


def test_the_latest_hearing_decides(monkeypatch):
    w, pcm = whisper(GARBLED, -0.9, monkeypatch)
    w.transcribe(pcm, command=True)
    w.recent.append(("Jarvis, " + GARBLED, -0.1))  # heard again with the command hint, and sure this time
    assert not w.unsure(GARBLED)


def test_what_was_never_heard_is_not_doubted(monkeypatch):
    w, _ = whisper(GARBLED, -0.9, monkeypatch)
    assert not w.unsure("typed this") and not w.unsure("")


def test_non_speech_markers_are_dropped():
    assert _clean("*Coughs*") == "" and _clean("[BLANK_AUDIO]") == "" and _clean("Hello *there") == "Hello *there"


def test_thanks_is_dropped_from_whisper_but_kept_from_parakeet():
    assert _clean("Thank you.") == "" and _clean("Thank you.", whisper=False) == "Thank you."
    assert _clean("Um.", whisper=False) == ""


class Doubts:
    def __init__(self, doubted):
        self.doubted = doubted

    def unsure(self, said):
        return self.doubted


def test_doubted_speech_does_not_reach_claude(jv):
    jv.j.whisper = Doubts(True)
    jv.do(GARBLED)
    assert not jv.claude.calls and jv.said


def test_confident_speech_still_reaches_claude(jv):
    jv.j.whisper = Doubts(False)
    jv.claude.reply("Sure.")
    jv.do(GARBLED)
    assert jv.claude.calls


def test_a_doubted_follow_up_is_dropped_quietly(jv):
    jv.j.whisper = Doubts(True)
    jv.j.handle(GARBLED, follow_up=True)
    assert jv.j.ignored and not jv.claude.calls and not jv.said


def test_a_doubted_command_jarvis_knows_still_runs(jv):
    jv.j.whisper = Doubts(True)
    jv.do("what time is it")
    assert jv.said and not any("catch" in line or "repeat" in line for line in jv.said)
