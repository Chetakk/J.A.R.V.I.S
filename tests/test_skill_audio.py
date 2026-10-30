"""Speakers and microphones (skills/audio.py, system/audio.py): by what you call them; switching moves what's
playing; changing the mic (what JARVIS hears you with) asks first; "undo that" goes back."""
import pytest

from harness import Pulse

USB = "alsa_output.usb-Generic_AB13X_USB_Audio-00.analog-stereo"
JACK = "alsa_output.platform-sound.analog-stereo"
ECHO = "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
USB_MIC = "alsa_input.usb-Generic_AB13X_USB_Audio-00.mono-fallback"
JACK_MIC = "alsa_input.platform-sound.analog-stereo"


@pytest.fixture
def pulse(jv):
    p = Pulse(jv.shell)
    p.add("sink", USB, "AB13X USB Audio Analog Stereo", volume=45, default=True)
    p.add("sink", JACK, "Built-in Audio Analog Stereo", volume=70, port="analog-output-headphones")
    p.add("source", USB_MIC, "AB13X USB Audio Mono", volume=100, default=True)
    p.add("source", JACK_MIC, "Built-in Audio Analog Stereo", volume=50)
    return p


def test_lists_devices_by_spoken_name(jv, pulse):
    jv.do("what audio devices are there")
    assert jv.says("Sound is going to the USB speaker", "listening on the USB microphone")
    text = jv.card_text()
    assert "the USB speaker **(in use)**, 45%" in text and "the headphone jack, 70%" in text
    assert "Monitor of" not in text  # a speaker's echo isn't a microphone


def test_which_speaker(jv, pulse):
    jv.do("which speaker am i using")
    assert jv.said == ["Sound is going to the USB speaker, at 45 percent."]


def test_which_mic_warns_when_muted(jv, pulse):
    pulse.get(USB_MIC)["mute"] = True
    jv.do("which mic are you using")
    assert jv.says("muted, so I can't hear you")


def test_low_mic_is_flagged(jv, pulse):
    pulse.get(USB_MIC)["volume"] = 12
    jv.do("what's the mic volume")
    assert jv.says("12 percent", "That's low")


def test_switch_output_moves_whats_playing_and_undo_goes_back(jv, pulse):
    jv.do("switch audio to the headphone jack")
    assert jv.said == ["Sound is going to the headphone jack now."]
    assert pulse.default["sink"] == JACK and pulse.moved == [("12", JACK)]
    jv.clear()
    jv.do("undo that")
    assert pulse.default["sink"] == USB
    assert jv.says("Sound is back on the USB speaker")


def test_switch_to_the_one_already_in_use(jv, pulse):
    jv.do("switch audio to usb")
    assert jv.said == ["Sound is already going to the USB speaker."]
    assert not jv.shell.ran(r"set-default-sink")


def test_switching_the_mic_asks_first(jv, pulse):
    jv.do("switch mic to the built-in input", "yes")
    assert jv.questions[0].startswith("Listen on the built-in input instead?")
    assert pulse.default["source"] == JACK_MIC
    assert jv.says("I'm listening on the built-in input now")


@pytest.mark.parametrize("answer", ["no", None])
def test_mic_stays_when_not_confirmed(jv, pulse, answer):
    jv.do("switch mic to the built-in input", *([answer] if answer else []))
    assert pulse.default["source"] == USB_MIC
    assert not jv.shell.ran(r"set-default-source")
    assert jv.says("I'll keep listening on the USB microphone")


def test_mic_volume(jv, pulse):
    jv.do("set the mic volume to 80")
    assert pulse.get(USB_MIC)["volume"] == 80
    assert jv.said == ["The mic is at 80 percent."]


def test_mic_volume_too_low_asks_first(jv, pulse):
    jv.do("set the mic volume to 10", "no")
    assert jv.questions == ["At 10 percent I may not hear you. Set it anyway?"]
    assert pulse.get(USB_MIC)["volume"] == 100
    assert jv.says("it stays at 100 percent")


@pytest.mark.xfail(strict=True, reason="BUG: the mic-switch rule takes it first and looks for a mic called '10'")
@pytest.mark.parametrize("text", ["set the mic to 60", "set the microphone to 60 percent"])
def test_mic_level_without_the_word_volume(jv, pulse, text):
    jv.do(text)
    assert pulse.get(USB_MIC)["volume"] == 60


# --- failure cases ---

def test_echo_dot_missing_gives_the_alexa_app_steps(jv, pulse):
    jv.do("switch audio to the echo dot")
    assert jv.says("isn't connected right now", "Alexa app")
    assert not jv.shell.ran(r"set-default-sink")


def test_unknown_device_lists_what_there_is(jv, pulse):
    jv.do("switch audio to the kitchen radio")
    assert jv.said == ["I can't find kitchen radio. You have the USB speaker and the headphone jack."]


def test_a_device_that_refuses_is_reported_not_claimed(jv, pulse):
    pulse.add("sink", ECHO, "Echo Dot-1AB")
    pulse.broken.add(ECHO)
    jv.do("switch audio to the echo dot")
    assert jv.said == ["Sorry, the Echo Dot didn't take it."]
    assert pulse.default["sink"] == USB


def test_mic_volume_that_fails_is_reported(jv, pulse):
    pulse.broken.add(USB_MIC)
    jv.do("set the mic volume to 80")
    assert jv.said == ["Sorry, that didn't work."]


def test_no_microphone_at_all(jv):
    Pulse(jv.shell).add("sink", USB, "AB13X USB Audio Analog Stereo")
    jv.do("which mic are you using")
    assert jv.said == ["I can't find a microphone."]


def test_pactl_missing_does_not_crash(jv):
    jv.shell.on(r"^pactl", raises=FileNotFoundError)
    jv.do("which speaker am i using")
    assert jv.said == ["There's no speaker in use."]


def test_play_music_on_youtube_is_not_a_speaker(jv, pulse):
    jv.do("play music on youtube")
    assert not jv.says("I can't find")
    assert not jv.shell.ran(r"set-default-sink")
