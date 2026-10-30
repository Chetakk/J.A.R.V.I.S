"""Chat replies: JARVIS's own answers to things that aren't a request ("do you have any pets?", "I'm having a rough
day"), so they don't all go to Claude.

config/chat.yaml holds topics, each with its wordings and replies. What you said is matched by meaning with the
meaning matcher's model (core/intent.py), so a wording needn't be said word for word. Runs after every command,
skill and meaning match has missed, just before Claude, so it never takes a real request.

When a reply asks you something back ("Do you have any pets?"), your next words get a matching answer from the
topic's "then" lines ("yes, a dog" -> "Lovely. Give them a pat from me."), for up to a minute.
"""
import hashlib
import os
import re
import time

import numpy as np
import yaml

from core.intent import MODEL_DIR
from core.persona import fill
from voice.speech import pick

CACHE = os.path.join(MODEL_DIR, "chat.npz")
ASKED_FOR = 60  # seconds an answer to its question counts as one
YES = re.compile(r"^(yes|yeah|yep|yup|ya|yah|sure|of course|definitely|absolutely|totally|certainly|i do|i have|i did|"
                 r"i am|i'm|a bit|a little|kind of|sort of)\b", re.I)
NO = re.compile(r"^(no|nope|nah|not really|not yet|not at all|never|i don't|i do not|i haven't|i have not|"
                r"i didn't|i'm not)\b", re.I)
BAD = re.compile(r"\b(bad|terrible|awful|horrible|rough|hard|tough|stressful|exhausting|tiring|meh|so so|sucks|"
                 r"sucked|could be better|not (good|great|so good|too good))\b", re.I)
GOOD = re.compile(r"\b(not bad|good|great|fine|okay|ok|alright|amazing|awesome|fantastic|brilliant|nice|wonderful|"
                  r"lovely|busy)\b", re.I)
# A question or a request of your own isn't an answer to its question: it goes on as usual.
NOT_AN_ANSWER = re.compile(r"\?$|^(what|why|how|who|where|when|which|can|could|would|should|do|does|did|is|are|will|"
                           r"tell me|open|play|show|set|close|turn|start|stop|find|search)\b", re.I)
GENERAL = {"yes": ["Nice.", "Good to hear."], "no": ["Fair enough.", "Okay, no worries."],
           "good": ["Glad to hear it, {name}."], "bad": ["Sorry to hear that, {name}. I'm here if you need anything."]}


class ChatReplies:
    def __init__(self, path, embed, threshold=0.8, margin=0.02):
        """embed: the meaning matcher's embed(), so the model is loaded once."""
        self.embed, self.threshold, self.margin = embed, threshold, margin
        with open(path, encoding="utf-8") as f:
            topics = yaml.load(f, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}  # C: ~10x faster
        self.replies = {name: t["replies"] for name, t in topics.items() if t.get("replies")}
        self.then = {name: t.get("then") or {} for name, t in topics.items()}
        self.asked = None  # (topic, when) after a reply that asked you something
        self.topics, examples = [], []
        for name, t in topics.items():
            if name in self.replies:
                for s in t.get("says") or []:
                    self.topics.append(name)
                    examples.append(s)
        self.matrix = self._cached(examples)

    def _cached(self, examples):
        """Wording vectors, reused from disk while the list is unchanged (~5,000 wordings take ~15 s to embed)."""
        key = hashlib.sha1("\n".join(examples).encode()).hexdigest()
        try:
            data = np.load(CACHE)
            if str(data["key"]) == key:
                return data["matrix"]
        except (OSError, KeyError, ValueError):
            pass
        matrix = self.embed(examples)
        try:
            np.savez(CACHE, key=key, matrix=matrix)
        except OSError as e:
            print("[chat] can't cache wording vectors: %r" % e)
        return matrix

    def match(self, text):
        """(topic, score), or (None, best score) when nothing is close enough or two topics are as close."""
        scores = self.matrix @ self.embed([text])[0]
        order = np.argsort(-scores)
        best = order[0]
        rival = next((i for i in order[1:] if self.topics[i] != self.topics[best]), None)
        score = float(scores[best])
        if score < self.threshold or (rival is not None and score - scores[rival] < self.margin):
            return None, score
        return self.topics[best], score

    def reply(self, text, name=None, me="Jarvis", real=None):
        """What to say back, or None. name: how to address you (a list: one is picked, "sir" or "Ckay");
        real: who you are, for a reply about you ("Chetak built me")."""
        topic, score = self.last = self.match(text)
        if not topic:
            return None
        replies = [r for r in self.replies[topic] if real or "{real}" not in r] or self.replies[topic]
        line = pick(replies).replace("{real}", real or "you")
        self.asked = (topic, time.time()) if line.endswith("?") else None
        return fill(line, name, me)

    def ask(self, topic, name=None, me="Jarvis"):
        """A question of its own from `topic` (e.g. "wiki_health" after a Wikipedia answer), remembered like a reply
        that asked something, so your answer gets that topic's "then" lines."""
        if topic not in self.replies:
            return None
        self.asked = (topic, time.time())
        return fill(pick(self.replies[topic]), name, me)

    def take(self):
        """The topic of its last question, if that was under a minute ago; asked only once."""
        asked, self.asked = self.asked, None
        return asked[0] if asked and time.time() - asked[1] < ASKED_FOR else None

    def answer(self, topic, text, name=None, me="Jarvis", quick=False):
        """What to say to your answer to its question about `topic`, or None (a question or request of your own;
        or nothing fits). quick: only a short yes / no / good / bad ("nope", "pretty good"), before the small talk."""
        text = text.strip(" .!,")
        if NOT_AN_ANSWER.search(text):
            return None
        said = ["yes" if YES.match(text) else "no" if NO.match(text) else None,
                "bad" if BAD.search(text) and not re.search(r"\bnot bad\b", text, re.I) else
                "good" if GOOD.search(text) else None]
        kinds = [k for k in said if k]
        if quick and (len(text.split()) > 4 or not kinds):
            return None
        then = self.then.get(topic) or {}
        # Its own line for what you said, else a general one ("Fair enough."); "other" only for an answer that's
        # none of those ("a golden retriever"), so "no" never gets "Good choice".
        lines = next((then[k] for k in kinds if then.get(k)), None) or (
            GENERAL[kinds[0]] if kinds else then.get("other"))
        return fill(pick(lines), name, me) if lines else None
