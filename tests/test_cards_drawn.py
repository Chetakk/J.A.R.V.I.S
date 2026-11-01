"""Cards drawn instead of written (ui/blocks.py): each comes up from JARVIS alone, with its drawn block in it, and
reads aloud as sentences, not as the block's raw lines. Claude's every call fails here, as in
test_cards_without_claude.py."""
import os
import time
from collections import namedtuple

import pytest

from claude.bridge import ClaudeError
from harness import Hub
from skills import media, network, slow_computer
from test_skill_weather import sky  # noqa: F401 (fixture)
from ui.card import plain

Statvfs = namedtuple("Statvfs", "f_bavail f_frsize f_blocks")


@pytest.fixture(autouse=True)
def no_claude(jv):
    def down(default):
        raise ClaudeError("Claude is off in this test")
    jv.claude._next = down
    yield
    for stop in list(jv.j.cards.timers.values()):
        stop.set()


def card(jv, title):
    titles = [c.get("title") for c in jv.cards]
    assert title in titles, "no %r card; shown: %s, said: %s" % (title, titles, jv.said)
    assert not jv.claude.calls, "Claude was asked: %s" % jv.claude.calls
    return [c for c in jv.cards if c.get("title") == title][-1]


# --- the machine ---

def test_system_usage_is_rings(jv):
    jv.files["/proc/meminfo"] = "MemTotal:       8000000 kB\nMemAvailable:    3000000 kB\n"
    jv.do("show the system usage")
    text = card(jv, "System usage")["text"]
    assert "```ring" in text and "CPU:" in text and "Memory:" in text


@pytest.mark.parametrize("said", ["show the temperatures", "how hot is the jetson", "is the computer overheating",
                                  "jetson temperature", "show me the sensors"])
def test_temperatures_and_batteries(jv, monkeypatch, said):
    monkeypatch.setattr(slow_computer, "_temps", lambda: [("CPU", 64), ("GPU", 88)])
    monkeypatch.setattr(slow_computer, "_batteries", lambda: [("Wireless Mouse", "normal")])
    monkeypatch.setattr(slow_computer, "_gpu_percent", lambda: 12)
    jv.do(said)
    text = card(jv, "Temperatures")["text"]
    assert "CPU: 64/100 °C ok" in text and "GPU: 88/100 °C bad" in text and "Wireless Mouse" in text
    assert jv.says("The hottest part is the GPU at 88 degrees, which is hot")


def test_the_cpu_temperature_command_still_answers_by_voice(jv):
    jv.shell.on(r"thermal_zone", out="61 °C")
    jv.do("cpu temperature")
    assert not [c for c in jv.cards if c.get("title") == "Temperatures"]


