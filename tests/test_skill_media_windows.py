"""Media (skills/media.py: MPRIS players, YouTube's own key) and windows (skills/windows.py: workspaces, move,
maximise, snap, close, focus)."""
import pytest

from harness import Clock
from skills import media


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(media, "time", Clock())  # waits and the card-following loop run their course at once
    monkeypatch.setattr(media, "FOLLOW_FOR", 60)


class Variant:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return self.value


class Player:
    """A pretend MPRIS player (what Gio.DBusProxy gives media.py)."""

    def __init__(self, title=None, artist=None, status="Playing", fail=None):
        self.calls, self.fail = [], fail
        self.props = {"PlaybackStatus": Variant(status),
                      "Metadata": Variant({k: v for k, v in (("xesam:title", title), ("xesam:artist", artist)) if v})}

    def call_sync(self, method, *a):
        if self.fail:
            raise self.fail
        self.calls.append(method)

    def get_cached_property(self, name):
        return self.props.get(name)


@pytest.fixture
def player(jv, monkeypatch):
    p = Player(title="Clair de Lune", artist=["Debussy"])
    monkeypatch.setattr(media, "_mpris_player", lambda: ("org.mpris.MediaPlayer2.rhythmbox", p))
    return p


@pytest.fixture
def no_player(jv, monkeypatch):
    monkeypatch.setattr(media, "_mpris_player", lambda: None)


@pytest.fixture
def spotify(jv, monkeypatch):
    """The Spotify web app installed but not running; launching it, or pressing Space in it, is recorded."""
    launched, state = [], {"player": None}
    monkeypatch.setattr(jv.j.apps, "find", lambda name: {"name": "Spotify", "process": ""} if name == "spotify"
                        else None)
    monkeypatch.setattr(jv.j.apps, "launch", lambda app, paths=(): launched.append(app["name"]) or True)
    monkeypatch.setattr(media.desktop, "go_to_jarvis_workspace", lambda: None)
    monkeypatch.setattr(media, "_spotify_player", lambda: state["player"])
    monkeypatch.setattr(media, "_mpris_player", lambda: state["player"])
    real_keys = media.desktop.keys

    def keys(combo):
        real_keys(combo)
        if combo == "space":
            state["player"] = ("org.mpris.MediaPlayer2.chrome.instance7", Player("Blinding Lights", ["The Weeknd"]))
    monkeypatch.setattr(media.desktop, "keys", keys)
    state["launched"] = launched
    return state


def test_play_music_starts_spotify_presses_play_and_shows_its_card(jv, spotify):
    jv.desktop.add("Spotify - Web Player", "spotifyweb SpotifyWeb", front=False)  # the window, once it's up
    jv.do("play some music")
    assert spotify["launched"] == [] and jv.desktop.did("keys") == ["space"]  # its window was up already
    assert jv.desktop.did("focus") == ["Spotify - Web Player"]
    c = [c for c in jv.cards if c.get("title") == "Spotify"][-1]
    assert "title: Blinding Lights" in c["text"] and "artist: The Weeknd" in c["text"]
    assert [x["label"] for x in c["choices"]] == ["Previous", "Pause", "Next"]


def test_play_music_launches_spotify_when_its_not_open(jv, spotify):
    jv.do("play music on spotify")
    assert spotify["launched"] == ["Spotify"]
    assert jv.says("Spotify didn't open") and not jv.desktop.did("keys")  # its window never came up


def test_play_spotify_resumes_it_when_its_open(jv, spotify):
    spotify["player"] = ("org.mpris.MediaPlayer2.chrome.instance7", Player("Blinding Lights", status="Paused"))
    jv.do("play spotify")
    assert spotify["player"][1].calls.count("Play") == 1 and not spotify["launched"]
    assert [c for c in jv.cards if c.get("title") == "Spotify"]


def test_play_music_without_spotify_goes_on(jv, monkeypatch):
    monkeypatch.setattr(jv.j.apps, "find", lambda name: None)
    monkeypatch.setattr(media, "_spotify_player", lambda: None)
    assert jv.j.skills.media.spotify() is False  # "play music" carries on to the browser / Claude


def test_album_art_from_a_file_url(tmp_path):
    art = tmp_path / "a b.jpg"
    art.write_bytes(b"x")
    assert media._art("file://" + str(art).replace(" ", "%20")) == str(art)
    assert media._art("file:///no/such.jpg") is None and media._art(None) is None


@pytest.mark.parametrize("text, method", [
    ("pause the music", "Pause"), ("resume", "Play"), ("next song", "Next"), ("skip this track", "Next"),
    ("previous track", "Previous"), ("mute the music", "Pause"),
])
def test_controls_the_player(jv, player, text, method):
    jv.do(text)
    assert player.calls == [method]
    assert not jv.claude.calls


def test_whats_playing(jv, player):
    jv.do("what's playing")
    assert jv.said == ["It's Clair de Lune by Debussy."]


def test_playing_without_a_title_says_so(jv, monkeypatch):
    monkeypatch.setattr(media, "_mpris_player", lambda: ("x", Player()))
    jv.do("what song is this")
    assert jv.said == ["Something's playing, but it doesn't say what."]


def test_youtube_in_the_browser_gets_its_k_key(jv, no_player):
    jv.desktop.add("lofi beats - YouTube - Google Chrome", "google-chrome Google-chrome")
    jv.desktop.add("Terminal", "gnome-terminal-server Gnome-terminal")
    jv.do("pause the video")
    assert jv.desktop.did("focus") == ["lofi beats - YouTube - Google Chrome"]
    assert jv.desktop.did("keys") == ["k"]


