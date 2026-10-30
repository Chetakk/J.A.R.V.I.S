"""Chat replies: JARVIS's own answers to things that aren't a request ("do you have any pets?", "I'm having a rough
day"), so they don't all go to Claude.

config/chat.yaml holds topics, each with its wordings and replies. What you said is matched by meaning with the
meaning matcher's model (core/intent.py), so a wording needn't be said word for word. Runs after every command,
skill and meaning match has missed, just before Claude, so it never takes a real request.
"""
import hashlib
import os

import numpy as np
import yaml

from core.intent import MODEL_DIR
from core.persona import fill
from voice.speech import pick

CACHE = os.path.join(MODEL_DIR, "chat.npz")


class ChatReplies:
    def __init__(self, path, embed, threshold=0.8, margin=0.02):
        """embed: the meaning matcher's embed(), so the model is loaded once."""
        self.embed, self.threshold, self.margin = embed, threshold, margin
        with open(path, encoding="utf-8") as f:
            topics = yaml.load(f, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}  # C: ~10x faster
        self.replies = {name: t["replies"] for name, t in topics.items() if t.get("replies")}
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
        return fill(line, name, me)
