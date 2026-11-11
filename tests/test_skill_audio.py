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
    assert jv.says("Sound is going to the earphones", "listening on the USB microphone")
    text = jv.card_text()
    assert "the earphones **(in use)**, 45%" in text and "the built-in output, 70%" in text
    assert "Monitor of" not in text  # a speaker's echo isn't a microphone


def test_which_speaker(jv, pulse):
    jv.do("which speaker am i using")
    assert jv.said == ["Sound is going to the earphones, at 45 percent."]


def test_which_mic_warns_when_muted(jv, pulse):
    pulse.get(USB_MIC)["mute"] = True
    jv.do("which mic are you using")
    assert jv.says("it's muted")


def test_low_mic_is_flagged(jv, pulse):
    pulse.get(USB_MIC)["volume"] = 12
    jv.do("what's the mic volume")
    assert jv.says("12 percent", "That's low")


def test_switch_output_moves_whats_playing_and_undo_goes_back(jv, pulse):
    jv.do("switch audio to the built-in output")
    assert jv.said == ["Sound is going to the built-in output now."]
    assert pulse.default["sink"] == JACK and pulse.moved == [("12", JACK)]
    jv.clear()
    jv.do("undo that")
    assert pulse.default["sink"] == USB
    assert jv.says("Sound is back on the earphones")


@pytest.mark.parametrize("said, to", [("switch audio to earphones", USB), ("switch to my headphones", USB),
                                      ("switch audio to the speaker", ECHO), ("use the echo", ECHO)])
def test_earphones_are_the_usb_and_the_speaker_is_the_echo(jv, pulse, said, to):
    pulse.add("sink", ECHO, "Echo Dot-1AB")
    pulse.default["sink"] = JACK
    jv.do(said)
    assert pulse.default["sink"] == to


def test_switch_to_the_one_already_in_use(jv, pulse):
    jv.do("switch audio to usb")
    assert jv.said == ["Sound is already going to the earphones."]
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
    assert jv.said == ["I can't find kitchen radio. You have the earphones and the built-in output."]


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


# --- muting: the speaker, the microphone and JARVIS's own voice, each on its own and one way at a time ---

def test_mute_the_sound_mutes_the_speaker(jv, pulse):
    jv.do("mute the sound")
    assert pulse.get(USB)["mute"] is True
    assert jv.says("muted")
    assert jv.j.mutes["speaker"] and "speaker" in jv.j.muted_on_purpose


def test_unmute_never_mutes(jv, pulse):
    """A toggle made "unmute" mute the speaker whenever nothing was muted."""
    jv.do("unmute the sound")
    assert pulse.get(USB)["mute"] is False
    assert jv.says("already on")


def test_bare_mute_and_unmute_are_jarvis_mic(jv, pulse):
    jv.do("mute")
    assert jv.j.deaf and pulse.get(USB)["mute"] is False
    jv.do("unmute")
    assert not jv.j.deaf and pulse.get(USB)["mute"] is False


def test_no_sound_mutes_the_speaker(jv, pulse):
    jv.do("no sound")
    assert pulse.get(USB)["mute"] is True and not jv.j.deaf


def test_unmute_the_sound_brings_it_back(jv, pulse):
    pulse.get(USB)["mute"] = True
    jv.j.show_mutes(speaker=True)
    jv.do("unmute the sound")
    assert pulse.get(USB)["mute"] is False
    assert not jv.j.mutes["speaker"] and "speaker" not in jv.j.muted_on_purpose


def test_muting_the_mic_stops_only_jarvis_straight_away(jv, pulse):
    jv.do("mute the mic")
    assert not jv.questions  # you said it: no "are you sure"
    assert jv.j.deaf and jv.j.mutes["mic"]
    assert pulse.get(USB_MIC)["mute"] is False  # the system microphone stays live for other apps
    assert jv.says("not listening") and not jv.says("tray")  # nothing about the tray, ever
    assert not any("set-source-mute" in c for c in jv.shell.calls)


