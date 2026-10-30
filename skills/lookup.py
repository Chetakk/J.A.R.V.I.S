"""Quick spoken answers: "wikipedia alan turing", "search the web for X and tell me", and, when nothing else
knows it, a plain "who is X" / "what is X" answered from Wikipedia before Claude is asked.

Wikipedia is one HTTP call (no key, no Claude, works in system mode). Anything current ("latest", "price of",
"who is the president of ...") goes to a web search instead: one Claude call with WebSearch, no pages opened.
"google X" still opens the results in your browser.

After an answer it asks one question back, as a friend would: "Are you feeling okay?" after an illness, "Thinking of
going there?" after a place, now and then "Want to hear a bit more?" (a "yes" reads on). Your answer to the first two
gets a fitting line (config/chat.yaml wiki_health / wiki_place), or goes to Claude, who sees the question."""
import json
import random
import re
import time
import urllib.parse
import urllib.request

from core import persona
from skills.base import Skill
from system.webagent import UNTRUSTED_RULE

API = "https://en.wikipedia.org/w/api.php"
AGENT = "JARVIS-voice-assistant/1.0 (personal desktop assistant)"
# Questions Wikipedia answers badly: anything that changes.
CURRENT = re.compile(r"\b(?:latest|current(?:ly)?|today|tonight|tomorrow|yesterday|this (?:week|month|year)|now|"
                     r"news|price|cost|weather|score|result|winner|won|president|prime minister|pm|ceo|chief "
                     r"minister|governor|captain|champion|ranking|release date|open (?:now|today)|near me|20\d\d)\b",
                     re.IGNORECASE)
# Not a topic: about you, the computer, or what's on screen.
PERSONAL = re.compile(r"\b(?:my|mine|me|i|you|your|yours|this|that|it|here|there|we|our)\b|\d", re.IGNORECASE)
QUESTION = re.compile(r"^(?:(?:who|what)(?: is|'s| was| are| were) (?:an? |the )?|tell me (?:about|something "
                      r"about) (?:an? |the )?|what does (?:an? |the )?(.+?) mean$|define |meaning of )(.+)$",
                      re.IGNORECASE)
# What the article's first sentence says it is ("Malaria is a mosquito-borne infectious disease ...").
_IS = r"\b(?:is|are|was) (?:an? |the )?(?:[\w'-]+ ){0,5}"
HEALTH = re.compile(_IS + r"(?:diseases?|infections?|disorders?|syndromes?|illness(?:es)?|conditions?|virus(?:es)?|"
                    r"cancers?|tumou?rs?|allerg(?:y|ies)|deficienc(?:y|ies))\b", re.IGNORECASE)
PLACE = re.compile(_IS + r"(?:city|capital|country|nation|island|islands|archipelago|town|village|state|province|"
                   r"region|district|mountain|peak|lake|beach|resort|national park)\b", re.IGNORECASE)
MORE_CHANCE = 0.5  # "Want to hear a bit more?" after other articles: now and then, not every time
LOOKUP_SYSTEM = ("You answer quick questions for a voice assistant by searching the web. %s Reply with only JSON: "
                 "{\"say\": \"<the answer in one or two short spoken sentences, no markdown or URLs; when the "
                 "question hints at the user's own situation, end with one short friendly question, e.g. health or "
                 "symptoms -> are they feeling okay, flights, hotels or prices -> are they planning a trip, how "
                 "something works -> is it for something they're working on; none for plain facts>\", \"card\": "
                 "\"<markdown: the answer in a few lines, then a 'Sources' list of [title](url)>\"}." % UNTRUSTED_RULE)


def wikipedia(query, timeout=4):
    """The best-matching article's {"title", "text", "url"}, or None (nothing found, or a disambiguation page)."""
    params = {"action": "query", "format": "json", "redirects": 1, "generator": "search", "gsrsearch": query,
              "gsrlimit": 1, "prop": "extracts|info|pageprops", "exintro": 1, "explaintext": 1, "exsentences": 5,
              "inprop": "url", "ppprop": "disambiguation"}
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        pages = list((json.load(r).get("query") or {}).get("pages", {}).values())
    if not pages or "disambiguation" in (pages[0].get("pageprops") or {}) or not pages[0].get("extract"):
        return None
    p = pages[0]
    return {"title": p["title"], "text": p["extract"].strip(), "url": p.get("fullurl") or ""}


def about(title, topic):
    """True if the article is about what was asked: most of the asked words are in its title, or the reverse."""
    t = set(re.findall(r"\w+", re.sub(r"\(.*?\)", "", title).lower()))
    q = set(re.findall(r"\w+", topic.lower())) - {"the", "a", "an", "of", "in", "on"}
    return bool(t and q) and (len(t & q) * 2 >= len(q) or t <= q)


