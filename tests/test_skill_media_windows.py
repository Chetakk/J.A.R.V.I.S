"""Media (skills/media.py: MPRIS players, YouTube's own key) and windows (skills/windows.py: workspaces, move,
maximise, snap, close, focus)."""
import pytest

from skills import media


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
