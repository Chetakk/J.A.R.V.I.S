""""Did you learn anything new?": what JARVIS taught itself (core/teaching.py) and learned from you, since you last
asked, and what it took back as wrong. It learns without asking, so this is how you find out."""
import time

from core import learned
from core.intent import LEAVE
from skills.base import Skill

NEW = r"(?:phrases?|sentences?|wordings?|words?|things?|commands?|ways of saying things)"
LEARN = r"(?:learn(?:ed|t)?|picked up|pick up|taught yourself|teach yourself)"
WHEN = r"(?: (today|this week|lately|recently|yet|so far))?"


class Learning(Skill):
    group = "what I've learned"
    does = "Tells you the new phrases and sentences JARVIS has learned; it learns them without asking"
    phrases = ("did you learn anything new", "what have you learned today", "what new phrases do you know",
               "save that command")
    gives = "speech and a card"
    permission = "auto"
    asks = ""
    checks = "not checked: it only reports"

    def patterns(self):
        return [
            (r"(?:did|have) you (?:been )?%s(?: (?:any|anything|something|some))?(?: new)?(?: %s)?%s" % (LEARN, NEW, WHEN),
             self.whats_new),
            (r"what(?: new)?(?: %s)? (?:have|did) you %s%s" % (NEW, LEARN, WHEN), self.whats_new),
            (r"what new %s do you know%s" % (NEW, WHEN), self.whats_new),
            (r"(?:any|show me|tell me|list|what are)(?: your| the| any)? new %s(?: (?:that )?you(?: have|'ve)? "
             r"(?:%s|know))?%s" % (NEW, LEARN, WHEN), self.whats_new),
            (r"(?:tell me|show me|list) what you(?: have|'ve)? %s%s" % (LEARN, WHEN), self.whats_new),
            (r"(?:save|remember|keep|learn) (?:that|this|the last|my last) (?:command|one|wording)(?: for next time)?",
             self.save_last),
            # A bare "remember that" after a command is about the command: as a note it saved just "that"
            (r"remember (?:that|this|it)(?: for next time)?", self.save_last),
        ]

    def save_last(self, m):
        """"Save that command": what Claude just did for you is kept (_save_claudes); what you just confirmed already
        is (jarvis.py learns it on your yes), so this says so, instead of "save that" saving whatever card is on screen."""
        rows = learned.history()
        last = rows[-1] if rows else None
        recent = last and last.get("time", "") >= time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 600))
        did = getattr(self.j, "claude_did", None)
        if did and time.time() - did[0] < 600 and (not recent or time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(did[0])) >= last["time"]):
            return self._save_claudes(did[1], did[2])
        if not recent or last.get("unlearned") or last["means"] == LEAVE:
            self.say("There's nothing new to save. When you say yes to one of my guesses, I keep it on my own.")
            return True
        self.say(["Already saved. Next time, %s means %s." % (last["said"], last["means"]),
                  "Already got it: %s means %s." % (last["said"], last["means"]),
                  "I already have that one: %s means %s." % (last["said"], last["means"])])
        return True

    def _save_claudes(self, said, phrase):
        """Claude did what you asked with one of JARVIS's own phrases: keep your wording for it, so next time needs no
        Claude. You asked for it, so it's kept even if JARVIS had chosen not to ask (too long, has a number, declined)."""
        said = said.strip(" .!?")
        if said.lower() == phrase.lower().strip(" .!?") or learned.load()["wordings"].get(said.lower()) == phrase:
            self.say(["Already saved. Next time, %s means %s." % (said, phrase),
                      "Already got it: %s means %s." % (said, phrase),
                      "I already have that one: %s means %s." % (said, phrase)])
            return True
        learned.remember(said, phrase, why="you asked me to remember it")
        if self.j.intents:
            self.j.intents.learn(said, phrase)
        self.say(["Saved. Next time, %s means %s." % (said, phrase),
                  "Taught. From now on, %s means %s." % (said, phrase),
                  "Lesson learned: %s means %s." % (said, phrase),
                  "Got it. From now on, %s means %s." % (said, phrase),
                  "Learned: %s means %s from now on." % (said, phrase)])
        return True

    def whats_new(self, m):
        when = next((g for g in m.groups() if g), "").lower()
        rows = learned.history()
        if not rows:
            self.say("I haven't learned anything new yet.")
            return True
        if when == "today":
            since, nothing = time.strftime("%Y-%m-%d"), "Nothing new today."
        elif when == "this week":
            since, nothing = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400)), "Nothing new this week."
        else:
            since, nothing = self.j.memory.pref("learned_asked") or "", "Nothing new since you last asked."
        fresh = [r for r in rows if r.get("time", "") > since or (when == "today" and r.get("time", "") >= since)]
        self.j.memory.set_pref("learned_asked", time.strftime("%Y-%m-%d %H:%M"))
        back = [r for r in fresh if r.get("unlearned")]
        gone = {(r["said"], r["means"]) for r in back}
        fresh = [r for r in fresh if not r.get("unlearned") and (r["said"], r["means"]) not in gone]
        if not fresh and back:
            self.say("Nothing new, but I took back %d %s I'd got wrong." % (len(back), "way" if len(back) == 1 else "ways"))
            self.j.show({"title": "What I've learned", "text": self._took_back(back) + self._score()})
            return True
        if not fresh:
            self.say("%s The last thing I learned was on %s." % (nothing, rows[-1]["time"].split()[0]))
            return True
        by_phrase = {}
        for r in fresh:
            by_phrase.setdefault(r["means"], []).append(r["said"])
        first = next((r for r in reversed(fresh) if r["means"] != LEAVE), None)
        count = "%d new %s" % (len(fresh), "way" if len(fresh) == 1 else "ways")
        things = "%d %s" % (len(by_phrase), "thing" if len(by_phrase) == 1 else "things")
        example = (" For example, %s now means %s." % (first["said"], first["means"]) if first else
                   " They're all things I now leave to Claude, like %s." % fresh[-1]["said"])
        self.say("I learned %s of saying %s.%s%s" % (count, things, example,
                                                     " I also took back %d I'd got wrong." % len(back) if back else ""))
        lines = []
        for phrase, says in by_phrase.items():
            lines.append("**%s**" % ("Left for Claude" if phrase == LEAVE else phrase))
            lines += ["- %s" % s for s in says]
        self.j.show({"title": "What I've learned",
                     "text": "\n".join(lines) + ("\n\n" + self._took_back(back) if back else "") + self._score()})
        return True

    @staticmethod
    def _took_back(rows):
        return "**Taken back**\n" + "\n".join("- %s (not %s)" % (r["said"], r["means"]) for r in rows)

    @staticmethod
    def _score():
        """The last teaching pass's score on sentences it was never taught (core/heldout.py)."""
        from core import teaching  # (here: core.teaching reads the skills registry)
        scores = teaching._state()["scores"]
        if not scores:
            return ""
        after = scores[-1]["after"]
        return "\n\nOn %d sentences I was never taught: %d right, %d wrong." % (after["out_of"], after["right"],
                                                                                 after["wrong"])