def test_the_tray_stops_and_resumes_listening_without_asking(jv, pulse):
    """Unmuting can only come from the tray icon: by then JARVIS can't hear a spoken answer."""
    jv.do("/mute mic on")
    assert jv.j.deaf and not jv.questions
    jv.do("/mute mic off")
    assert not jv.j.deaf and not jv.questions
    assert jv.says("listening again") and not jv.j.mutes["mic"]
    assert pulse.get(USB_MIC)["mute"] is False


def test_a_spoken_unmute_mic_does_not_mute_anything(jv, pulse):
    jv.do("unmute the mic")
    assert not jv.j.deaf and jv.says("listening")


def test_a_muted_speaker_you_asked_for_is_not_reported_as_a_fault(jv, pulse):
    from system import health
    jv.do("mute the sound")
    assert "speaker" not in health.problems(jv.j)
    assert jv.j.mutes["speaker"]


def test_jarvis_not_listening_stays_on_the_tray_after_a_health_check(jv, pulse):
    from system import health
    jv.do("/mute mic on")
    health.problems(jv.j)
    assert jv.j.mutes["mic"] and jv.j.deaf


def test_a_mute_you_did_not_ask_for_is_still_reported(jv, pulse):
    from system import health
    pulse.get(USB)["mute"] = True
    pulse.get(USB_MIC)["mute"] = True
    found = health.problems(jv.j)
    assert found.get("speaker") == "The speaker is muted."
    assert found.get("mic") == "Your microphone is muted."
    assert jv.j.mutes["speaker"] and jv.j.mutes["mic"] and not jv.j.muted_on_purpose


def test_quiet_mode_is_not_a_broken_voice(jv, pulse):
    """/mute used to turn the speech engine off, so the watchdog announced that Piper and spd-say were missing."""
    from system import health
    enabled = jv.j.speaker.enabled
    jv.do("/mute voice on")
    assert jv.j.speaker.muted and jv.j.speaker.enabled == enabled
    assert jv.j.mutes["voice"]
    jv.j.speaker.enabled = True
    assert "voice" not in health.problems(jv.j)


def test_unmute_yourself_never_mutes_jarvis(jv, pulse):
    jv.do("unmute yourself")
    assert not jv.j.speaker.muted and not jv.j.mutes["voice"]


def test_the_mute_replies_are_short(jv, pulse):
    """Short and said like a person, not a status line. They used to be a paragraph each ("...use Mute the microphone on my tray icon, or type unmute mic...")."""
    for phrase, answers in (("mute the sound", ()), ("unmute the sound", ()), ("unmute the mic", ()),
                            ("mute the mic", ("yes",)), ("mute the mic", ())):
        jv.clear()
        jv.do(phrase, *answers)
        for line in (alt for alts in jv.spoken for alt in alts):  # every wording, not just the first
            assert len(line) <= 75, (phrase, line)
            assert "tray" not in line.lower(), line


def test_the_mute_replies_sound_like_a_person(jv, pulse):
    """No "Muted." / "Unmuted." status words alone for the mic."""
    import re
    robotic = re.compile(r"^(?:muted|unmuted|mic's (?:off|back)|not listening|listening again)\.?$", re.I)
    for phrase, answers in (("mute the mic", ("yes",)), ("/mute mic off", ()), ("mute the mic", ("no",))):
        jv.clear()
        jv.do(phrase, *answers)
        for line in jv.said:
            assert not robotic.match(line), line


def test_unticking_the_tray_mic_box_undoes_a_system_mute(jv, pulse):
    """The tray box shows the system's own mute too; unticking it said "I'm already listening" and left it muted."""
    pulse.get(USB_MIC)["mute"] = True
    jv.j.show_mutes(_asked=False, mic=True)
    jv.do("/mute mic off")
    assert pulse.get(USB_MIC)["mute"] is False and not jv.j.mutes["mic"] and not jv.j.deaf
    assert jv.says("unmuted") and not jv.questions
