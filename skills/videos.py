"""Videos, links and tabs in your own Chrome, through the JARVIS Link extension (tools/chrome-link,
system/chrome_link.py): "play the video tiger 3 trailer", "open the third video", "number the videos" then
"play 4", "open <a title you can see>", "full screen it", "skip 10 seconds", "video speed 1.5",
"watch X on youtube" (the top result, not the search page). Page titles only ever match what you said."""
import difflib
import re
import time
import urllib.parse
import urllib.request

from skills.base import Skill
from skills.browser_tabs import BROWSERS
from skills.screen import front_window
from skills.studybuddy import NUM, N, SPEED_WORDS, SPEEDS
from system import chrome_link, desktop
from system.browser import BrowserAction

ORDINALS = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4, "fifth": 5, "5th": 5, "five": 5, "sixth": 6, "6th": 6, "six": 6,
            "seventh": 7, "7th": 7, "seven": 7, "eighth": 8, "8th": 8, "eight": 8, "ninth": 9, "9th": 9,
            "nine": 9, "tenth": 10, "10th": 10, "ten": 10, "last": -1}
NTH = r"(\d+|%s)" % "|".join(sorted(ORDINALS, key=len, reverse=True))
OPEN = r"(?:open|play|watch|start|put on|click(?: on)?)"
# Words around "full screen" that still mean the thing you're looking at ("make the video full screen").
FILLER = {"make", "put", "turn", "set", "go", "it", "this", "that", "the", "video", "player", "to", "in", "into",
          "mode", "please", "now", "on", "a", "window", "page", "tab", "can", "you", "jarvis", "full", "screen"}
EXIT = {"exit", "leave", "close", "off", "out", "of", "get", "escape", "undo", "stop", "quit", "no", "not", "un",
        "back", "normal", "from"}
STOP_WORDS = {"the", "a", "an", "video", "of", "on", "in", "to", "and", "by", "for", "with", "about", "called",
              "named", "titled", "that", "this", "one", "link", "please", "page", "screen"}
NOT_A_NAME = re.compile(r"^(?:it|this|that|music|some music|the music|something|anything|songs?|a song|"
                        r"some songs|me|again|everything|all|more|next|previous)$", re.I)
LABEL_SECONDS = 300


def words(text):
    return re.findall(r"[a-z0-9ऀ-ॿ]+", text.lower())


def score(said, title):
    """How much of what you said is in the title: 1.0 = every word (a word heard slightly wrong still counts)."""
    want = [w for w in words(said) if w not in STOP_WORDS]
    have = words(title)
    if not want or not have:
        return 0.0
    if "".join(want) in "".join(have):  # "rust life times" / "lifetimes": speech splits and joins words
        return 1.0
    hit =sum(1 for w in want if w in have or (len(w) > 3 and difflib.get_close_matches(w, have, n=1, cutoff=0.8)))
    return hit / len(want)


def best(said, items, strict=False):
    """The item whose title is what you said, or None. strict (no "video" said): a lone word must be the whole title."""
    want = [w for w in words(said) if w not in STOP_WORDS]
    if not want:
        return None
    scored = [(score(said, it["title"]), it) for it in items]
    scored = [(s, it) for s, it in scored if s >= (0.85 if strict else 0.75)]
    if strict and len(want) == 1:
        scored = [(s, it) for s, it in scored if [w for w in words(it["title"]) if w not in STOP_WORDS] == want]
    if not scored:
        return None
    # Ties: the one on screen, then a video, then the shortest title (closest to just what you said).
    return max(scored, key=lambda p: (p[0], p[1].get("seen", False), p[1].get("video", False),
                                      -len(p[1]["title"])))[1]


def on_screen_videos(items):
    """What "the third video" counts: videos you can see, in page order; links when the page has no videos."""
    shown = [it for it in items if it.get("seen")]
    return [it for it in shown if it.get("video")] or shown[:40]


