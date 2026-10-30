"""Meaning matcher: maps differently-worded requests onto phrases JARVIS already knows.

"kill the music" -> "pause the music". Runs after the regex skills and command list miss, before
Claude. Uses all-MiniLM-L6-v2 (int8 ONNX, ~23 MB) with a small built-in WordPiece tokenizer, so
nothing beyond onnxruntime and numpy is needed. ~10-20 ms per sentence on the Orin's CPU.
"""
import hashlib
import os
import re
import unicodedata

import numpy as np
import yaml

from core.registry import opposite_direction

MODEL_DIR = os.path.expanduser("~/.local/share/jarvis/models/minilm")
CACHE = os.path.join(MODEL_DIR, "phrases.npz")
# A small embedding model scores by topic, not by action: "make me a reminder" sits right next to "what are my
# reminders". So a request that does something never becomes a phrase that only asks something.
DOING = re.compile(r"^(?:(?:i want you to|i need you to|i'd like you to|i want to|i need to|i'd like to|please|just|"
                   r"go ahead and|let's) )?(?:set|add|make|create|start|new|put|save|schedule|book|write|record|change|"
                   r"switch|adjust|connect|join|install|remind|(?:give|get) me an?|(?:i'd like|i want|i need) an?)\b", re.I)
ASK_FOR_VALUE = "ask for the value"
ASKING = re.compile(r"^(?:what|what's|which|how|show|list|read|tell me|any|is|am|are|where|when|who|did|do i|does|"
                    r"has|have i)\b", re.I)


class WordPiece:
    """Lower-cased BERT WordPiece, enough for short spoken English."""

    def __init__(self, vocab_path):
        with open(vocab_path, encoding="utf-8") as f:
            self.vocab = {w.rstrip("\n"): i for i, w in enumerate(f)}
        self.unk, self.cls, self.sep = self.vocab["[UNK]"], self.vocab["[CLS]"], self.vocab["[SEP]"]

    def encode(self, text, max_len=64):
        text = unicodedata.normalize("NFD", text.lower())
        text = "".join(c for c in text if unicodedata.category(c) != "Mn")
        ids = [self.cls]
        for word in re.findall(r"\w+|[^\w\s]", text):
            start = 0
            while start < len(word):
                end, piece = len(word), None
                while end > start:
                    sub = word[start:end] if start == 0 else "##" + word[start:end]
                    if sub in self.vocab:
                        piece = self.vocab[sub]
                        break
                    end -= 1
                if piece is None:
                    ids.append(self.unk)
                    break
                ids.append(piece)
                start = end
        return ids[:max_len - 1] + [self.sep]


def _norm(text):
    return " ".join(re.findall(r"[\w']+|:", text.lower()))


def wrong_kind(said, phrase):
    """True if `said` asks to do something and `phrase` only asks a question ("add a todo" / "what's on my todo list")."""
    return bool(DOING.match(said)) and bool(ASKING.match(phrase))


def _value(slot, value):
    """{when}: "5 pm" -> "at 5 pm", "20 minutes" -> "in 20 minutes" ("tomorrow at 9 am" stays)."""
    value = value.strip()
    if slot != "when" or re.match(r"^(?:in|at|today|tonight|tomorrow)\b", value, re.IGNORECASE):
        return value
    if re.search(r"\d\s*(?:[ap]\.?\s?m\b|:\d)|\b(?:noon|midnight|o'?clock)\b", value, re.IGNORECASE):
        return "at " + value
    return "in " + value


_NUM = (r"(?:\d[\d:.]*|an?|half an|a couple of|a few|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
        r"twelve|fifteen|twenty|thirty|forty(?: five)?|fifty|ninety)")
# Slots that only take a certain kind of value; any other {slot} takes any words.
SLOTS = {
    # "20 minutes", "5 pm", "in an hour", "tomorrow at 9 am", "noon"
    "when": r"(?:(?:in|at) )?(?:(?:today|tonight|tomorrow)(?: at)? ?)?(?:%s(?: ?(?:[ap]\.? ?m\.?|minutes?|mins?|"
            r"hours?|hrs?|seconds?|secs?|o'?clock))?|noon|midnight)" % _NUM,
    "level": r"\d{1,3}",  # a volume or brightness ("set volume to {level}" mustn't take "30 and open spotify")
    "dur": r"%s ?(?:minutes?|mins?|hours?|hrs?|seconds?|secs?)" % _NUM,
    "a": r"-?[\d.,]+", "b": r"-?[\d.,]+",  # sums take numbers only ("add a note to X" isn't one)
    "n": r"first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th|one|two|three|four|five|\d",
    # a todo: never "I'm", "it", "that" ("I'm done" isn't a todo), nor a question ("is the task done")
    "item": r"(?!(?:i'?m|i am|it|that|this|we'?re|you'?re|all|everything)$)"
            r"(?!(?:is|was|are|were|has|have|had|did|does|do|which|what|how|when|where|who|why|will|would|can|could|"
            r"should)\b).+?",
    # an app, never "down ..." ("shut down the computer") or the computer itself
    "app": r"(?!down\b|off\b|up\b)(?!.*\b(?:then|and)\b)(?!(?:the )?(?:computer|system|jetson|laptop|pc|machine|wifi|bluetooth|tab|window|"
           r"everything|all|it all)$).+?",
}


def template_regex(wording):
    """"shut {app} down" -> ^shut (?P<app>.+?) down$; a stray "the"/"my" before the value is dropped."""
    parts = re.split(r"\{(\w+)\}", wording)
    keep = ("what", "text")  # a reminder's or a typed text's own words stay as said
    pattern = "".join(re.escape(p) if i % 2 == 0 else "%s(?P<%s>%s)" % ("" if p in keep else "(?:the |my )?", p,
                                                                       SLOTS.get(p, ".+?"))
                      for i, p in enumerate(parts))
    return re.compile("^%s$" % pattern, re.IGNORECASE)


