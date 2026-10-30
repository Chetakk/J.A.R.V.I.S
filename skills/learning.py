""""Did you learn anything new?": what JARVIS taught itself (core/teaching.py) and learned from you, since you last
asked. It learns without asking, so this is how you find out."""
import time

from core import learned
from skills.base import Skill

NEW = r"(?:phrases?|sentences?|wordings?|words?|things?|commands?|ways of saying things)"
LEARN = r"(?:learn(?:ed|t)?|picked up|pick up|taught yourself|teach yourself)"
WHEN = r"(?: (today|this week|lately|recently|yet|so far))?"


class Learning(Skill):
    group = "what I've learned"
    does = "Tells you the new phrases and sentences JARVIS has learned; it learns them without asking"
    phrases = ("did you learn anything new", "what have you learned today", "what new phrases do you know")
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
        ]

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
        if not fresh:
            self.say("%s The last thing I learned was on %s." % (nothing, rows[-1]["time"].split()[0]))
            return True
        by_phrase = {}
        for r in fresh:
            by_phrase.setdefault(r["means"], []).append(r["said"])
        first = fresh[-1]
        count = "%d new %s" % (len(fresh), "way" if len(fresh) == 1 else "ways")
        things = "%d %s" % (len(by_phrase), "thing" if len(by_phrase) == 1 else "things")
        self.say("I learned %s of saying %s. For example, %s now means %s." % (count, things, first["said"], first["means"]))
        lines = []
        for phrase, says in by_phrase.items():
            lines.append("**%s**" % phrase)
            lines += ["- %s" % s for s in says]
        self.j.show({"title": "What I've learned", "text": "\n".join(lines)})
        return True
