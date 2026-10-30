"""Study Buddy (~/Projects/study-budd): everything its app does, by voice.

Talks to its local API (http://127.0.0.1:7314). The open app window is steered through the app's remote channel
(POST /api/remote: go to a page, play/pause/seek/speed, a note at the video's current time, mark it done), so
none of it needs the window in front.

  carry on: "continue studying", "continue python", "next video", "previous video", "what should I study"
  player:   "pause the video", "play the lecture", "skip ahead 30 seconds", "go back 10 seconds",
            "play at 1.5x", "speed up", "normal speed", "video focus mode", "mark this video done",
            "skip this video" (doesn't count it, plays the next), "unskip it"
            (bare "pause", "rewind", "note ..." count as the video's only while the app is the window in front)
  notes:    "add a study note: ...", "notes on this video", "search my study notes for gradients",
            "delete my last study note"
  cards:    "quiz me (on python)", "how many cards are due", "make a flashcard: what is X, answer: Y"
  progress: "how far am I in machine learning", "how much is left in python", "my study streak",
            "how much have I studied in study buddy today", "show my study progress"
  problems: "what's the next leetcode problem", "mark it solved", "mark two sum as solved", "open the problem"
  groups:   "create a study group called Rust", "rename the python group to Python basics", "delete the rust
            group", "make python red", "set my python goal to 30 minutes", "add python to today",
            "remove cloud from today", "move devops to the top"
  library:  "add this playlist to python" / "add this article to deep learning" / "add this sheet to leetcode"
            (the browser tab you're on, or a link you copied), "check python for new videos", "back up study buddy"
  pages:    "open my flashcards", "open the study library", "show my study notes", "open the python group"
"""
import difflib
import json
import os
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request

from skills.base import Skill
from skills.daytracker import spoken
from skills.helpers import and_list, clipboard
from system import desktop

SB = os.environ.get("STUDY_BUDDY_URL", "http://127.0.0.1:7314")
APP_ID = "mfemhkgammpchekfkcindllglgdddajg"  # the installed app (chrome --app-id)
APP_FILE = os.path.expanduser("~/.local/share/applications/chrome-%s-Default.desktop" % APP_ID)
COLORS = {"blue": "blue", "orange": "orange", "aqua": "aqua", "cyan": "aqua", "teal": "aqua", "yellow": "yellow",
          "magenta": "magenta", "pink": "magenta", "green": "green", "violet": "violet", "purple": "violet",
          "red": "red"}
COLOR_WORDS = "|".join(COLORS)
SPEEDS = {"half": 0.5, "0.5": 0.5, ".5": 0.5, "0.75": 0.75, ".75": 0.75, "normal": 1, "1": 1, "one": 1,
          "1.25": 1.25, "1.5": 1.5, "one and a half": 1.5, "one point five": 1.5, "1.75": 1.75, "2": 2, "two": 2,
          "double": 2}
SPEED_WORDS = "|".join(sorted((re.escape(k) for k in SPEEDS), key=len, reverse=True))
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20,
       "thirty": 30, "forty five": 45, "sixty": 60, "ninety": 90}
N = r"(\d+|%s)" % "|".join(sorted(NUM, key=len, reverse=True))
RATINGS = ["again", "hard", "good", "easy"]
STOP = re.compile(r"^(?:stop|that'?s (?:enough|all)|enough|finish|end|quit|done|i'?m done)\b", re.I)
MAX_QUIZ = 30
VIDEO = r"(?:the |this |my )?(?:video|lecture|lesson|study video|class)"
IN_SB = r"(?: (?:in|on|from) study ?budd?y)?"
BROWSERS = ("google-chrome", "chromium", "firefox", "brave")


class SBDown(Exception):
    pass


class SBError(Exception):
    pass


def squash(s):
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("&", "and"))


def best_match(name, items, key):
    """The item whose `key` is what `name` says: "gen ai" -> "GenAI & LLMs", "ml" -> "Machine Learning"."""
    s = squash(re.sub(r"^(?:the|my|our) ", "", name.strip().lower()))
    if len(s) < 2:
        return None
    best, top = None, 0.0
    for it in items:
        full = key(it)
        k = squash(full)
        initials = "".join(w[0] for w in re.findall(r"[a-z0-9]+", full.lower()) if w != "and")
        if s in (k, initials):
            score = 1.0
        elif k.startswith(s) and len(s) >= 3:
            score = 0.9
        elif s in k and len(s) >= 4:
            score = 0.82
        else:
            score = difflib.SequenceMatcher(None, s, k).ratio()
        if score > top:
            best, top = it, score
    return best if top >= 0.8 else None


def cap(s):
    return s[:1].upper() + s[1:]


def mins_said(n, unit):
    n = NUM.get(n.lower(), None) if n.lower() in NUM else float(n)
    return n * (60 if unit.lower().startswith("h") else 1)