class IntentMatcher:
    def __init__(self, path, threshold=0.72, margin=0.03, extra=None, learned=None):
        """extra: more {phrase: [other wordings]}, e.g. the command list's own "say" phrases.
        learned: {what you said: phrase} learned from Claude (core/learned.py); treated like this file's own."""
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(os.path.join(MODEL_DIR, "model_quantized.onnx"), opts,
                                            providers=["CPUExecutionProvider"])
        self.tok = WordPiece(os.path.join(MODEL_DIR, "vocab.txt"))
        self.threshold, self.margin = threshold, margin
        with open(path, encoding="utf-8") as f:
            intents = yaml.safe_load(f) or {}
        # "ask for the value:" holds bare requests ("set a timer") whose handler asks for what's missing.
        # They never take a request that already says a number ("copy this to workspace 2"): asking for it again
        # would be wrong, and a request that really was one of them has already been taken by its pattern.
        self.bare = set(intents.get(ASK_FOR_VALUE) or {})
        intents.update(intents.pop(ASK_FOR_VALUE, None) or {})
        for said, target in (learned or {}).items():
            intents[target] = list(intents.get(target) or []) + [said]
        own = {k: set(v or []) for k, v in intents.items()}  # only this file's wordings get the word-for-word check
        for target, says in (extra or {}).items():
            intents[target] = list(intents.get(target) or []) + list(says)
        self.targets, examples = [], []
        self.templates = []  # (regex of another wording with {slots}, known phrase with the same {slots})
        self.exact = {}  # another wording, word for word -> the known phrase (checked before anything else)
        for target, says in intents.items():
            if "{" in target:
                for s in [target] + list(says or []):
                    self.templates.append((template_regex(s), target))
                continue
            for s in [target] + list(says or []):
                self.targets.append(target)
                examples.append(s)
                if s in own.get(target, ()):
                    self.exact.setdefault(_norm(s), target)
        self.templates.sort(key=lambda t: -len(re.sub(r"\(\?.*?\)", "", t[0].pattern)))  # most fixed words first
        self.matrix = self._cached(examples)
        self.nearest = (None, 0.0)

    def _cached(self, examples):
        """Phrase vectors, reused from disk while the phrase list is unchanged (saves ~2 s at startup)."""
        key = hashlib.sha1("\n".join(["one by one"] + examples).encode()).hexdigest()  # (see embed)
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
            print("[intent] can't cache phrase vectors: %r" % e)
        return matrix

    def embed(self, sentences):
        """One at a time: in a batch, padding and the quantised model's per-batch ranges shift every vector according
        to what else is in the batch (as far as 0.91 from itself), so adding one long sentence could flip a match
        anywhere else. It costs nothing: ~3 ms each, the same as a batch."""
        return np.vstack([self._embed_one(s) for s in sentences])

    def _embed_one(self, sentence):
        ids = np.array([self.tok.encode(sentence)], dtype=np.int64)
        mask = np.ones_like(ids)
        out = self.session.run(None, {"input_ids": ids, "attention_mask": mask,
                                      "token_type_ids": np.zeros_like(ids)})[0]
        vec = (out * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
        return vec / np.linalg.norm(vec, axis=1, keepdims=True)

    def learn(self, said, target):
        """Add one wording while running (word for word, and by meaning); no rebuild needed."""
        self.exact[_norm(said)] = target
        self.targets.append(target)
        self.matrix = np.vstack([self.matrix, self.embed([said])])

    def early(self, text):
        """A listed other wording, said word for word: its known phrase, before any pattern can misread it
        ("kill the wifi" isn't "kill {app}", "start working" isn't a project)."""
        return self.exact.get(_norm(text))

    def fill(self, text):
        """Another wording with a value in it -> the known phrase with that value ("shut chrome" -> "close chrome")."""
        for regex, target in self.templates:
            m = regex.match(text.strip().rstrip("?.!"))
            if m:
                return re.sub(r"\{(\w+)\}", lambda s: _value(s.group(1), m.group(s.group(1))), target)
        return None

    def _against(self, text, phrase):
        """`phrase` goes the other way ("on" said, "off" in it), only asks when you asked to do something, or would
        ask for a value you already said."""
        return opposite_direction(text, phrase) or wrong_kind(text, phrase) or (
            phrase in self.bare and bool(re.search(r"\d", text)))

    def candidates(self, text, n=2):
        """The n best different phrases for `text`, as [(phrase, score)] (going the same way as what was said)."""
        scores = self.matrix @ self.embed([text])[0]
        out = []
        for i in np.argsort(-scores):
            if self.targets[i] not in [t for t, _ in out] and not self._against(text, self.targets[i]):
                out.append((self.targets[i], float(scores[i])))
                if len(out) == n:
                    break
        return out

    def match(self, text):
        """(known phrase, score), or (None, best score) when nothing is close enough."""
        scores = self.matrix @ self.embed([text])[0]
        order = [i for i in np.argsort(-scores) if not self._against(text, self.targets[i])]
        if not order:
            return None, 0.0
        best = order[0]
        # The runner-up must be clearly worse unless it points at the same phrase.
        rival = next((i for i in order[1:] if self.targets[i] != self.targets[best]), None)
        score = float(scores[best])
        self.nearest = (self.targets[best], score)  # for the miss log, even when it's not a match
        if score < self.threshold or (rival is not None and score - scores[rival] < self.margin):
            return None, score
        return self.targets[best], score
