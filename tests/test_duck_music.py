"""Music goes down while JARVIS listens (so it hears you) and back to its own volume once it's done; JARVIS's own
voice (pacat) and paused streams are left alone."""
STREAMS = """Sink Input #0
\tCorked: no
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.process.binary = "pacat"

Sink Input #62
\tCorked: no
\tVolume: front-left: 52429 /  80% / 0.00 dB,   front-right: 52429 /  80% / 0.00 dB
\tProperties:
\t\tapplication.process.binary = "chrome"

Sink Input #70
\tCorked: yes
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.process.binary = "firefox"
"""


def test_music_ducks_while_listening_and_comes_back(jv):
    jv.shell.on(r"^pactl list sink-inputs$", out=STREAMS)
    jv.j.state("listening")
    jv.j.state("listening")  # the follow-up window lights it again: still from 80%, not from the lowered 24%
    jv.j.state("thinking")
    assert jv.shell.ran(r"set-sink-input-volume") == ["pactl set-sink-input-volume 62 24%"]
    jv.j.state(jv.j.rest)
    assert jv.shell.ran(r"set-sink-input-volume")[-1] == "pactl set-sink-input-volume 62 80%"
    jv.j.state("idle")  # nothing ducked: nothing to put back
    assert len(jv.shell.ran(r"set-sink-input-volume")) == 2


def test_a_stream_started_while_ducked_comes_back_too(jv):
    """"Play the music" while a video plays: PulseAudio starts Spotify's new stream at the lowered volume it
    remembers for Chrome, so it's put back to the video's volume along with it."""
    jv.shell.on(r"^pactl list sink-inputs$", out=STREAMS)
    jv.j.state("listening")
    jv.shell.on(r"^pactl list sink-inputs$", out=STREAMS.replace("#62", "#80").replace("80%", "24%"))
    jv.j.state("listening")  # the new one isn't lowered again
    jv.j.state(jv.j.rest)
    assert jv.shell.ran(r"set-sink-input-volume") == ["pactl set-sink-input-volume 62 24%",
                                                      "pactl set-sink-input-volume 80 80%"]


def test_shutting_down_puts_the_music_back_first(jv):
    """"Shut down" is answered while JARVIS still listens: the computer would go off with the music lowered, and
    PulseAudio would start Chrome at 24% from then on."""
    jv.shell.on(r"^pactl list sink-inputs$", out=STREAMS)
    jv.j.state("listening")
    jv.do("shut down", "yes")
    ran = jv.shell.ran(r"set-sink-input-volume|systemctl poweroff")
    assert ran[:3] == ["pactl set-sink-input-volume 62 24%", "pactl set-sink-input-volume 62 80%",
                       "systemctl poweroff"]
