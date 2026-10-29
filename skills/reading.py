"""Long text read aloud: only the start is spoken (locally, instantly), all of it is on a card, and then "read
the rest" goes on, "read all of it" reads the remainder, "summarise it" has Claude say it in a few sentences.
With speech.long_text: summary, long text is summarised straight away instead. Short text is just said."""
import re
import time

from claude.bridge import ClaudeError
from skills.base import Skill
from ui.card import plain

FRESH = 10 * 60  # "read the rest" still means this text for this long
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


class Reading(Skill):
    def __init__(self, jarvis):
        super().__init__(jarvis)
        cfg = jarvis.settings.get("speech") or {}
        self.words = int(cfg.get("read_words", 60))  # spoken in one go; longer text is split
        self.mode = cfg.get("long_text", "start")  # start | summary
        self.text, self.parts, self.at = "", [], 0.0

    def patterns(self):
        return [
            (r"(?:read|go on with|continue with|carry on with) (?:me )?the rest(?: of it)?(?: to me)?|keep reading|"
             r"continue reading|carry on(?: reading)?|go on|continue|next part|read (?:me )?more|"
             r"what else does it say", lambda m: self.rest()),
            (r"read (?:me )?(?:all of it|it all|the whole (?:thing|text|card)|everything)(?: out)?(?: to me)?",
             lambda m: self.rest(everything=True)),
            (r"summari[sz]e (?:it|that|this|the card|the text)(?: for me)?|(?:give me |what'?s )?(?:a |the )?"
             r"(?:summary|gist)(?: of (?:it|that|this))?|sum (?:it|that) up|tl ?dr", lambda m: self.summarise()),
        ]

    def long(self, text, title=None):
        """Say `text`, or for long text the start of it (and show all of it on a card when `title` is given)."""
        text = text.strip()
        if len(text.split()) <= self.words:
            self.say(text)
            return
        if title:
            self.j.show({"title": title, "text": text[:6000]})
        self.text, self.parts, self.at = text, _chunks(text, self.words), time.time()
        if self.mode == "summary" and self.j.mode != "sys":
            self.summarise()
            return
        first = self.parts.pop(0)
        self.say("%s ... That's the start of %d words. Say read the rest, or summarise it." % (
            first.rstrip(), len(text.split())))

    def _fresh(self):
        return self.text and time.time() - self.at < FRESH

    def rest(self, everything=False):
        if not self._fresh():
            return False  # nothing being read: "continue" is the music, "read all of it" the card
        if not self.parts:
            self.say("That's the end of it.")
            return
        self.at = time.time()
        if everything:
            part, self.parts = " ".join(self.parts), []
        else:
            part = self.parts.pop(0)
        self.say(part + ("" if not self.parts else " ... Say read the rest for more."))

    def summarise(self):
        text = self.text if self._fresh() else self._card_text()
        if not text:
            return False  # nothing to summarise: "summarise my clipboard" and the like go on
        if self.j.mode == "sys":
            self.say("Summaries need Claude, which is off in system mode. Say read the rest instead.")
            return
        self.say(["Let me sum it up.", "One sec, summing it up."])
        self.j.state("claude")
        try:
            summary = self.j.claude.rewrite("Summarise this in two or three short, plain sentences to be read "
                                            "aloud: the main point first, no lists, no markdown.", text[:20000])
        except ClaudeError as e:
            self.say("Sorry, I couldn't summarise it: %s." % str(e).rstrip("."))
            return
        finally:
            self.j.state("thinking")
        self.j.claude_says(summary)

    def _card_text(self):
        ui = self.j.ui
        card = ui.current_card() if ui and ui.card_visible() else None
        return plain(card) if card else ""


def _chunks(text, words):
    """Whole sentences, about `words` words a piece (a very long sentence is its own piece)."""
    parts, now = [], []
    sentences = []
    for s in (s.strip() for s in _SENTENCE.split(text) if s.strip()):
        w = s.split()
        sentences += [" ".join(w[i:i + words]) for i in range(0, len(w), words)]  # a run-on in pieces
    for sentence in sentences:
        if now and len(" ".join(now + [sentence]).split()) > words:
            parts.append(" ".join(now))
            now = []
        now.append(sentence if re.search(r"[.!?:]$", sentence) else sentence + ".")
    if now:
        parts.append(" ".join(now))
    return parts
