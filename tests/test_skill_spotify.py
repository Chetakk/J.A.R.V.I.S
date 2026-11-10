"""Spotify's own page through JARVIS Link (skills/media.py): play one of yours by name, a song by search, your
usual, your library as a card, shuffle, repeat, like, Spotify's volume; and skip / restart on any player."""
import pytest

from harness import Clock
from skills import media
from test_skill_media_windows import Player


class Page:
    """A pretend Spotify page: answers media._page like the extension's spotify() does."""

    def __init__(self):
        self.calls = []
        self.state = {"path": "/", "shuffle": False, "repeat": False, "liked": False, "volume": 0.5}
        self.items = [{"name": "Liked Songs", "sub": "Playlist · 120 songs", "path": "/collection/tracks"},
                      {"name": "Chill Vibes", "sub": "Playlist · Chetak", "path": "/playlist/abc"},
                      {"name": "Arijit Singh", "sub": "Artist", "path": "/artist/xyz"}]

    def __call__(self, do, timeout=3.0, **args):
        self.calls.append((do, args))
        if do == "library":
            return {"items": self.items}
        if do == "play":
            self.state["path"] = args["path"]
            return dict(self.state, played=True)
        if do in ("shuffle", "repeat"):
            self.state[do] = args["on"]
        if do == "like":
            self.state["liked"] = True
        if do == "volume":
            self.state["volume"] = args["to"]
        return dict(self.state)

    def played(self):
        return [a["path"] for d, a in self.calls if d == "play"]


@pytest.fixture
def page(jv, monkeypatch, tmp_path):
    monkeypatch.setattr(media, "time", Clock())
    monkeypatch.setattr(media, "STORE", str(tmp_path / "spotify.json"))
    monkeypatch.setattr(media, "_spotify_player", lambda: None)  # the card has nothing to follow
    p = Page()
    monkeypatch.setattr(media, "_page", p)
    return p


@pytest.mark.parametrize("text", ["play chill vibes", "play my chill vibes playlist", "play chill vibes on spotify",
                                  "put on my chill vibes"])
def test_plays_one_of_yours_by_name(jv, page, text):
    jv.do(text)
    assert page.played() == ["/playlist/abc"]
    assert jv.said == ["Playing Chill Vibes."]
    assert media._load()["plays"] == {"/playlist/abc": 1}


def test_songs_by_an_artist_you_follow_play_their_page(jv, page):
    jv.do("play songs by arijit singh")
    assert page.played() == ["/artist/xyz"]


@pytest.mark.parametrize("text, path", [
    ("play blinding lights by the weeknd", "/search/blinding%20lights%20by%20the%20weeknd/tracks"),
    ("play the song kesariya", "/search/kesariya/tracks"),
    ("play lofi beats on spotify", "/search/lofi%20beats"),
])
def test_anything_else_is_spotifys_search(jv, page, text, path):
    jv.do(text)
    assert page.played() == [path]


def test_play_something_not_yours_is_handed_on(jv, page):
    jv.do("play chess")
    assert page.played() == []  # not in your library: the browser / Claude has it


def test_play_my_usual_is_the_most_played(jv, page):
    jv.do("play arijit singh")
    jv.do("play arijit singh")
    jv.do("play chill vibes")
    jv.do("play my usual")
    assert page.played()[-1] == "/artist/xyz"


def test_your_library_as_a_card(jv, page):
    jv.do("play chill vibes")
    jv.do("what playlists do i have")
    card = [c for c in jv.cards if c.get("id") == "spotify-library"][-1]
    assert "**Liked Songs**" in card["text"] and "**Arijit Singh** · Artist" in card["text"]
    assert card["choices"][0] == {"label": "Chill Vibes", "do": "play Chill Vibes on spotify"}
    assert jv.said[-1] == "You usually listen to Chill Vibes, Liked Songs and Arijit Singh."


@pytest.mark.parametrize("text, what, on, line", [
    ("shuffle on", "shuffle", True, "Shuffle on."), ("turn on shuffle", "shuffle", True, "Shuffle on."),
    ("shuffle the playlist", "shuffle", True, "Shuffle on."), ("stop shuffling", "shuffle", False, "Shuffle off."),
    ("repeat this song", "repeat", "one", "Repeating this song."), ("repeat on", "repeat", True,
                                                                     "Repeating the playlist."),
    ("repeat the playlist", "repeat", True, "Repeating the playlist."), ("repeat off", "repeat", False, "Repeat off."),
])
def test_shuffle_and_repeat(jv, page, text, what, on, line):
    jv.do(text)
    assert (what, {"on": on}) in page.calls
    assert jv.said == [line]


def test_like_and_volume(jv, page):
    jv.do("like this song")
    jv.do("spotify volume 40")
    jv.do("turn spotify up")
    assert jv.said == ["Added to your Liked Songs.", "Spotify at 40%.", "Spotify at 55%."]


def test_repeat_after_me_still_repeats(jv, page):
    jv.do("repeat hello there")
    assert jv.said == ["hello there"] and not page.calls


def test_controls_when_spotify_isnt_open(jv, page, monkeypatch):
    monkeypatch.setattr(media, "_page", lambda do, **a: None)
    jv.do("shuffle on")
    assert jv.said == ["Spotify isn't open."]


def test_without_spotify_installed_play_x_on_spotify_goes_on(jv, page, monkeypatch):
    monkeypatch.setattr(media, "_page", lambda do, **a: None)
    monkeypatch.setattr(jv.j.apps, "find", lambda name: None)
    assert jv.j.skills.media.play_named("lofi beats") is False


def test_skip_and_restart_seek_the_playing_player(jv, monkeypatch):
    p = Player("Blinding Lights")
    p.props["Metadata"].value["mpris:trackid"] = "/track/1"
    monkeypatch.setattr(media, "_mpris_player", lambda: ("org.mpris.MediaPlayer2.chrome", p))
    jv.do("skip 30 seconds")
    jv.do("rewind 10 seconds")
    jv.do("restart this song")
    assert p.calls == ["Seek", "Seek", "SetPosition"]