def top_result(query):
    """YouTube's first video for `query`, or None.
    shortcut: reads YouTube's results page (no API key); if YouTube changes it, the search page opens instead."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            html = r.read(4000000).decode("utf-8", "ignore")
    except OSError:
        return None
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html)  # not ads ("adSlotRenderer")
    return "https://www.youtube.com/watch?v=" + m.group(1) if m else None


def _short(title):
    return title if len(title) <= 60 else title[:57].rsplit(" ", 1)[0] + "..."


class Videos(Skill):
    group = "videos and chrome"
    does = ("Finds a video or link on the Chrome page you're looking at by its name or number and opens it; "
            "full screen, skip and speed for the video; the top YouTube result for a name")
    phrases = ("play the video <name>", "open <name> (a title on the Chrome page)", "open the third video",
               "number the videos", "play <n> (after number the videos)", "full screen it", "exit full screen",
               "skip <n> seconds (the video in Chrome)", "video speed <n>", "watch <name> on youtube")
    gives = "the video or page opens, or the player changes"
    permission = "auto"
    asks = ""
    checks = "not checked: Chrome doesn't say what the page did"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.labelled = None  # (time, items) after "number the videos"

    def patterns(self):
        return [
            (r"(.*?)\bfull ?-?screen\b(.*)", self.fullscreen),
            (r"(?:number|label|tag) (?:the |all )?(?:videos|links)(?: on (?:the |this )?(?:page|screen))?|"
             r"(?:show|put)(?: me)? (?:the )?numbers(?: on (?:the )?(?:videos|links|page|screen))?",
             lambda m: self.label()),
            (r"(?:hide|remove|clear) (?:the )?numbers", lambda m: self.unlabel()),
            (r"%s(?: the)? %s (?:video|link)(?: on (?:the |this )?(?:page|screen))?" % (OPEN, NTH), self.nth),
            (r"%s(?: number| no\.?| video number| link number| video)? (\d+|%s)" % (
                OPEN, "|".join(sorted(ORDINALS, key=len, reverse=True))), self.number),
            (r"(?:play|watch|put on)(?: the)? (.+?) (?:on|from) youtube", self.youtube),
            (r"%s(?: the| a| that| this)? video(?: called| named| titled| about| of| on)? (.+)" % OPEN, self.by_name),
            (r"%s(?: the| that)? (.+)" % OPEN, self.on_page),
            (r"(?:skip|jump|go|fast forward|move)(?: ahead| forward| forwards| on)?(?: by)? %s (seconds?|secs?|"
             r"minutes?|mins?)(?: ahead| forward| on)?(?: in the video)?" % N, lambda m: self.seek(m, 1)),
            (r"(?:go back|rewind|jump back|skip back|back)(?: by)? %s (seconds?|secs?|minutes?|mins?)(?: in the "
             r"video)?|(?:go|jump|skip) %s (seconds?|secs?|minutes?|mins?) back(?:wards)?" % (N, N),
             lambda m: self.seek(m, -1)),
            (r"(?:set |change )?(?:the )?(?:video|playback) speed(?: to)? (%s)(?: ?x| times)?|"
             r"play (?:it |the video )?at (%s)(?: ?x| times)?(?: speed)?" % (SPEED_WORDS, SPEED_WORDS), self.speed),
            (r"(speed up|slow down) (?:the )?(?:video|playback)", self.speed_step),
        ]

    # --- the page ---

    def _browser(self):
        win = front_window()
        return win if win and any(b in win.wm_class for b in BROWSERS) else None

    def _page(self):
        """What's on the Chrome tab in front, or None (no browser in front, or JARVIS Link isn't installed)."""
        return chrome_link.page() if self._browser() else None

    def _no_link(self):
        self.say("I can't see inside Chrome yet. Install JARVIS Link: run tools/chrome-link/install.py, then "
                 "load that folder in chrome://extensions.")

    def _open(self, item):
        chrome_link.call("open", url=item["url"])
        self.labelled = None
        self.say(("Playing %s." if item.get("video") else "Opening %s.") % _short(item["title"]))

    def on_page(self, m):
        """"open <a title you can see>": only when Chrome is in front and the title is a clear match."""
        said = m.group(1).strip()
        if NOT_A_NAME.match(said) or self.j.apps.find(said) or self.j.browser.site(said):
            return False
        page = self._page()
        item = page and best(said, page["items"], strict=True)
        if not item:
            return False
        self._open(item)

    def by_name(self, m):
        """"play the video <name>": on the page you're looking at, else YouTube's top result."""
        said = m.group(1).strip()
        page = self._page()
        item = page and best(said, page["items"])
        if item:
            return self._open(item)
        return self._youtube(said)

    def youtube(self, m):
        return self._youtube(re.sub(r"^(?:some|me) ", "", m.group(1).strip(), flags=re.I))

    def _youtube(self, said):
        url = top_result(said)
        if not url:
            return False  # offline or YouTube changed: the browser opens the search page instead
        self.j.executor.run(BrowserAction([url], None, said).shell(self.j.registry.browsers),
                            reply="Playing %s on YouTube." % said, window=True)

    def _listed(self):
        """The videos "the third one" / "play 3" count: the numbered ones while their numbers are up."""
        if self.labelled and time.time() - self.labelled[0] < LABEL_SECONDS:
            return self.labelled[1]
        page = self._page()
        return None if page is None else on_screen_videos(page["items"])

    def _pick(self, n):
        items = self._listed()
        if items is None:
            return self._no_link() if self._browser() else False
        if not items:
            self.say("I don't see any videos on this page.")
            return
        if n == -1:
            n = len(items)
        if not 1 <= n <= len(items):
            self.say("There %s only %d on screen." % ("is" if len(items) == 1 else "are", len(items)))
            return
        self._open(items[n - 1])

    def nth(self, m):
        word = m.group(1).lower()
        return self._pick(int(word) if word.isdigit() else ORDINALS[word])

    def number(self, m):
        """"play 3", "open number 3": the numbers on screen, or the third video when you said "video"/"number"."""
        word = m.group(1).lower()
        if not (self.labelled and time.time() - self.labelled[0] < LABEL_SECONDS) and \
                not re.search(r"\b(?:number|no|video|link)\b", m.group(0), re.I):
            return False  # "play one" with nothing numbered: not ours
        return self._pick(int(word) if word.isdigit() else ORDINALS[word])

    def label(self):
        page = self._page()
        if page is None:
            return self._no_link() if self._browser() else self.say("Chrome isn't in front.")
        items = on_screen_videos(page["items"])[:30]
        if not items:
            self.say("I don't see anything to number on this page.")
            return
        chrome_link.call("label", urls=[it["url"] for it in items])
        self.labelled = (time.time(), items)
        self.say(["Numbered %d. Say play and a number." % len(items),
                  "Numbered %d of them. Say play and a number." % len(items),
                  "%d numbered. Say play and a number." % len(items)])

    def unlabel(self):
        if not self.labelled:
            return False
        chrome_link.call("label", urls=[])
        self.labelled = None
        self.say(["Numbers gone.",
                  "Numbers cleared away.", "Cleared the numbers."])

    # --- the player ---

    def fullscreen(self, m):
        said = set(words(m.group(1) + " " + m.group(2)))
        if said - FILLER - EXIT:
            return False  # "full screen screenshot", "full screen chrome": someone else's
        off = bool(said & EXIT)
        win = front_window()
        if not win:
            return False
        if self._browser():
            r = chrome_link.call("fullscreen", timeout=4, mode="off" if off else "on")
            if r and r.get("result") == "on":
                return
        # No video (or no JARVIS Link): the window itself
        desktop.set_state(win, 0 if off else 1, "_NET_WM_STATE_FULLSCREEN")

    def _video(self, **cmd):
        """True when the video in Chrome took it; False (hand it on) when there's none or no JARVIS Link."""
        if not self._browser():
            return False
        r = chrome_link.call("video", **cmd)
        return bool(r and isinstance(r.get("result"), dict))

    def seek(self, m, sign):
        n, unit = [x for x in m.groups() if x][:2]
        secs = (NUM[n.lower()] if n.lower() in NUM else float(n)) * (60 if unit.lower().startswith("m") else 1)
        return None if self._video(do="seek", by=sign * secs) else False

    def speed(self, m):
        to = SPEEDS.get(next(x for x in m.groups() if x).lower())
        if to is None or not self._video(do="speed", to=to):
            return False
        self.say("Speed %s." % ("normal" if to == 1 else "%gx" % to))

    def speed_step(self, m):
        return None if self._video(do="speed_step", by=0.25 if m.group(1).lower() == "speed up" else -0.25) else False