def test_disk_space_is_a_donut(jv, monkeypatch):
    home = os.path.expanduser("~")
    jv.shell.on(r"^du -x -d1", out="38000\t%s/Projects\n12000\t%s/Downloads\n500\t%s/.cache\n52000\t%s\n"
                % (home, home, home, home))
    monkeypatch.setattr(os, "statvfs", lambda path: Statvfs(28 * 2 ** 30 // 4096, 4096, 117 * 2 ** 30 // 4096))
    jv.do("what's taking up space")
    c = card(jv, "Disk space")
    assert "```donut" in c["text"] and "Projects: 37.1" in c["text"] and "free 28" in c["text"]
    assert c["choices"][0]["do"] == "open file ~/Projects"


# --- the network ---

def test_wifi_signal_is_a_live_ring(jv, monkeypatch):
    monkeypatch.setattr(network.network, "link", lambda: {"type": "wifi", "signal": 72, "connection": "Home",
                                                          "gateway": "192.168.1.1"})
    jv.do("how strong is my wifi")
    text = card(jv, "Wi-Fi signal")["text"]
    assert "Signal: 72% ok" in text and "Home" in text


@pytest.mark.parametrize("said", ["run a speed test", "how fast is my internet", "test my internet speed"])
def test_speed_test_gauges(jv, monkeypatch, said):
    monkeypatch.setattr(network.network, "ping", lambda host, count=3: {"ok": True, "loss": 0, "ms": 48.0})
    monkeypatch.setattr(network, "_download", lambda show: show(30.0) or 54.2)
    monkeypatch.setattr(network, "_upload", lambda show: 33.1)
    jv.do(said)
    text = card(jv, "Speed test")["text"]
    assert "Download: 54.2 / 100 Mbps" in text and "Upload: 33.1 / 100 Mbps" in text and "done" in text
    assert jv.says("Download 54 megabits a second, upload 33, ping 48 milliseconds")


def test_speed_test_that_cant_reach_its_server_says_so(jv, monkeypatch):
    monkeypatch.setattr(network.network, "ping", lambda host, count=3: {"ok": False, "loss": 100, "ms": None})
    monkeypatch.setattr(network, "_download", lambda show: (_ for _ in ()).throw(OSError("no route")))
    jv.do("run a speed test")
    assert jv.says("couldn't reach its server")


# --- media and levels ---

class Player:
    def __init__(self, status="Playing"):
        self.status = status

    def get_cached_property(self, name):
        values = {"PlaybackStatus": self.status,
                  "Metadata": {"xesam:title": "Blinding Lights", "xesam:artist": ["The Weeknd"],
                               "xesam:album": "After Hours", "mpris:length": 200_000_000}}
        v = values.get(name)
        return v and type("V", (), {"unpack": lambda self, v=v: v})()

    def call_sync(self, *a, **k):
        return type("V", (), {"unpack": lambda self: (63_000_000,)})()


def test_whats_playing_shows_the_song_moving(jv, monkeypatch):
    monkeypatch.setattr(media, "_mpris_player", lambda: ("org.mpris.MediaPlayer2.vlc", Player()))
    jv.do("what's playing")
    c = card(jv, "Now playing")
    assert "**Blinding Lights**" in c["text"] and "```progress\nPlaying: 63/200 playing" in c["text"]
    assert [x["label"] for x in c["choices"]] == ["Previous", "Pause", "Next"]


def test_a_paused_song_offers_play(jv, monkeypatch):
    monkeypatch.setattr(media, "_mpris_player", lambda: ("org.mpris.MediaPlayer2.vlc", Player("Paused")))
    jv.do("what's playing")
    assert card(jv, "Now playing")["choices"][1] == {"label": "Play", "do": "play"}


def test_volume_up_shows_the_new_level_then_goes(jv, monkeypatch):
    from skills import cards
    monkeypatch.setattr(cards, "LEVEL_SECONDS", 0.2)
    jv.shell.on(r"^pactl get-sink-volume", out="Volume: front-left: 45875 /  70% / -9.29 dB")
    jv.shell.on(r"^pactl get-sink-mute", out="Mute: no")
    jv.do("volume up")
    assert "Volume: 70%" in card(jv, "Volume")["text"]
    time.sleep(0.4)
    assert not jv.ui.card_open("level")


def test_brightness_shows_its_level(jv):
    jv.do("brightness high")
    assert "Brightness: 80%" in card(jv, "Brightness")["text"]


# --- your day ---

def _hub(jv):
    now = time.time()
    day = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    Hub(jv.net, entries=[{"cat": "work", "start": (day + 3600) * 1000, "end": (day + 3 * 3600) * 1000},
                         {"cat": "study", "start": (day - 86400 + 7200) * 1000, "end": (day - 86400 + 9000) * 1000}],
        goals={"work": {"mins": 240}}, reminders=[{"id": "r", "text": "Call the bank", "at": int((day + 23 * 3600) * 1000)}])


def test_how_did_i_spend_my_day(jv):
    _hub(jv)
    jv.do("how did I spend my day")
    text = next(c for c in jv.cards if c["title"].startswith("Your day"))["text"]
    assert "```donut" in text and "Work: 2.0" in text and "Work: 2.0/4.0 h" in text and not jv.claude.calls


def test_week_at_a_glance(jv):
    _hub(jv)
    jv.do("show me my week at a glance")
    text = card(jv, "Week at a glance")["text"]
    assert "```heat" in text and "```stack" in text


def test_calendar_is_a_timeline(jv):
    _hub(jv)
    jv.do("what's on my calendar today")
    text = next(c for c in jv.cards if c["title"].startswith("Today"))["text"]
    assert "```timeline\n23:00 Call the bank" in text and not jv.claude.calls


def test_the_briefing_draws_its_reminders(jv):
    _hub(jv)
    jv.do("brief me")
    text = next(c for c in jv.cards if c["title"].startswith("Briefing"))["text"]
    assert "```timeline" in text and "```ring" in text and "Call the bank" in text


# --- time and places ---

def test_time_somewhere_shows_two_clocks_offline(sky):
    sky.do("what time is it in tokyo")
    text = card(sky, "Time in Tokyo")["text"]
    assert "```clock\nHere\nTokyo: +0900" in text and not sky.net.calls


def test_weather_is_drawn_with_a_map(sky):
    sky.do("what's the weather")
    text = card(sky, "Weather in Bengaluru")["text"]
    assert "```weather\nnow: 2 28 29" in text and "```map\n12.97, 77.59: Bengaluru" in text


# --- reading a drawn card aloud ---

def test_a_drawn_card_reads_as_sentences():
    said = plain({"text": "**Working**\n```steps\ndone: Open VS Code | opened\nnow: Start the server\n```\n"
                          "```countdown\nend: 1\ntotal: 60\n```\n{ok} All good"})
    assert said == "Working. Open VS Code, done. Start the server, under way. All good."