class StudyBuddy(Skill):
    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.last_item = None  # the problem/video/article last talked about: "mark it solved"
        self.last_skipped = None

    def patterns(self):
        g = r"(?:the |my )?(.+?)(?: group| course| playlists?)?"
        v = VIDEO
        return [
            # --- the player (only while a Watch page is open) ---
            (r"(pause|stop|play|resume|unpause|continue) %s%s|(pause|play|resume) study ?budd?y" % (v, IN_SB),
             self.play),
            (r"(pause|play|resume|unpause)(?: it)?", lambda m: self.play(m, front=True)),
            (r"(?:skip|jump|go|fast forward|move)(?: ahead| forward| forwards| on)?(?: by)? %s (seconds?|secs?|minutes?|"
             r"mins?)(?: ahead| forward| on)?(?: in %s)?" % (N, v), lambda m: self.seek(m, 1)),
            (r"(?:go back|rewind|jump back|skip back|back)(?: by)? %s (seconds?|secs?|minutes?|mins?)(?: in %s)?|"
             r"(?:go|jump|skip) %s (seconds?|secs?|minutes?|mins?) back(?:wards)?" % (N, v, N), lambda m: self.seek(m, -1)),
            (r"(rewind|skip ahead|fast forward)(?: (?:the )?video)?", self.seek_bare),
            (r"(?:play|set (?:the )?(?:video |playback )?speed|speed)(?: it| the video)?(?: at| to)? (%s)"
             r"(?: ?x| times| speed)?|(normal|double|half) speed" % SPEED_WORDS, self.speed),
            (r"(speed up|slow down|faster|slower)(?: the)?(?: video| lecture| playback)?", self.speed_step),
            (r"(?:play |go to |open )?(?:the )?(next|previous|last) (?:video|lecture|lesson)%s|"
             r"(?:go )?back a (video|lecture)" % IN_SB, self.next_video),
            (r"(?:video|study|lecture) focus mode(?: on)?|(?:make )?(?:the )?video full ?screen|"
             r"hide everything (?:but|except) the video", lambda m: self.key("f", "Focus mode.")),
            (r"(?:exit|leave|close|turn off) (?:video |study |lecture )?focus mode|(?:exit|leave) full ?screen",
             lambda m: self.key("Escape", "", front=not re.search(r"video|study|lecture", m.group(0)))),
            (r"(?:mark )?%s (?:as )?(?:done|watched|finished|complete|completed)|mark (?:it |this )?(?:as )?watched|"
             r"i(?:'ve| have)? (?:finished|watched|completed|done with) %s" % (v, v), lambda m: self.video_done()),
            (r"skip %s|don'?t count %s|(?:exclude|ignore) %s" % (v, v, v), lambda m: self.skip_video()),
            (r"(?:unskip|count) (?:it|that|this(?: video)?|the video)(?: again)?", lambda m: self.unskip()),

            # --- notes ---
            (r"(?:add |take |make |write )?(?:a )?(?:study |video |lecture )note(?: (?:on|to|for) %s)?[:,]? (.+)|"
             r"(?:add |take |make )?(?:a )?note (?:on|to|for) %s[:,]? (.+)" % (v, v), self.note),
            (r"(?:add |take |make )?(?:a )?note[:,]? (.+)", lambda m: self.note(m, front=True)),
            (r"(?:read|show|what are|what did i write in)(?: me)? (?:my )?notes (?:on|for|from|in) %s|"
             r"(?:my )?notes (?:on|for) %s" % (v, v), lambda m: self.video_notes()),
            (r"(?:search|look through|find in) (?:my )?(?:study|video|lecture) notes (?:for|about) (.+)|"
             r"find (.+) in my (?:study|video|lecture) notes|what did i (?:note|write) about (.+?)(?: in study buddy)?",
             self.search_notes),
            (r"(?:delete|remove|undo) (?:my |the )?last (?:study |video |lecture )note", lambda m: self.delete_note()),

            # --- flashcards ---
            (r"quiz me(?: on (.+))?|test me(?: on (.+))?|(?:let'?s |start (?:a |my )?|do (?:a |my )?)?"
             r"(?:flash ?card|card) review(?: (?:for|on) (.+))?|review (?:my )?(?:flash ?)?cards(?: (?:for|on|in) (.+))?",
             self.quiz),
            (r"how many (?:flash ?)?cards (?:are |do i have )?(?:due|to review|left|waiting)(?: today)?|"
             r"(?:any )?(?:flash ?)?cards due(?: today)?|how many cards are (?:pending|waiting for (?:my )?review)",
             lambda m: self.cards_due()),
            (r"(?:make|add|create|new)(?: a)?(?: new)? (?:flash ?)?card[:,]? (.+?)"
             r"(?:[,.;]? (?:answer|back|the answer is|and the answer is)[:,]? (.+))?", self.add_card),
            (r"(?:open|show)(?: me)? (?:the )?(?:flash ?card )?review page|open (?:the )?card review",
             lambda m: self.go("#/review", "Here's your review.")),

            # --- carrying on, and what to study ---
            (r"what (?:should|shall|do) i (?:study|learn|watch)(?: now| next| today)?|what'?s next (?:to study|in study "
             r"buddy)|(?:any )?study suggestions?|what should i do in study buddy", lambda m: self.suggest()),
            (r"(?:continue|resume|keep|carry on|pick up)(?: with)?(?: my| the)? (?:studying|study|learning|course|"
             r"lectures?|lessons?|studies)(?: where i left off)?%s|(?:pick up )?where i left off(?: in study buddy)?|"
             r"(?:play|watch) (?:my |the )?next (?:study video|lecture|lesson)" % IN_SB, lambda m: self.resume()),
            (r"(?:continue|resume|carry on with|study|learn|watch) (?:some |my |the )?(.+?)(?: now| next)?",
             self.resume_group),
            (r"(?:what'?s|how long is|how many days is|what is) my (?:study )?streak|(?:my )?study streak",
             lambda m: self.streak()),
            (r"how (?:much|long) (?:have i|did i) (?:studied|study|watched|learned|learnt)(?: in| on) study ?budd?y"
             r"(?: today)?|study ?budd?y (?:today|summary|status)|how am i doing (?:in|on|with) (?:study ?budd?y|my "
             r"studies|studying)", lambda m: self.today()),
            (r"how (?:far|much) (?:am i|have i done|have i got|did i get|have i watched) (?:in|with|through|into|of) "
             r"(.+)|how much (?:is )?left (?:in|of|for) (.+)|(?:what'?s )?my progress (?:in|on|with) (.+)",
             self.how_far),
            (r"(?:show |what'?s )?(?:me )?my (?:study|study buddy) (?:progress|stats|overview)|"
             r"how (?:far|much) (?:am i|have i done) overall", lambda m: self.overview()),

            # --- problems and articles ---
            (r"what'?s (?:the |my )?next (?:leet ?code |coding )?problem(?: (?:in|for|from) (.+))?|"
             r"(?:give me |show me )?(?:the |my )?next (?:leet ?code |coding )?problem(?: (?:in|for|from) (.+))?",
             self.next_problem),
            (r"open (?:the |that |this )?(?:leet ?code |coding )?problem", lambda m: self.open_item()),
            (r"(?:mark|tick off|check off) (.+?) (?:as )?(solved|read|watched)|i (solved|read|finished reading) (.+)",
             self.mark),

            # --- groups ---
            (r"(?:create|make|add|start)(?: a)?(?: new)? study (?:buddy )?group (?:called |named |for )?(.+)|"
             r"(?:create|make|add)(?: a)?(?: new)? group (?:called |named |for )?(.+?) in study ?budd?y", self.new_group),
            (r"rename (?:the |my )?(.+?) (?:study )?group to (.+)", self.rename_group),
            (r"(?:add|put|show) %s (?:on|to) (?:the )?today(?: page| list)?|(?:remove|take|hide) %s (?:off|from) "
             r"(?:the )?today(?: page| list)?" % (g, g), self.on_today),
            (r"(?:delete|remove) (?:the |my )?(.+?) (?:study )?group(?: from study ?budd?y)?", self.delete_group),
            (r"(?:make|colou?r|change|set|turn) (?:the |my )?(.+?)(?: group)?(?:'?s)?(?: colou?r)? (?:to |into |as )?"
             r"(%s)" % COLOR_WORDS, self.color_group),
            (r"set (?:my |the )?(.+?) (?:daily )?(?:study )?goal to %s (minutes?|mins?|hours?)(?: a day| per day| daily)?"
             % N, self.group_goal),
            (r"(?:remove|clear|turn off|delete) (?:my |the )?(.+?) (?:daily )?(?:study )?goal", self.group_goal),
            (r"(?:move|put) %s (?:to the )?(top|first|bottom|last)(?: of (?:the )?(?:list|today))?" % g, self.move_group),

            # --- the library ---
            (r"(?:add|import|save|put) (?:this|that)(?: youtube)?(?: (playlist|article|post|page|sheet|problem sheet|"
             r"link|blog))? (?:to|into|in|under) (?:my |the )?(.+?)(?: group| reading list| playlists?|"
             r" in study ?budd?y)?|(?:add|import|save) (?:this|that) (playlist|article|sheet|link) to study ?budd?y",
             self.add_link),
            (r"(?:refresh|sync|update|check) (?:the |my )?(.+?)(?: group| playlists?)? for new (?:videos|problems|uploads)|"
             r"(?:refresh|sync|update) (?:the |my )?(.+?) (?:group|playlists?)(?: in study ?budd?y)?", self.sync),
            (r"back ?up study ?budd?y|(?:make|take) a study ?budd?y backup", lambda m: self.backup()),

            # --- pages ---
            (r"(?:open|show|go to)(?: me)? (?:the |my )?(?:study ?budd?y |study )(library|progress|flash ?cards|cards|"
             r"notes|today)(?: page)?|(?:open|show|go to)(?: me)? (?:the |my )?(library|progress|flash ?cards|cards|"
             r"notes|today)(?: page)? in study ?budd?y|(?:open|show)(?: me)? (?:my )?(flash ?cards)", self.page),
            (r"(?:open|show|go to)(?: me)? (?:the |my )?(.+?) (?:study )?(?:group|course|playlists?)(?: in study ?budd?y)?",
             self.open_group),
        ]

    # --- API ---

    def _call(self, method, path, body=None, timeout=4):
        req = urllib.request.Request(SB + "/api" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                raise SBError(json.load(e).get("error") or str(e))
            except ValueError:
                raise SBError(str(e))
        except (urllib.error.URLError, OSError):
            raise SBDown()

    def _guard(fn):
        def wrapped(self, *a, **k):
            try:
                return fn(self, *a, **k)
            except SBDown:
                self.say("Study Buddy isn't running. Say run study buddy to start it.")
            except SBError as e:
                self.say("Study Buddy says: %s." % str(e).rstrip("."))
        wrapped.__name__ = fn.__name__
        return wrapped

    def _send(self, msg):
        """To the open app pages; how many got it."""
        return self._call("POST", "/remote", msg).get("pages", 0)

    def _changed(self):
        """Something changed through the API: the open pages fetch it again."""
        try:
            self._send({"changed": True})
        except (SBDown, SBError):
            pass

    def _watching(self):
        """The video id an open Watch page is on (the newest such page), or None."""
        try:
            pages = self._call("GET", "/remote/pages", timeout=1)
        except (SBDown, SBError):
            return None
        ids = [(p["since"], int(m.group(1))) for p in pages for m in [re.match(r"#/watch/(\d+)", p["at"])] if m]
        return max(ids)[1] if ids else None

    @staticmethod
    def _app_window():
        return desktop.find_window("crx_" + APP_ID)

    def _front(self):
        win, app = desktop.active_window(), self._app_window()
        return bool(win and app and win.id == app.id)

    def go(self, route, reply="", focus=True):
        """Show `route` ("#/watch/34") in the app: the open window moves there, or the app opens on it."""
        try:
            pages = self._call("GET", "/remote/pages", timeout=1)
        except SBDown:
            self.say("Study Buddy isn't running. Say run study buddy to start it.")
            return
        win = self._app_window() or desktop.find_window("study buddy")
        if pages:
            self._send({"go": route})
            if focus and win:
                desktop.focus(win)
            if reply:
                self.say(reply)
            return
        url = SB + "/" + route
        if os.path.exists(APP_FILE):
            cmd = "google-chrome --profile-directory=Default --app-id=%s --app-launch-url-for-shortcuts-menu-item=%s" % (
                APP_ID, shlex.quote(url))
        else:
            cmd = "xdg-open %s" % shlex.quote(url)
        self.j.executor.run(cmd, reply=reply or "Opening Study Buddy.", window=True)

    def _groups(self):
        return self._call("GET", "/groups")

    def _quiet_groups(self):
        """The groups, or [] while Study Buddy is down: for phrases that are only Study Buddy's when they name one of
        its groups ("watch devops" yes, "watch a movie" no), so they're handed on instead of answered."""
        try:
            return self._groups()
        except (SBDown, SBError):
            return []

    def _group(self, name, groups=None):
        name = re.sub(r"\b(?:group|course|playlists?)$", "", name.strip()).strip()
        return best_match(name, self._quiet_groups() if groups is None else groups, lambda g: g["name"])

    # --- the player ---

    def _player(self, cmd, reply="", front=False):
        """A play/pause/seek/speed for the open Watch page. False (hand it on) when there's none, or when a bare
        word like "pause" isn't meant for it (the app isn't the window in front)."""
        if not self._watching() or (front and not self._front()):
            return False
        self._send({"player": cmd})
        if reply:
            self.say(reply)

    def play(self, m, front=False):
        word = next(x for x in m.groups() if x).lower()
        return self._player({"do": "pause" if word in ("pause", "stop") else "play"}, front=front)

    def seek(self, m, sign):
        n, unit = [x for x in m.groups() if x][:2]
        secs = (NUM[n.lower()] if n.lower() in NUM else float(n)) * (60 if unit.lower().startswith("m") else 1)
        return self._player({"do": "seek", "by": sign * secs}, front="video" not in m.group(0))

    def seek_bare(self, m):
        back = m.group(1).lower() == "rewind"
        return self._player({"do": "seek", "by": -10 if back else 10}, front="video" not in m.group(0))

    def speed(self, m):
        said = next(x for x in m.groups() if x).lower()
        to = SPEEDS.get(said)
        if to is None:
            return False
        return self._player({"do": "speed", "to": to}, "Speed %s." % ("normal" if to == 1 else "%gx" % to))

    def speed_step(self, m):
        if not self._watching():
            return False
        if "video" not in m.group(0) and "lecture" not in m.group(0) and not self._front():
            return False
        self._send({"key": ">" if m.group(1).lower() in ("speed up", "faster") else "<"})

    def key(self, key, reply, front=False):
        if not self._watching() or (front and not self._front()):
            return False
        self._send({"key": key})
        if reply:
            self.say(reply)

    @_guard
    def next_video(self, m):
        which = next(x for x in m.groups() if x).lower()
        vid = self._watching()
        if not vid:
            return False  # "next video" on YouTube: the media keys'
        item = self._call("GET", "/items/%d" % vid)
        order = [p for p in item["playlist"] if not p.get("skipped") or p["id"] == vid]
        i = next(n for n, p in enumerate(order) if p["id"] == vid)
        pick = order[i + 1] if which == "next" and i + 1 < len(order) else \
            order[i - 1] if which != "next" and i > 0 else None
        if not pick:
            self.say("That's the %s video in %s." % ("last" if which == "next" else "first", item["sourceTitle"]))
            return
        self.go("#/watch/%d" % pick["id"], focus=False)
        self.say(pick["title"])

    @_guard
    def video_done(self):
        if not self._watching():
            return False  # some other video
        self._send({"done": True})
        self.say("Marked it done.")

    @_guard
    def skip_video(self):
        vid = self._watching()
        if not vid:
            return False
        self._call("POST", "/items/%d/skip" % vid, {"skipped": True})
        self.last_skipped = vid
        item = self._call("GET", "/items/%d" % vid)
        i = next(n for n, p in enumerate(item["playlist"]) if p["id"] == vid)
        nxt = next((p for p in item["playlist"][i + 1:] if not p.get("skipped")), None)
        if nxt:
            self.go("#/watch/%d" % nxt["id"], focus=False)
        self.say("Skipped, it won't count. Say unskip it to undo." + ("" if nxt else " That was the last one."))

    @_guard
    def unskip(self):
        vid = self.last_skipped or self._watching()
        if not vid:
            return False
        self._call("POST", "/items/%d/skip" % vid, {"skipped": False})
        self.last_skipped = None
        self._changed()
        self.say("It counts again.")

    # --- notes ---

    def _video(self):
        """The video being watched, or the one to pick up."""
        vid = self._watching()
        if vid:
            return self._call("GET", "/items/%d" % vid), True
        r = self._call("GET", "/today").get("resume")
        return (self._call("GET", "/items/%d" % r["id"]), False) if r else (None, False)

    @_guard
    def note(self, m, front=False):
        text = next(x for x in m.groups() if x).strip()
        if front and not (self._watching() and self._front()):
            return False
        if self._watching():
            self._send({"note": text})  # the page stamps it with the player's current time
            self.say("Noted.")
            return
        item, _ = self._video()
        if not item:
            self.say("There's no video to put it on yet.")
            return
        self._call("POST", "/items/%d/notes" % item["id"], {"atS": item["progress"]["positionS"], "body": text})
        self.say("Noted on %s." % item["title"])

    @staticmethod
    def _clock(s):
        s = int(s)
        return "%d:%02d" % divmod(s, 60)

    @_guard
    def video_notes(self):
        item, _ = self._video()
        notes = item and item["notes"]
        if not notes:
            self.say("No notes on %s yet." % item["title"] if item else "There's no video to read notes from.")
            return
        self.j.show({"title": "Notes: " + item["title"],
                     "text": "\n".join("- **%s** %s" % (self._clock(n["atS"]), n["body"]) for n in notes)})
        self.say("%d note%s on it. %s" % (len(notes), "" if len(notes) == 1 else "s",
                                        notes[0]["body"] if len(notes) == 1 else "They're on your screen."))

    @_guard
    def search_notes(self, m):
        q = next(x for x in m.groups() if x).strip()
        hits = [n for n in self._call("GET", "/notes?q=" + urllib.parse.quote(q))]
        if not hits:
            self.say("Nothing about %s in your study notes." % q)
            return
        self.j.show({"title": "Study notes: " + q,
                     "text": "\n".join("- %s *(%s, %s)*" % (n["body"], n["itemTitle"], self._clock(n["atS"]))
                                       for n in hits[:20])})
        self.say("%d note%s about %s, on your screen." % (len(hits), "" if len(hits) == 1 else "s", q))

    @_guard
    def delete_note(self):
        notes = self._call("GET", "/notes")
        if not notes:
            self.say("You don't have any study notes.")
            return
        last = max(notes, key=lambda n: (n["createdAt"], n["id"]))
        if not self.j.ask('Delete your note "%s"?' % last["body"][:80]):
            self.say("Okay, kept it.")
            return
        self._call("DELETE", "/notes/%d" % last["id"])
        self._changed()
        self.say("Deleted.")

    # --- flashcards ---

    @_guard
    def cards_due(self):
        s = self._call("GET", "/cards/summary")
        if not s["total"]:
            self.say("You don't have any flashcards yet.")
            return
        line = "%d card%s due" % (s["due"], "" if s["due"] == 1 else "s") if s["due"] else "No cards are due"
        if s["due"] and s["newToday"]:
            line += ", %d of them new" % s["newToday"]
        if s["piles"].get("pending"):
            line += ". %d drafted cards are waiting for your review" % s["piles"]["pending"]
        self.say(line + ".")

    @_guard
    def quiz(self, m):
        name = next((x for x in m.groups() if x), None)
        group = None
        if name:
            group = self._group(name)
            if not group:
                self.say("I don't have a group called %s." % name)
                return
        cards = self._call("GET", "/cards/queue" + ("?group=%d" % group["id"] if group else ""))
        if not cards:
            self.say("No cards are due%s. Nice work." % (" in " + group["name"] if group else ""))
            return
        n = min(len(cards), MAX_QUIZ)
        self.say("%d card%s. Answer, or say show me. Say stop to finish." % (n, "" if n == 1 else "s"))
        done = 0
        for card in cards[:n]:
            reply = self.j._answer(card["front"])  # no answer in time: just reveal it
            if reply and STOP.match(reply.strip()):
                break
            rating = self.j.choose("%s. Again, hard, good or easy?" % card["back"], RATINGS)
            if not rating:
                break
            self._call("POST", "/cards/%d/review" % card["id"], {"rating": RATINGS.index(rating) + 1})
            done += 1
        self._changed()
        self.say("Reviewed %d card%s." % (done, "" if done == 1 else "s") if done else "Okay, stopped.")

    @_guard
    def add_card(self, m):
        front, back = m.group(1).strip(), (m.group(2) or "").strip()
        if not back:
            back = (self.j._answer("And the answer?") or "").strip()
            if not back:
                self.say("Okay, no card.")
                return
        body = {"front": front[0].upper() + front[1:], "back": back[0].upper() + back[1:]}
        vid = self._watching()
        if vid:
            body.update(itemId=vid, atS=self._call("GET", "/items/%d" % vid)["progress"]["positionS"])
        self._call("POST", "/cards", body)
        self._changed()
        self.say("Card added%s." % (" to this video" if vid else ""))

    # --- carrying on ---

    @_guard
    def suggest(self):
        tips = self._call("GET", "/suggestions")
        if tips:
            t = tips[0]
            self.say(t["text"])
            if t.get("href"):
                self.j.show({"title": "Study Buddy", "text": t["text"],
                             "choices": [{"label": t.get("action") or "Open", "do": "continue studying"
                                          if t["href"].startswith("#/watch") else "open my flashcards"
                                          if t["href"] == "#/review" else "open study buddy today"}]})
            return
        r = self._call("GET", "/today").get("resume")
        if r:
            self.say("Pick up %s, in %s. Say continue studying." % (r["title"], r["groupName"]))
        else:
            self.say("Nothing's waiting. Add a playlist to a group in the library.")

    @_guard
    def resume(self):
        r = self._call("GET", "/today").get("resume")
        if not r:
            self.say("There's nothing to pick up. Add a playlist in the library first.")
            return
        self.last_item = r
        self.go("#/watch/%d" % r["id"], "%s, %s." % (r["groupName"], r["title"]))

    @_guard
    def resume_group(self, m):
        name = m.group(1)
        if re.match(r"^(?:study(?:ing)?|learning|lectures?|videos?)$", name, re.I):
            return self.resume()
        group = self._group(name)
        if not group:
            return False  # "continue the download", "watch a movie": not a group
        detail = self._call("GET", "/groups/%d" % group["id"])
        if detail["upNext"]:
            v = detail["upNext"][0]
            self.last_item = v
            self.go("#/watch/%d" % v["id"], "%s: %s." % (group["name"], v["title"]))
            return
        for s in detail["sources"]:
            if s.get("next"):
                self.last_item = s["next"]
                self.go("#/source/%d" % s["id"], "%s: next is %s." % (group["name"], s["next"]["title"]))
                return
        self.say("You've finished everything in %s." % group["name"])

    # --- progress ---

    @_guard
    def streak(self):
        d = self._call("GET", "/today")
        if not d["streak"]:
            self.say("No streak right now. Study anything today to start one."
                     + (" Your longest was %d days." % d["longestStreak"] if d["longestStreak"] else ""))
            return
        self.say("%d day%s in a row%s." % (d["streak"], "" if d["streak"] == 1 else "s",
                                           ", your longest" if d["streak"] >= d["longestStreak"] else
                                           ". Your longest is %d" % d["longestStreak"]))

    @_guard
    def today(self):
        d = self._call("GET", "/today")
        secs = d["studiedTodayS"]
        parts = ["%s in Study Buddy today" % spoken(secs).capitalize() if secs >= 60 else "No study in Study Buddy today yet"]
        for g in d["groups"]:
            if g["goalMin"]:
                parts.append("%d of %d minutes on %s" % (g["todayS"] // 60, g["goalMin"], g["name"]))
        if d["streak"]:
            parts.append("a %d-day streak" % d["streak"])
        self.say(parts[0] + (". " + cap(and_list(parts[1:])) if parts[1:] else "") + ".")

    @_guard
    def how_far(self, m):
        name = next(x for x in m.groups() if x)
        groups = self._quiet_groups()
        group = self._group(name, groups)
        if group:
            what, total, done, dur, watched = group["name"], group["total"], group["done"], group["durationS"], \
                group["watchedS"]
        else:
            src = best_match(re.sub(r"\bplaylists?$", "", name).strip(), [s for g in groups for s in g["sources"]],
                             lambda s: s["title"])
            if not src:
                return False  # "how far is the moon": not a group
            what, total, done, dur, watched = src["title"], src["total"], src["done"], src["durationS"], src["watchedS"]
        if not total:
            self.say("%s is empty." % what)
            return
        pct = round(100 * watched / dur) if dur else round(100 * done / total)
        line = "%s: %d of %d done, %d percent." % (what, done, total, pct)
        if dur and dur > watched:
            line += " About %s left." % spoken(dur - watched)
        self.say(line)

    @_guard
    def overview(self):
        groups = [g for g in self._groups() if g["total"]]
        if not groups:
            self.say("There's nothing in Study Buddy yet.")
            return
        rows = ["%s: %d%%" % (g["name"], round(100 * (g["watchedS"] / g["durationS"] if g["durationS"] else
                                                       g["done"] / g["total"]))) for g in groups]
        d = self._call("GET", "/today")
        self.j.show({"title": "Study progress", "text": "```bars\n%s\n```\n\nToday: %s · streak: %d day%s" % (
            "\n".join(rows), spoken(d["studiedTodayS"]), d["streak"], "" if d["streak"] == 1 else "s")})
        top = max(groups, key=lambda g: g["done"] / g["total"])
        self.say("%d groups. Furthest along is %s. It's on your screen." % (len(groups), top["name"]))

    # --- problems and articles ---

    @_guard
    def next_problem(self, m):
        name = next((x for x in m.groups() if x), None)
        groups = [g for g in self._groups() if any(s["kind"] == "problem_set" for s in g["sources"])]
        if name:
            groups = [g for g in [self._group(name, groups)] if g]
        for g in groups:
            for s in self._call("GET", "/groups/%d" % g["id"])["sources"]:
                p = s["kind"] == "problem_set" and s.get("next")
                if p:
                    self.last_item = p
                    level = (p.get("meta") or {}).get("difficulty") or ""
                    self.say("Next in %s: %s%s, from %s." % (g["name"], p["title"], ", " + level.lower() if level
                                                              else "", s["title"]))
                    self.j.show({"title": p["title"], "text": "%s · %s%s" % (
                        g["name"], s["title"], " · " + level if level else "") +
                        ("\n\n[Open the problem](%s)" % p["url"] if p.get("url") else ""),
                        "choices": [{"label": "Open it", "do": "open the problem"},
                                    {"label": "Mark it solved", "do": "mark it solved"}]})
                    return
        self.say("No problems left%s." % (" in " + groups[0]["name"] if name and groups else ""))

    def open_item(self):
        p = self.last_item
        if not p or not p.get("url"):
            return False
        self.j.executor.run("xdg-open %s" % shlex.quote(p["url"]), reply="Opening %s." % p["title"], window=True)

    def _items(self):
        """Every problem and article, with their group: they're listed on the group pages."""
        out = []
        for g in self._groups():
            if any(s["kind"] in ("problem_set", "articles") for s in g["sources"]):
                for s in self._call("GET", "/groups/%d" % g["id"])["sources"]:
                    out += [dict(i, group=g["name"]) for i in s.get("items") or []]
        return out

    @_guard
    def mark(self, m):
        groups = [x for x in m.groups() if x]
        name, how = (groups[0], groups[1]) if m.group(1) else (groups[1], groups[0])
        how = how.lower()
        kind = {"solved": "problem", "read": "article", "finished reading": "article", "watched": "video"}[how]
        if re.match(r"^(?:it|this|that|this one|that one|the (?:problem|article|video))$", name, re.I):
            item = self.last_item
            if not item or item.get("kind", "video") != kind:
                return False  # "mark it as read" in your mail
        elif not self._quiet_groups():
            return False
        else:
            if kind == "video":
                hits = [h for h in self._call("GET", "/search?q=" + urllib.parse.quote(name)) if h["kind"] == "Video"]
                item = hits and {"id": int(hits[0]["href"].split("/")[-1]), "title": hits[0]["title"], "kind": "video"}
            else:
                item = best_match(name, [i for i in self._items() if i["kind"] == kind], lambda i: i["title"])
            if not item:
                return False  # not something in Study Buddy: maybe a to-do
        self._call("POST", "/items/%d/status" % item["id"], {"done": True})
        self.last_item = item
        self._changed()
        self.say("Marked %s %s.%s" % (item["title"], "solved" if how == "solved" else "done",
                                      " A solve-again card comes up in a few days." if how == "solved" else ""))

    # --- groups ---

    def _need_group(self, name):
        group = self._group(name)
        if not group:
            self.say("I don't have a study group called %s." % name)
        return group

    @_guard
    def new_group(self, m):
        name = next(x for x in m.groups() if x).strip().rstrip(".")
        name = name.title() if name.islower() else name
        groups = self._groups()
        if any(squash(g["name"]) == squash(name) for g in groups):
            self.say("You already have a %s group." % name)
            return
        used = [g["color"] for g in groups]
        color = next((c for c in ("blue", "orange", "aqua", "yellow", "magenta", "green", "violet", "red")
                      if c not in used), "blue")
        self._call("POST", "/groups", {"name": name, "color": color})
        self._changed()
        self.say("Made a %s group. Add playlists to it with: add this playlist to %s." % (name, name))

    @_guard
    def rename_group(self, m):
        group = self._need_group(m.group(1))
        if group:
            new = m.group(2).strip().rstrip(".")
            new = new.title() if new.islower() else new
            self._call("PATCH", "/groups/%d" % group["id"], {"name": new})
            self._changed()
            self.say("%s is now %s." % (group["name"], new))

    @_guard
    def delete_group(self, m):
        group = self._group(m.group(1))
        if not group:
            return False  # "remove the tab group"
        if not self.j.ask("Delete %s and its %d playlist%s, with all their progress and notes? This can't be undone."
                          % (group["name"], len(group["sources"]), "" if len(group["sources"]) == 1 else "s")):
            self.say("Okay, kept it.")
            return
        self._call("DELETE", "/groups/%d" % group["id"])
        self._changed()
        self.say("Deleted %s." % group["name"])

    @_guard
    def color_group(self, m):
        group = self._group(m.group(1))
        if not group:
            return False  # "make the text red": not a group
        self._call("PATCH", "/groups/%d" % group["id"], {"color": COLORS[m.group(2).lower()]})
        self._changed()
        self.say("%s is %s now." % (group["name"], m.group(2).lower()))

    @_guard
    def group_goal(self, m):
        group = self._group(m.group(1))
        if not group:
            return False  # "set my study goal to 2 hours": the day tracker's
        mins = round(mins_said(m.group(2), m.group(3))) if m.lastindex and m.lastindex >= 3 else 0
        if mins > 600:
            self.say("A daily goal can be up to 10 hours.")
            return
        self._call("PATCH", "/groups/%d" % group["id"], {"goalMin": mins})
        self._changed()
        self.say("%s goal: %s a day." % (group["name"], spoken(mins * 60)) if mins else
                 "No daily goal for %s now." % group["name"])

    @_guard
    def on_today(self, m):
        name, on = (m.group(1), True) if m.group(1) else (m.group(2), False)
        group = self._group(name)
        if not group:
            return False
        self._call("PATCH", "/groups/%d" % group["id"], {"onToday": on})
        self._changed()
        self.say("%s is %s Today." % (group["name"], "on" if on else "off"))

    @_guard
    def move_group(self, m):
        groups = self._groups()
        group = self._group(m.group(1), groups)
        if not group:
            return False
        ids = [g["id"] for g in groups if g["id"] != group["id"]]
        ids = [group["id"]] + ids if m.group(2).lower() in ("top", "first") else ids + [group["id"]]
        self._call("POST", "/groups/order", {"ids": ids})
        self._changed()
        self.say("Moved %s to the %s." % (group["name"], "top" if ids[0] == group["id"] else "bottom"))

    # --- the library ---

    def _link(self):
        """The link meant by "this": the browser tab you're on, or a link you copied while somewhere else."""
        win = desktop.active_window()
        copied = clipboard().strip()
        if not (win and any(b in win.wm_class for b in BROWSERS)) and re.match(r"^https?://\S+$", copied):
            return copied
        return self.j.skills.web._page_url() if hasattr(self.j, "skills") else None

    @_guard
    def add_link(self, m):
        kind = (m.group(1) or m.group(3) or "").lower()
        name = m.group(2) or ""
        groups = self._quiet_groups()
        if not groups and not (kind in ("playlist", "sheet", "problem sheet") or "study" in m.group(0).lower()):
            return False  # down, and "add this to my shopping list" may not be Study Buddy's
        groups = groups or self._groups()  # down: says so
        group = self._group(name, groups) if name else None
        if name and not group:
            if re.search(r"\b(?:to ?-?do|task|note|clipboard|bookmark|calendar|playlist)s?\b", name, re.I):
                return False  # "add this to my todo list": not a group
            self.say("I don't have a study group called %s." % name)
            return
        if not group:
            answer = self.j._answer("Which group?")
            group = answer and self._group(answer, groups)
            if not group:
                self.say("Okay, I didn't add it.")
                return
        url = self._link()
        if not url:
            self.say("I couldn't get the link. Copy it, then say that again.")
            return
        if re.search(r"[?&]list=[\w-]+", url):
            self.say("Importing the playlist into %s." % group["name"])
            r = self._call("POST", "/sources", {"groupId": group["id"], "url": url}, timeout=60)
            self.say("Added %s, %d videos." % (r["title"], r["count"]))
        elif "docs.google.com/spreadsheets" in url:
            self.say("Reading the sheet.")
            r = self._call("POST", "/sheets", {"groupId": group["id"], "url": url}, timeout=60)
            self.say("Added %d problems in %d patterns to %s." % (r["added"], r["sections"], group["name"]))
        elif re.search(r"youtube\.com/watch|youtu\.be/", url) or kind == "playlist":
            self.say("That's a single video. Study Buddy takes playlists: open the playlist and say that again.")
            return
        else:
            r = self._call("POST", "/articles", {"groupId": group["id"], "url": url}, timeout=30)
            self.say("Added %s to the %s reading list, about %d minutes." % (r["title"], group["name"], r["minutes"]))
        self._changed()

    @_guard
    def sync(self, m):
        group = self._group(next(x for x in m.groups() if x))
        if not group:
            return False
        self.say("Checking %s." % group["name"])
        added = 0
        for s in group["sources"]:
            if s["kind"] != "articles":
                added += self._call("POST", "/sources/%d/sync" % s["id"], {}, timeout=60).get("added", 0)
        self._changed()
        self.say("%d new in %s." % (added, group["name"]) if added else "Nothing new in %s." % group["name"])

    @_guard
    def backup(self):
        f = self._call("POST", "/backup", {})["file"]
        self.say("Backed up to %s." % os.path.basename(f))

    # --- pages ---

    @_guard
    def page(self, m):
        which = squash(next(x for x in m.groups() if x))
        route = {"library": "#/library", "progress": "#/progress", "flashcards": "#/cards", "cards": "#/cards",
                 "notes": "#/notes", "today": "#/"}[which]
        self.go(route, "Here's %s." % ("your flashcards" if "cards" in which else "Today" if which == "today"
                                       else "your " + which))

    @_guard
    def open_group(self, m):
        group = self._group(m.group(1))
        if not group:
            return False  # "open the tab group"
        self.go("#/group/%d" % group["id"], group["name"] + ".")
