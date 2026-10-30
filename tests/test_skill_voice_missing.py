"""How JARVIS talks (skills/voice_prefs.py: speed, brief, private, quiet; kept across restarts) and requests that
leave out the one thing they need (skills/missing.py: it asks, then carries on with the whole request)."""
import pytest

from harness import Pulse

JACK = "alsa_output.platform-sound.analog-stereo"
USB = "alsa_output.usb-Generic_AB13X-00.analog-stereo"
BUDS = "bluez_sink.11_22_33_44_55_66.a2dp_sink"


@pytest.fixture
def speaker(jv):
    return jv.j.speaker


def pref(jv, name):
    return jv.j.memory.pref(name)


# --- voice prefs ---

def test_faster_and_slower_are_remembered(jv, speaker):
    before = speaker.speed()
    jv.do("talk faster")
    assert speaker.speed() < before and pref(jv, "speed") == speaker.speed()
    jv.do("slow down")
    assert speaker.speed() == pytest.approx(before, abs=0.01)
    jv.do("talk at your normal speed")
    assert speaker.speed() == speaker.default_speed and pref(jv, "speed") is None


def test_speed_has_limits(jv, speaker):
    for _ in range(12):
        jv.do("talk faster")
    assert speaker.speed() >= 0.6 and jv.says("That's as fast as I go.")


def test_brief_and_detailed(jv, speaker):
    jv.do("be brief")
    assert speaker.brief and pref(jv, "answers") == "brief"
    jv.do("give me detailed answers")
    assert not speaker.brief and pref(jv, "answers") == "detailed"


def test_quiet_mode_mutes_and_survives_a_restart(jv, speaker):
    jv.do("quiet mode")
    assert speaker.muted and jv.ui.calls[-1] == ("show_chat", (True,))
    speaker.muted = False  # a restart: a fresh speaker, then the saved prefs applied
    jv.j.skills.voice_prefs.apply()
    assert speaker.muted
    jv.do("you can talk again")
    assert not speaker.muted and pref(jv, "quiet") is None


def test_private_mode_goes_to_the_headphones(jv, speaker):
    p = Pulse(jv.shell)
    p.add("sink", JACK, "Built-in Audio", default=True)
    p.add("sink", BUDS, "WH-1000XM4", form="headset")
    jv.do("private mode")
    assert speaker.sink == BUDS and pref(jv, "private") is True
    jv.do("private mode off")
    assert speaker.sink is None and pref(jv, "private") is None


def test_private_without_headphones_offers_quiet_instead(jv, speaker):
    Pulse(jv.shell).add("sink", USB, "USB Audio", default=True)  # (the jack is the configured fallback)
    jv.do("headphones only", "yes")
    assert jv.questions == ["I can't find any headphones. Shall I go quiet instead, and just write in the chat?"]
    assert speaker.muted and speaker.sink is None


def test_private_without_headphones_and_a_no_stays_as_it_was(jv, speaker):
    Pulse(jv.shell).add("sink", USB, "USB Audio", default=True)  # (the jack is the configured fallback)
    jv.do("private mode", "no")
    assert not speaker.muted and pref(jv, "private") is None


def test_private_stays_private_when_the_headphones_vanish(jv, speaker):
    speaker.sink = BUDS
    jv.j.skills.voice_prefs._sink_lost()
    assert speaker.muted  # nothing goes out loud on the speaker instead
    assert jv.ui.calls[-1] == ("show_chat", (True,))


def test_private_on_restart_without_the_headphones_is_silent_not_loud(jv, speaker):
    jv.j.memory.set_pref("private", True)
    Pulse(jv.shell).add("sink", USB, "USB Audio", default=True)  # (the jack is the configured fallback)
    jv.j.skills.voice_prefs.apply()
    assert speaker.muted and speaker.sink is None


# --- missing values ---

def test_set_a_timer_asks_how_long(jv):
    jv.do("set a timer", "for 5 minutes")
    assert jv.questions == ["Sure, for how long?"]
    assert jv.j.last_request is not None or not jv.says("Sorry")


@pytest.mark.parametrize("answer", ["never mind", "no", None])
def test_no_answer_does_nothing(jv, answer):
    jv.do("set a timer", *([answer] if answer else []))
    assert jv.said[-1] in ("Okay, never mind.", "Okay.")
    assert not jv.shell.calls and not jv.cards


def test_add_a_todo_asks_what_then_adds_it(jv):
    from harness import Hub
    hub = Hub(jv.net)
    jv.do("add a todo", "buy milk")
    assert jv.questions == ["Sure, what should I add?"]
    assert [t["text"] for t in hub.state["todos"]] == ["Buy milk"]


def test_set_the_volume_asks_for_a_level(jv):
    jv.do("set the volume", "40")
    assert jv.questions == ["Sure, to what level?"]
    assert jv.shell.ran(r"^pactl set-sink-volume .* 40%")


def test_an_answer_that_isnt_a_level_is_not_guessed(jv):
    jv.do("set the volume", "banana")
    assert jv.said[-1] == "Sorry, I didn't get that."
    assert not jv.shell.ran(r"pactl set-sink-volume")


def test_volume_answer_out_of_range_is_not_used(jv):
    jv.do("set the volume", "400")
    assert not jv.shell.ran(r"pactl set-sink-volume")


def test_open_an_app_asks_which(jv):
    jv.do("open an app", "terminal")
    assert jv.questions == ["Sure, which app?"]
    assert jv.shell.ran(r"^gnome-terminal$")


def test_the_value_is_never_a_placeholder(jv):
    jv.do("type something", "hello there")  # not "typing something"
    assert jv.questions == ["Sure, what should I type?"]


def test_private_falls_back_to_the_configured_jack(jv, speaker):
    Pulse(jv.shell).add("sink", JACK, "Built-in Audio", default=True)
    jv.do("private mode")
    assert speaker.sink == JACK and pref(jv, "private") is True
