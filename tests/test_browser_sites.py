"""The sites you use (system/browser.py, the sites in config/commands.yaml and your own in
~/.local/share/jarvis/sites.yaml): "open X", "search Y on X", "open Y on X", "open X and search Y", a site's
own pages, your channels and shows, and GitHub with a personal and a work account. Nothing is opened: the
browser commands are only recorded."""
import os

import pytest

import conftest

MINE = """
sites:
  github:
    accounts: {personal: Chetakk, work: chetak-biztel}
    repos: [Chetakk/J.A.R.V.I.S, chetak-biztel/retraining-pipeline, chetak-biztel/o-ring]
  youtube:
    pages: {channel: "https://www.youtube.com/@ChetakKumar-kr6lm", rakazone: "https://www.youtube.com/@RakaZoneGaming"}
  kick:
    pages: {yatra rp: events/yatra-rp, tbone gaming: tbone_gaming}
  hotstar:
    pages: {bigg boss: "in/shows/bigg-boss/1971002586"}
  myflixer:
    url: "https://myflixer.vg"
"""


@pytest.fixture
def mine(jv):
    path = os.path.join(conftest.DATA, "sites.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(MINE)
    jv.j.registry.reload()
    return jv


def opened(jv):
    return [c for c in jv.shell.calls if c.startswith(("xdg-open ", "google-chrome ", "firefox "))]


@pytest.mark.parametrize("said, url", [
    ("open hotstar", "https://www.hotstar.com/in/home"),
    ("open kick", "https://kick.com"),
    ("open twitch", "https://www.twitch.tv"),
    ("open instagram", "https://www.instagram.com"),
    ("open x", "https://x.com/home"),
    ("open twitter", "https://x.com/home"),
    ("open google drive", "https://drive.google.com"),
    ("search bigg boss on hotstar", "https://www.hotstar.com/in/explore?search_query=bigg+boss"),
    ("search rakazone on kick", "https://kick.com/search?query=rakazone"),
    ("open kick and search rakazone", "https://kick.com/search?query=rakazone"),
    ("open youtube and search deva deva song", "https://www.youtube.com/results?search_query=deva+deva+song"),
    ("open zepto and search milk", "https://www.zepto.com/search?query=milk"),
    ("open stableronaldo on twitch", "https://www.twitch.tv/stableronaldo"),
    ("open rakazone on kick", "https://kick.com/rakazone"),
    ("open claude new chat", "https://claude.ai/new"),
    ("open new chat on claude", "https://claude.ai/new"),
    ("open my instagram messages", "https://www.instagram.com/direct/inbox"),
    ("open linkedin jobs", "https://www.linkedin.com/jobs/"),
    ("open my linkedin", "https://www.linkedin.com/in/me/"),
    ("open youtube history", "https://www.youtube.com/feed/history"),
    ("open hotstar watchlist", "https://www.hotstar.com/in/mypage"),
])
def test_sites_without_claude(jv, said, url):
    jv.do(said)
    assert opened(jv) and url in opened(jv)[0]
    assert not jv.claude.calls


@pytest.mark.parametrize("verb", ["open", "go to", "launch", "show", "show me", "take me to", "bring up", "pull up"])
def test_every_way_of_saying_open(jv, verb):
    jv.do("%s kick" % verb)
    assert opened(jv) == ["xdg-open https://kick.com"]
    jv.clear()
    jv.do("%s kick and search rakazone" % verb)
    assert opened(jv) == ["xdg-open 'https://kick.com/search?query=rakazone'"]
    jv.clear()
    jv.do("%s rakazone on kick" % verb)
    assert opened(jv) == ["xdg-open https://kick.com/rakazone"]


def test_bring_up_an_open_kick_tab_is_that_window(jv):
    jv.desktop.add("Rakazone Live Stream - Watch Live on KICK - Google Chrome", "google-chrome Google-chrome")
    jv.do("bring up kick")
    assert not opened(jv)


def test_the_browser_you_name(jv):
    jv.do("open hotstar in firefox")
    assert opened(jv) == ["firefox --new-window https://www.hotstar.com/in/home"]


def test_a_site_opens_in_a_new_window_on_the_empty_workspace(jv, monkeypatch):
    """Not a tab in the browser window already open on this workspace."""
    from system import browser, desktop
    went = []
    monkeypatch.setattr(browser, "default_browser", lambda browsers: "chrome")
    monkeypatch.setattr(desktop, "go_to_jarvis_workspace", lambda: went.append(True))
    jv.do("open youtube")
    assert opened(jv) == ["google-chrome --new-window https://www.youtube.com"] and went


def test_twitter_isnt_libreoffice_writer(jv, monkeypatch):
    launched = []
    monkeypatch.setattr(jv.j.apps, "launch", lambda app, paths=(): launched.append(app["name"]) or True)
    jv.do("open twitter")
    assert launched == [] and opened(jv) == ["xdg-open https://x.com/home"]


def test_discord_is_the_app_unless_a_browser_is_named(jv, monkeypatch):
    launched = []
    monkeypatch.setattr(jv.j.apps, "find", lambda name: {"name": "Discord", "process": "discord"}
                        if "discord" in name.lower() else None)
    monkeypatch.setattr(jv.j.apps, "launch", lambda app, paths=(): launched.append(app["name"]) or True)
    jv.do("open discord")
    assert launched == ["Discord"] and not opened(jv)
    jv.clear()
    jv.do("open discord in chrome")
    assert launched == ["Discord"] and opened(jv) == ["google-chrome --new-window https://discord.com/channels/@me"]


@pytest.mark.parametrize("said, url", [
    ("open bigg boss on hotstar", "https://www.hotstar.com/in/shows/bigg-boss/1971002586"),
    ("open yatra rp on kick", "https://kick.com/events/yatra-rp"),
    ("open tbone gaming on kick", "https://kick.com/tbone_gaming"),
    ("open rakazone youtube channel", "https://www.youtube.com/@RakaZoneGaming"),
    ("open rakazone on youtube", "https://www.youtube.com/@RakaZoneGaming"),
    ("open my youtube channel", "https://www.youtube.com/@ChetakKumar-kr6lm"),
    ("open myflixer", "https://myflixer.vg"),
    ("open jarvis repo on github", "https://github.com/Chetakk/J.A.R.V.I.S"),
    ("open jarvis on github", "https://github.com/Chetakk/J.A.R.V.I.S"),
    ("open the retraining pipeline pull requests", "https://github.com/chetak-biztel/retraining-pipeline/pulls"),
    ("open o-ring actions on github", "https://github.com/chetak-biztel/o-ring/actions"),
    ("open my work github", "https://github.com/chetak-biztel"),
    ("open biztel github", "https://github.com/chetak-biztel"),
    ("open my personal github repos", "https://github.com/Chetakk?tab=repositories"),
])
def test_your_own_pages(mine, said, url):
    mine.do(said)
    assert opened(mine) == ["xdg-open %s" % url] or opened(mine) == ["xdg-open '%s'" % url]
    assert not mine.questions


@pytest.mark.parametrize("said, url", [
    # other ways of saying "open", and Hindi
    ("fire up youtube", "https://www.youtube.com"),
    ("i need youtube open", "https://www.youtube.com"),
    ("can i get youtube", "https://www.youtube.com"),
    ("youtube kholo", "https://www.youtube.com"),
    ("youtube pe le chalo", "https://www.youtube.com"),
    ("open insta", "https://www.instagram.com"),
    ("open claude dot ai", "https://claude.ai"),
    ("open www dot hotstar dot com", "https://www.hotstar.com"),
    # searches
    ("youtube pe lofi search karo", "https://www.youtube.com/results?search_query=lofi"),
    ("lofi youtube pe chalao", "https://www.youtube.com/results?search_query=lofi"),
    ("play deva deva song", "https://www.youtube.com/results?search_query=deva+deva+song"),
    ("order milk on zepto", "https://www.zepto.com/search?query=milk"),
    ("see if zepto has milk", "https://www.zepto.com/search?query=milk"),
    ("cats google karo", "https://www.google.com/search?q=cats"),
    ("search the web for cats", "https://www.google.com/search?q=cats"),
    ("look for ML engineer jobs", "https://www.linkedin.com/jobs/search/?keywords=ML+engineer"),
    ("where is the nearest pizza place", "https://www.google.com/maps/search/nearest+pizza+place"),
    # saved names without the site's, and pages said other ways
    ("watch bigg boss", "https://www.hotstar.com/in/shows/bigg-boss/1971002586"),
    ("bigg boss lagao", "https://www.hotstar.com/in/shows/bigg-boss/1971002586"),
    ("watch yatra rp", "https://kick.com/events/yatra-rp"),
    ("open the tbone stream", "https://kick.com/tbone_gaming"),
    ("go to rakazone's youtube", "https://www.youtube.com/@RakaZoneGaming"),
    ("open my channel", "https://www.youtube.com/@ChetakKumar-kr6lm"),
    ("watch shorts", "https://www.youtube.com/shorts"),
    ("new claude chat", "https://claude.ai/new"),
    ("open my linkedin inbox", "https://www.linkedin.com/messaging/"),
    ("navigate to youtube", "https://www.youtube.com"),
    # GitHub
    ("open my company github", "https://github.com/chetak-biztel"),
    ("open chetakk's github", "https://github.com/Chetakk"),
    ("make a new github repo", "https://github.com/new"),
    ("open the pull requests for jarvis", "https://github.com/Chetakk/J.A.R.V.I.S/pulls"),
    ("did jarvis's build pass", "https://github.com/Chetakk/J.A.R.V.I.S/actions"),
])
def test_other_ways_of_saying_it(mine, said, url):
    mine.do(said)
    assert opened(mine) == ["xdg-open %s" % url] or opened(mine) == ["xdg-open '%s'" % url]
    assert not mine.claude.calls


@pytest.mark.parametrize("said", ["what's on youtube", "turn the volume up on youtube", "open home"])
def test_not_a_site(jv, said):
    jv.do(said)
    assert not opened(jv)


@pytest.mark.parametrize("answer, url", [("work", "https://github.com/chetak-biztel"),
                                         ("the biztel one", "https://github.com/chetak-biztel"),
                                         ("personal", "https://github.com/Chetakk"),
                                         ("chetakk", "https://github.com/Chetakk")])
def test_my_github_asks_which(mine, answer, url):
    mine.do("open my github", answer)
    assert mine.questions == ["Your personal GitHub, or the work one?"]
    assert opened(mine) == ["xdg-open %s" % url]


def test_no_answer_opens_nothing(mine):
    mine.do("open my github repos")
    assert mine.questions and not opened(mine)


def test_without_your_file_the_sites_still_work(jv):
    jv.do("open my github")  # no accounts known: not something to guess at
    assert not any("github.com/" in c for c in opened(jv))
    jv.clear()
    jv.do("open bigg boss on hotstar")  # not a page you've saved: searched for on the site
    assert opened(jv) == ["xdg-open 'https://www.hotstar.com/in/explore?search_query=bigg+boss'"]


def test_a_broken_file_is_ignored(jv):
    path = os.path.join(conftest.DATA, "sites.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("sites: [unclosed")
    jv.j.registry.reload()
    jv.do("open kick")
    assert opened(jv) == ["xdg-open https://kick.com"]


def test_a_site_name_inside_something_else_isnt_the_site(jv):
    for said in ("open the x file", "open project x"):
        jv.clear()
        jv.do(said)
        assert not any("x.com" in c for c in opened(jv))


def test_youtube_playlists_arent_a_study_buddy_group(mine):
    mine.do("open my youtube playlists")
    assert opened(mine) == ["xdg-open https://www.youtube.com/feed/playlists"]


@pytest.mark.parametrize("said, cmd", [
    ("open my work github account", "firefox --new-window https://github.com/chetak-biztel"),
    ("open the o-ring repo", "firefox --new-window https://github.com/chetak-biztel/o-ring"),
    ("open pdi demo workflow on github", "firefox --new-window https://github.com/biztel-engg/pdi_demo_workflow"),
    ("open my personal github", "xdg-open https://github.com/Chetakk"),
    ("open my work github in chrome", "google-chrome --new-window https://github.com/chetak-biztel"),
])
def test_work_github_opens_where_it_is_logged_in(mine, said, cmd):
    """The work account is logged in on Firefox: its pages and repos open there unless a browser is named."""
    with open(os.path.join(conftest.DATA, "sites.yaml"), "w") as f:
        f.write(MINE.replace("chetak-biztel/o-ring]", "chetak-biztel/o-ring, biztel-engg/pdi_demo_workflow]\n"
                             "    orgs: {work: [biztel-engg]}\n    browsers: {work: firefox}"))
    mine.j.registry.reload()
    mine.do(said)
    assert opened(mine) == [cmd]


@pytest.mark.parametrize("said", ["I said open my work github account", "i told you open my work github",
                                  "like I said, open my work github"])
def test_i_said_is_sorry_then_the_request(mine, said):
    """"I said X": JARVIS got it wrong or missed it. It apologises and does X, "I said" left out."""
    mine.do(said)
    assert opened(mine) == ["xdg-open https://github.com/chetak-biztel"]
    assert any("Apologies." in lines for lines in mine.spoken)


@pytest.mark.parametrize("said, cmd", [
    ("open youtube in my work browser", "firefox --new-window https://www.youtube.com"),
    ("open youtube in the office browser", "firefox --new-window https://www.youtube.com"),
    ("open gmail in my personal browser", "google-chrome --new-window https://mail.google.com"),
    ("open youtube in the browser", "xdg-open https://www.youtube.com"),
])
def test_work_and_personal_browser(jv, said, cmd):
    """"browsers: {work: firefox, personal: chrome}" in sites.yaml: "my work browser" is Firefox."""
    with open(os.path.join(conftest.DATA, "sites.yaml"), "w") as f:
        f.write("browsers: {work: firefox, personal: chrome}\n")
    jv.j.registry.reload()
    jv.do(said)
    assert opened(jv) == [cmd]
