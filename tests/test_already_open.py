""""open chrome" / "open vs code" with one already open goes to that window (jarvis.py _go_to_open_app);
"open another chrome" / "open a new vs code window" starts a second one. Nothing is launched for real."""
import pytest

CHROME = "google-chrome Google-chrome"


@pytest.fixture
def need_chrome(jv):
    if not jv.j.apps.find("chrome"):
        pytest.skip("Chrome isn't installed here")


def test_open_chrome_goes_to_the_open_one(jv, need_chrome):
    jv.desktop.add("Inbox - Gmail - Google Chrome", CHROME, front=False)
    jv.do("Open Chrome.")
    assert jv.desktop.did("focus") == ["Inbox - Gmail - Google Chrome"]
    assert not jv.shell.ran(r"^google-chrome") and jv.says("already open")


@pytest.mark.parametrize("said", ["open another chrome", "open a new chrome window", "launch new chrome"])
def test_another_chrome_opens_a_new_window(jv, need_chrome, said):
    jv.desktop.add("Inbox - Gmail - Google Chrome", CHROME, front=False)
    jv.do(said)
    assert jv.shell.ran(r"^google-chrome$") and jv.desktop.did("focus") == []


def test_open_chrome_with_none_open_starts_it(jv, need_chrome):
    jv.do("open chrome")
    assert jv.shell.ran(r"^google-chrome$")


def test_open_chrome_skips_a_site_app_window(jv, need_chrome):
    jv.j.apps.apps.append({"name": "Spotify", "window": "open.spotify.com", "process": "", "path": "spotify-web.desktop"})
    jv.desktop.add("Spotify", "open.spotify.com google-chrome", front=False)
    jv.do("Open Chrome.")
    assert jv.desktop.did("focus") == [] and jv.shell.ran(r"^google-chrome$")
