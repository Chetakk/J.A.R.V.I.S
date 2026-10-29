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
    "dur": r"%s ?(?:minutes?|mins?|hours?|hrs?|seconds?|secs?)" % _NUM,
    "a": r"-?[\d.,]+", "b": r"-?[\d.,]+",  # sums take numbers only ("add a note to X" isn't one)
    "n": r"first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th|one|two|three|four|five|\d",
    # a todo: never "I'm", "it", "that" ("I'm done" isn't a todo)
    "item": r"(?!(?:i'?m|i am|it|that|this|we'?re|you'?re|all|everything)$).+?",
    # an app, never "down ..." ("shut down the computer") or the computer itself
    "app": r"(?!down\b)(?!(?:the )?(?:computer|system|jetson|laptop|pc|machine|wifi|bluetooth|tab|window)$).+?",
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
    def __init__(self, path, threshold=0.72, margin=0.03, extra=None):
        """extra: more {phrase: [other wordings]}, e.g. the command list's own "say" phrases."""
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(os.path.join(MODEL_DIR, "model_quantized.onnx"), opts,
                                            providers=["CPUExecutionProvider"])
        self.tok = WordPiece(os.path.join(MODEL_DIR, "vocab.txt"))
        self.threshold, self.margin = threshold, margin
        with open(path, encoding="utf-8") as f:
            intents = yaml.safe_load(f) or {}
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
            print("[intent] can't cache phrase vectors: %r" % e)
        return matrix

    def embed(self, sentences):
        encoded = [self.tok.encode(s) for s in sentences]
        n = max(len(e) for e in encoded)
        ids = np.zeros((len(encoded), n), dtype=np.int64)
        mask = np.zeros_like(ids)
        for i, e in enumerate(encoded):
            ids[i, :len(e)], mask[i, :len(e)] = e, 1
        out = self.session.run(None, {"input_ids": ids, "attention_mask": mask,
                                      "token_type_ids": np.zeros_like(ids)})[0]
        vec = (out * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
        return vec / np.linalg.norm(vec, axis=1, keepdims=True)

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

    def match(self, text):
        """(known phrase, score), or (None, best score) when nothing is close enough."""
        scores = self.matrix @ self.embed([text])[0]
        order = [i for i in np.argsort(-scores) if not opposite_direction(text, self.targets[i])]
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
