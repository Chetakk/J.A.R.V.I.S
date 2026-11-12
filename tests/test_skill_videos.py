"""Videos, links and tabs in your own Chrome (skills/videos.py) through JARVIS Link, faked here: what the page
lists, what JARVIS asked the extension to do. No Chrome, no network."""
import pytest

from skills import videos
from system import chrome_link

CHROME = "google-chrome Google-chrome"
ITEMS = [
    {"title": "Tiger 3 | Official Trailer | Salman Khan", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
     "video": True, "seen": True},
    {"title": "Rust lifetimes explained", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "video": True,
     "seen": True},
    {"title": "Lofi beats to study to", "url": "https://www.youtube.com/watch?v=ccccccccccc", "video": True,
     "seen": True},
    {"title": "Below the fold video", "url": "https://www.youtube.com/watch?v=ddddddddddd", "video": True,
     "seen": False},
    {"title": "Settings", "url": "https://www.youtube.com/account", "video": False, "seen": True},
]


class Link:
    """Stands in for system/chrome_link.call: replies[op] is a value or a function(args)."""

    def __init__(self):
        self.calls, self.replies = [], {"page": {"url": "https://www.youtube.com/", "title": "YouTube",
                                                 "items": ITEMS, "video": False}}

    def call(self, op, timeout=2.0, **args):
        self.calls.append((op, args))
        r = self.replies.get(op)
        return r(args) if callable(r) else r

    def did(self, op):
        return [a for o, a in self.calls if o == op]


@pytest.fixture
def link(jv, monkeypatch):
    fake = Link()
    monkeypatch.setattr(chrome_link, "call", fake.call)
    jv.desktop.add("YouTube - Google Chrome", CHROME)
    return fake


def test_a_video_by_its_name_opens_in_the_tab(jv, link):
    jv.do("play the video tiger 3 trailer")
    assert link.did("open") == [{"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}]
    assert jv.says("Playing Tiger 3")


def test_a_title_you_can_see_opens_without_saying_video(jv, link):
    jv.do("open rust lifetimes explained")
    assert link.did("open") == [{"url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"}]


def test_words_that_arent_a_title_on_the_page_are_left_alone(jv, link):
    jv.do("play music")
    jv.do("open the pod bay doors")
    assert not link.did("open")


def test_a_video_not_on_the_page_plays_youtubes_top_result(jv, link):
    jv.net.on(r"youtube\.com/results\?search_query=dune\+part\+two",
              '..."adSlotRenderer":{"videoId":"zzzzzzzzzzz"}..."videoRenderer":{"videoId":"Way9Dexny3w",...')
    jv.do("play the video dune part two")
    assert not link.did("open")
    assert jv.shell.ran(r"watch\?v=Way9Dexny3w") and jv.says("Playing dune part two on YouTube")


def test_watch_on_youtube_offline_hands_on_to_the_search_page(jv, link):
    jv.do("watch dune part two on youtube")
    assert jv.shell.ran(r"youtube\.com/results\?search_query=dune")


def test_the_third_video_counts_the_ones_on_screen(jv, link):
    jv.do("play the third video")
    assert link.did("open") == [{"url": "https://www.youtube.com/watch?v=ccccccccccc"}]


def test_numbered_videos_then_a_number(jv, link):
    jv.do("number the videos")
    assert link.did("label") == [{"urls": [it["url"] for it in ITEMS[:3]]}]
    assert jv.says("Numbered 3")
    jv.do("play 2")
    assert link.did("open") == [{"url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"}]


def test_a_bare_number_with_nothing_numbered_isnt_a_video(jv, link):
    jv.do("play two")
    assert not link.did("open")


def test_no_extension_says_how_to_get_it(jv, monkeypatch):
    jv.desktop.add("YouTube - Google Chrome", CHROME)
    jv.do("play the second video")
    assert jv.says("JARVIS Link")


def test_full_screen_it_in_chrome_is_the_videos_own(jv, link):
    link.replies["fullscreen"] = lambda a: {"result": "on"}
    jv.do("full screen it")
    assert link.did("fullscreen") == [{"mode": "on"}]
    assert not jv.desktop.did("state")


def test_full_screen_with_no_video_fills_the_screen_with_the_window(jv, link):
    link.replies["fullscreen"] = lambda a: {"result": "none"}
    jv.do("make it full screen")
    assert jv.desktop.did("state") == [("YouTube - Google Chrome", 1, "_NET_WM_STATE_FULLSCREEN")]


def test_full_screen_it_outside_the_browser_is_the_window_in_front_not_one_called_it(jv):
    jv.desktop.add("Jetson/GitHub - Chromium", "chromium Chromium", front=False)
    jv.desktop.add("Notes", "gedit Gedit")
    jv.do("full screen it")
    jv.do("exit full screen")
    assert jv.desktop.did("state") == [("Notes", 1, "_NET_WM_STATE_FULLSCREEN"),
                                       ("Notes", 0, "_NET_WM_STATE_FULLSCREEN")]


def test_maximise_it_is_the_window_in_front(jv):
    jv.desktop.add("GitHub", "gedit Gedit", front=False)
    jv.desktop.add("Notes", "gedit Gedit")
    jv.do("maximise it")
    assert jv.desktop.did("state")[0][0] == "Notes"


def test_skip_and_speed_on_any_sites_video(jv, link):
    link.replies["video"] = lambda a: {"result": {"time": 10, "speed": 1}}
    jv.do("skip 30 seconds")
    jv.do("video speed 1.5")
    assert link.did("video") == [{"do": "seek", "by": 30.0}, {"do": "speed", "to": 1.5}]


def test_a_tab_by_name_through_the_extension(jv, link):
    link.replies["tabs"] = {"tabs": [{"id": 7, "windowId": 1, "title": "Inbox - Gmail", "url": "", "active": False},
                                     {"id": 9, "windowId": 1, "title": "Jetson forum", "url": "", "active": True}]}
    jv.do("switch to the gmail tab")
    assert link.did("focus_tab") == [{"tab": 7}]
    assert not jv.desktop.did("keys")


@pytest.mark.parametrize("said, title", [("tigar 3 trailer", "Tiger 3 | Official Trailer"),
                                         ("rust life times", "Rust lifetimes explained")])
def test_a_title_heard_slightly_wrong_still_matches(said, title):
    items = [{"title": title, "url": "u"}, {"title": "Something else entirely", "url": "v"}]
    assert videos.best(said, items) is items[0]


def test_a_lone_word_must_be_the_whole_title_when_video_wasnt_said():
    items = [{"title": "Settings for your account", "url": "u"}]
    assert videos.best("settings", items, strict=True) is None
