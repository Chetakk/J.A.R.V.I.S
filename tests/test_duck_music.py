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
