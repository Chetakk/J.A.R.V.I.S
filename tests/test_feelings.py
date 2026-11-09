"""What the idle eyes feel from music and from the computer (ui/feelings.py), on made-up sound: no speakers used."""
import numpy as np
import pytest

from ui.feelings import RATE, music_feel, pc_mood

rng = np.random.default_rng(7)


def _beat(bpm, seconds=8, kick=9000):
    """A drum loop: a low thump on every beat over quiet hiss, like a song's bass line."""
    t = np.arange(seconds * RATE) / RATE
    audio = rng.normal(0, 300, len(t))
    for start in np.arange(0, seconds, 60 / bpm):
        n = (t >= start) & (t < start + 0.12)
        audio[n] += kick * np.sin(2 * np.pi * 60 * (t[n] - start)) * np.exp(-(t[n] - start) * 30)
    return audio.astype(np.int16)


def test_a_steady_fast_beat_is_danced_to_at_its_tempo():
    kind, bpm, last = music_feel(_beat(120))
    assert kind == "dance" and 113 <= bpm <= 127 and 0 <= last <= 0.5


def test_a_slow_soft_beat_is_swayed_with():
    kind, bpm, _ = music_feel(_beat(70, kick=1500))
    assert kind == "sway" and 65 <= bpm <= 76


def test_a_slow_looking_beat_that_hits_hard_is_half_time_dance_at_double_tempo():
    kind, bpm, _ = music_feel(_beat(60))  # a kick every second, pumping hard: a 120 bpm track with the kick on 1 and 3
    assert kind == "dance" and 115 <= bpm <= 125


def test_talk_with_pauses_is_not_music_and_unbroken_sound_without_a_beat_sways():
    talk = np.zeros(8 * RATE)
    at = 0
    while at < len(talk):  # bursts of a few syllables, then a pause
        n = int(rng.uniform(0.2, 0.9) * RATE)
        talk[at:at + n] = rng.normal(0, 3000, len(talk[at:at + n])) * np.abs(np.sin(np.linspace(0, 9, len(talk[at:at + n]))))
        at += n + int(rng.uniform(0.15, 0.6) * RATE)
    assert music_feel(talk.astype(np.int16))[0] is None
    assert music_feel(rng.normal(0, 2000, 8 * RATE).astype(np.int16))[0] == "sway"
    assert music_feel(np.zeros(2 * RATE, dtype=np.int16))[0] is None  # too little to tell


@pytest.mark.parametrize("cpu,gpu,hot,free,hour,busy_for,mood", [
    (10, 10, 60, 0.3, 14, 0, None),
    (95, 10, 80, 0.3, 14, 70, "hot"),  # heat first
    (95, 10, 60, 0.3, 14, 70, "straining"),
    (10, 95, 60, 0.3, 14, 15, "busy"),
    (10, 10, 60, 0.05, 14, 0, "squeezed"),
    (10, 10, 60, 0.3, 2, 0, "sleepy"),
    (10, 10, 60, 0.3, 6, 0, None)])
def test_the_computer_s_state_picks_the_most_pressing_mood(cpu, gpu, hot, free, hour, busy_for, mood):
    assert pc_mood(cpu, gpu, hot, free, hour, busy_for) == mood


def test_a_new_ambient_mood_waits_for_the_old_one_to_fade_out():
    from types import SimpleNamespace
    from ui.bar import Bar

    bar = Bar.__new__(Bar)
    bar.music, bar.body = SimpleNamespace(kind="dance", bpm=120, beat_at=100.0), SimpleNamespace(mood="sleepy")
    bar._ambient, bar._ambient_k, bar._ambient_t, bar.beat = None, 0.0, 0.0, 0.0
    bar.style, bar._style_until, bar._style_beat = None, 0.0, 0
    assert bar._ambience(100.0) == ("dance", 0.0)  # music goes before the hour
    for i in range(1, 8):
        bar._ambience(100.0 + i * 0.1)
    assert bar._ambient_k == 1.0 and bar.beat == pytest.approx(1.4)  # 0.7 s at 120 bpm
    bar.music.kind = None
    assert bar._ambience(100.8)[0] == "dance"  # fading out first
    for i in range(1, 5):
        bar._ambience(100.8 + i * 0.1)
    assert bar._ambience(101.3)[0] == "sleepy"


@pytest.mark.parametrize("mood,style", [("dance", m) for m in ("hop", "nod", "groove", "seesaw", "pulse")]
                         + [("sway", m) for m in ("smile", "notes")]
                         + [(m, None) for m in ("busy", "straining", "hot", "squeezed", "sleepy")])
def test_each_new_mood_draws(mood, style):
    import cairo
    from ui.window import UI

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 56, 56)
    UI._pet_eyes(cairo.Context(surface), 28, 28, 25, 1.0, 1.0, mood, 1.0, 3.0, 2.5, style)
    assert any(surface.get_data())


def test_dance_moves_are_random_kept_at_least_30_s_and_changed_on_a_beat(monkeypatch):
    from types import SimpleNamespace
    import ui.bar as B

    bar = B.Bar.__new__(B.Bar)
    bar.music, bar.body = SimpleNamespace(kind="dance", bpm=120, beat_at=0.0), SimpleNamespace(mood=None)
    bar._ambient, bar._ambient_k, bar._ambient_t, bar.beat = "dance", 1.0, 0.0, 0.0
    bar.style, bar._style_until, bar._style_beat = None, 0.0, 0
    monkeypatch.setattr(B.random, "uniform", lambda a, b: a)  # the shortest hold: 30 s
    seen, changes = set(), []
    for i in range(1, 1200 * 4):  # 20 minutes, four frames a second
        t = i / 4
        was = bar.style
        bar._ambience(t)
        seen.add(bar.style)
        if was and bar.style != was:
            changes.append(t)
    assert seen == set(B.DANCES)  # every move comes up
    assert all(b - a >= 30 for a, b in zip(changes, changes[1:]))  # none is cut short
    assert all(t * 2 % 1 < 0.5 for t in changes)  # each change lands right after a beat (120 bpm: every 0.5 s)