# --- failure cases ---

def test_next_with_nothing_playing_says_so(jv, no_player):
    jv.do("next song")
    assert jv.said == ["Nothing seems to be playing."]
    assert not jv.desktop.did("keys")


def test_whats_playing_with_nothing_playing(jv, no_player):
    jv.do("what's playing")
    assert jv.said == ["Nothing seems to be playing."]


def test_pause_with_nothing_playing_presses_no_keys(jv, no_player):
    jv.do("pause")
    assert not jv.desktop.did("keys") and not jv.says("paused")


def test_a_player_that_errors_is_reported_not_crashed(jv, monkeypatch):
    monkeypatch.setattr(media, "_mpris_player", lambda: ("x", Player(fail=RuntimeError("player quit"))))
    jv.do("pause the music")
    assert jv.says("didn't work", "player quit")


# --- windows ---

@pytest.fixture
def two_windows(jv):
    chrome = jv.desktop.add("Inbox - Gmail - Google Chrome", "google-chrome Google-chrome", front=False)
    term = jv.desktop.add("biztelai@jetson: ~", "gnome-terminal-server Gnome-terminal")
    return chrome, term


def test_switch_workspace_by_word(jv):
    jv.do("switch to workspace three")
    assert jv.desktop.did("workspace") == [3]


def test_move_the_front_window_to_a_workspace(jv, two_windows):
    jv.do("move this window to workspace 2")
    assert jv.desktop.did("to_workspace") == [("biztelai@jetson: ~", 2)]
    assert jv.said == ["Moved it to workspace 2."]


def test_maximise_a_named_window(jv, two_windows):
    jv.do("maximise chrome")
    assert jv.desktop.did("state")[0][:2] == ("Inbox - Gmail - Google Chrome", 1)


def test_snap_left_uses_exact_halves_without_tiling(jv, two_windows):
    jv.do("put chrome on the left")
    assert jv.desktop.did("move") == [("Inbox - Gmail - Google Chrome", 0, 0, 960, 1080)]


def test_snap_right_swaps_tiles_under_pop_shell(jv, two_windows):
    jv.desktop.tiling = True
    jv.do("snap chrome to the right")
    assert jv.desktop.did("keys") == ["super+Return", "ctrl+Right", "Return"]
    assert not jv.desktop.did("move")


def test_close_window_asks_first(jv, two_windows):
    jv.do("close this window", "yes")
    assert jv.questions == ["Close biztelai@jetson: ~?"]
    assert jv.desktop.did("close") == ["biztelai@jetson: ~"]


@pytest.mark.parametrize("answer", ["no", None])
def test_close_window_no_or_silence_keeps_it(jv, two_windows, answer):
    jv.do("close this window", *([answer] if answer else []))
    assert not jv.desktop.did("close")


def test_close_this_closes_the_window_on_screen(jv, two_windows):
    jv.do("close this", "yes")
    assert jv.desktop.did("close") == ["biztelai@jetson: ~"]


def test_close_this_skips_jarvis_own_window(jv, two_windows):
    jv.desktop.add("J.A.R.V.I.S", "jarvis Jarvis")  # you're talking to it, so it's the active one
    jv.do("close it", "yes")
    assert jv.desktop.did("close") == ["biztelai@jetson: ~"]


def test_close_this_with_no_window_asks_which_app(jv):
    jv.do("close this")
    assert jv.questions and "Which app" in jv.questions[0]
    assert not jv.desktop.did("close")


def test_focus_an_open_app(jv, two_windows):
    jv.do("switch to chrome")
    assert jv.desktop.did("focus") == ["Inbox - Gmail - Google Chrome"]


# --- failure cases ---

def test_unknown_workspace_number_does_not_switch(jv):
    jv.do("go to workspace nine")
    assert not jv.desktop.did("workspace")


def test_focus_an_app_that_isnt_open_does_not_focus_something_else(jv, two_windows):
    jv.do("switch to slack")
    assert not jv.desktop.did("focus")


def test_move_with_no_windows_at_all_does_not_claim_it_moved(jv):
    jv.do("move this window to workspace 2")
    assert not jv.says("Moved it")


def test_the_music_card_stays_while_playing_and_can_hide_once_paused(jv, spotify):
    p = Player("Blinding Lights", status="Playing")
    spotify["player"] = ("org.mpris.MediaPlayer2.chrome.instance7", p)
    jv.do("play spotify")
    assert [c for c in jv.cards if c.get("title") == "Spotify"][-1]["stay"] is True
    p.props["PlaybackStatus"] = Variant("Paused")
    jv.do("what's playing")
    assert [c for c in jv.cards if c.get("id") == "playing"][-1]["stay"] is False


def test_the_card_closes_when_the_player_quits(jv, spotify, monkeypatch):
    p = Player("Blinding Lights")
    seen = iter([("org.mpris.MediaPlayer2.chrome.instance7", p)] * 2)
    monkeypatch.setattr(media, "_spotify_player", lambda: next(seen, None))  # there for the card, then gone
    monkeypatch.setattr(media, "_mpris_player", lambda: None)
    jv.do("play spotify")
    assert ("close_card", ("playing", False)) in jv.ui.calls


def test_a_staying_card_isnt_aged_out():
    from types import SimpleNamespace
    from ui import card
    closed = []
    wins = [SimpleNamespace(quiet=0, last={"stay": s}, close=lambda s=s: closed.append(s)) for s in (True, False)]
    card.Deck.age(SimpleNamespace(open=wins), 61, 60)
    assert closed == [False]
