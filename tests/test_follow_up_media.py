"""While a show or song plays, JARVIS needs its name again after answering: the show's words aren't your next request
(seen live: Brooklyn Nine-Nine's lines were taken as follow-ups and turned on private mode)."""
import time

import pytest

import jarvis as J


def run(jv, monkeypatch, playing, *said):
    monkeypatch.setattr(J, "something_playing", lambda: playing)
    calls = []
    jv.j.follow_up = lambda: calls.append("follow_up")
    jv.j.keep_following = lambda: calls.append("keep_following")
    monkeypatch.setattr(jv.j, "handle", lambda line, follow_up=False: True)
    for item in said:
        jv.j.inbox.put(item)
    jv.j.inbox.put(("quit", ""))
    J.run_loop(jv.j, has_ui=True)
    return calls


@pytest.mark.parametrize("item", [("voice", "what time is it"), ("follow", "and the date"), ("voice", "")])
def test_no_follow_up_while_something_plays(jv, monkeypatch, item):
    assert run(jv, monkeypatch, True, item) == []


def test_follow_up_as_before_when_nothing_plays(jv, monkeypatch):
    assert run(jv, monkeypatch, False, ("voice", "what time is it")) == ["follow_up"]


def test_answering_a_question_still_needs_no_name(jv, monkeypatch):
    """Its yes/no questions still listen without the name: they're asked, so an answer is expected."""
    monkeypatch.setattr(J, "something_playing", lambda: True)
    calls = []
    jv.j.follow_up = lambda: calls.append("follow_up")
    jv.j.speaker.done_at = time.time  # (nothing being said)
    jv.j.settings["confirm_timeout"] = 1
    jv.j.inbox.put(("voice", "yes"))
    assert J.Jarvis._answer(jv.j, "Close discord?") == "yes" and calls == ["follow_up"]


class Bus:
    def __init__(self, status, hang=False):
        self.status, self.hang = status, hang

    def __call__(self, args, **k):
        import subprocess
        if self.hang:
            raise subprocess.TimeoutExpired(args, k.get("timeout"))
        out = ("(['org.freedesktop.DBus', 'org.mpris.MediaPlayer2.chromium.instance1'],)" if any("ListNames" in a for a in args)
               else "(<'%s'>,)" % self.status)
        return type("Done", (), {"stdout": out})()


@pytest.mark.parametrize("status, playing", [("Playing", True), ("Paused", False), ("Stopped", False)])
def test_what_the_player_says(monkeypatch, status, playing):
    from skills import media
    monkeypatch.setattr(media.subprocess, "run", Bus(status))
    monkeypatch.setattr(media, "spotify_title", lambda: "")  # (not the real Spotify window)
    assert media.something_playing() is playing


def test_a_player_that_never_answers_is_not_playing(monkeypatch):
    """It once left the show recorder stuck for good: now each question has a time limit."""
    from skills import media
    monkeypatch.setattr(media.subprocess, "run", Bus("Playing", hang=True))
    monkeypatch.setattr(media, "spotify_title", lambda: "")
    assert media.something_playing() is False


def test_the_song_playing_is_read_from_the_player(monkeypatch):
    from skills import media

    def bus(args, **k):
        out = ("(<{'mpris:length': <int64 1>, 'xesam:title': <\"Don't Stop\">, 'xesam:artist': <['Queen', 'X']>}>,)"
               if "Metadata" in args else "(<{'xesam:title': <''>}>,)")
        return type("Done", (), {"stdout": out})()
    monkeypatch.setattr(media.subprocess, "run", bus)
    assert media.track("org.mpris.MediaPlayer2.chromium") == "Don't Stop · Queen"


def test_the_bar_says_why_it_wont_hear_you(jv, monkeypatch):
    """No follow-up window while music plays: the bar says to use the name, instead of silently not hearing."""
    run(jv, monkeypatch, True, ("voice", "what time is it"))
    assert any(c[0] == "hint" and "Jarvis" in c[1][0] for c in jv.ui.calls)