def spoken(text, sentences=2):
    """The first sentences, without the brackets (pronunciations, dates) that sound wrong read out."""
    text = re.sub(r"\s*\([^()]*\)", "", re.sub(r"\s*\([^()]*\([^()]*\)[^()]*\)", "", text))
    parts = re.split(r"(?<=[a-z0-9)\]][.!?])\s+(?=[A-Z])", text.replace("\n", " "))
    if len(parts[0]) > 180:  # one long sentence is enough to hear
        sentences = 1
    return " ".join(parts[:sentences])[:400].strip()


class Lookup(Skill):
    group = "web"
    does = ("Quick spoken answers from Wikipedia, or a web search for anything current; a plain \"who is X\" "
            "nothing else knows is tried on Wikipedia before Claude")
    phrases = ("wikipedia <topic>", "what does wikipedia say about <topic>", "search the web for <topic> and tell me",
               "what does the web say about <topic>", "look up <topic> online and tell me")
    gives = "a spoken answer and a card with the source link"
    checks = "the Wikipedia article's title must match what you asked, or it hands the question on"

    def patterns(self):
        tell = r"(?:,? and (?:tell|read)(?: it)?(?: to)? me(?: what (?:it|they) says?)?|,? (?:and )?tell me)"
        return [
            # ("search wikipedia for X" still opens the site)
            (r"(?:(?:what does|what'?s|check|ask) )?wikipedia(?: say| says)?(?: (?:about|on))? (.+)|"
             r"(?:find out about|tell me about) (.+?) (?:from|on|in) wikipedia" + tell + "?",
             lambda m: self.wiki(next(g for g in m.groups() if g))),
            (r"(?:search (?:the )?(?:web|internet|online)(?: for)?|search(?: for)?|google|look up) (.+?)" + tell + "|"
             r"(?:what does|what'?s) the (?:web|internet) say(?:ing)? (?:about|on) (.+)|"
             r"(?:look up|find out|search(?: for)?) (.+?) online(?:" + tell + ")?|quick (?:search|look ?up)(?: for| on)? (.+)",
             lambda m: self.web(next(g for g in m.groups() if g))),
        ]

    def wiki(self, topic, quiet=False):
        """Says the article's gist and shows it. quiet: nothing said when it isn't found (Claude gets asked)."""
        topic = topic.strip(" ?.!")
        try:
            page = wikipedia(topic)
        except (OSError, ValueError) as e:
            if quiet:
                return False
            self.say("I couldn't reach Wikipedia: %s." % getattr(e, "reason", e))
            return
        if not page or (quiet and not about(page["title"], topic)):
            if quiet:
                return False
            self.say("Wikipedia doesn't have a page on %s." % topic)
            return
        self.j.show({"title": page["title"], "text": "%s\n\n[Wikipedia: %s](%s)" % (
            page["text"], page["title"], page["url"])})
        self.say(spoken(page["text"]) or "It's on the card.")
        self.follow_up(page)

    def follow_up(self, page):
        """One question back (see the top of this file)."""
        first, chat = spoken(page["text"], 1), self.j.chat_replies
        topic = "wiki_health" if HEALTH.search(first) else "wiki_place" if PLACE.search(first) else None
        line = chat.ask(topic, persona.address(self.j.settings), self.j.name) if topic and chat else None
        if line:
            self.say(line)
            return
        said, full = spoken(page["text"]), spoken(page["text"], 5)
        more = full[len(said):].strip() if full.startswith(said) else ""
        if more and random.random() < MORE_CHANCE:
            self.say("Want to hear a bit more?")
            self.j.late_yes = (time.time() + 30, lambda: self.say(more))

    def web(self, topic):
        """One web search through Claude; no pages opened."""
        topic = topic.strip(" ?.!")
        web = self.j.skills.web
        self.say(["Checking the web.", "Looking that up."])
        answer = web._claude("Search the web and answer: %s" % topic, LOOKUP_SYSTEM, tools="WebSearch", allow=True)
        if not answer:
            return
        if answer.get("card"):
            self.j.show({"title": topic[:60].capitalize(), "text": str(answer["card"])})
        self.j.claude_says(str(answer.get("say") or "It's on the card."))

    def answer(self, text):
        """Last stop before Claude: "who is X" / "what is X" about a topic, answered from Wikipedia (or the web
        when it's about something current). False hands it on to Claude."""
        m = QUESTION.match(text.strip().rstrip("?.!"))
        topic = m and (m.group(1) or m.group(2)).strip()
        if not topic or PERSONAL.search(topic) or len(topic.split()) > 6:
            return False
        if CURRENT.search(topic):
            if self.j.mode == "sys":
                return False
            self.web(text)
            return True
        return self.wiki(topic, quiet=True) is not False
