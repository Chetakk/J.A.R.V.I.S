"""Meaning matcher: maps differently-worded requests onto phrases JARVIS already knows.

"kill the music" -> "pause the music". Runs after the regex skills and command list miss, before
Claude. Uses all-MiniLM-L6-v2 (int8 ONNX, ~23 MB) with a small built-in WordPiece tokenizer, so
nothing beyond onnxruntime and numpy is needed. ~10-20 ms per sentence on the Orin's CPU.
"""
import os
import re
import unicodedata

import numpy as np
import yaml

MODEL_DIR = os.path.expanduser("~/.local/share/jarvis/models/minilm")


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


def template_regex(wording):
    """"shut {app} down" -> ^shut (?P<app>.+?) down$; a stray "the"/"my" before the value is dropped."""
    parts = re.split(r"\{(\w+)\}", wording)
    pattern = "".join(re.escape(p) if i % 2 == 0 else "(?:the |my )?(?P<%s>.+?)" % p for i, p in enumerate(parts))
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
        for target, says in (extra or {}).items():
            intents[target] = list(intents.get(target) or []) + list(says)
        self.targets, examples = [], []
        self.templates = []  # (regex of another wording with {slots}, known phrase with the same {slots})
        for target, says in intents.items():
            if "{" in target:
                for s in [target] + list(says or []):
                    self.templates.append((template_regex(s), target))
                continue
            for s in [target] + list(says or []):
                self.targets.append(target)
                examples.append(s)
        self.templates.sort(key=lambda t: -len(re.sub(r"\(\?.*?\)", "", t[0].pattern)))  # most fixed words first
        self.matrix = self.embed(examples)
        self.nearest = (None, 0.0)

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

    def fill(self, text):
        """Another wording with a value in it -> the known phrase with that value ("shut chrome" -> "close chrome")."""
        for regex, target in self.templates:
            m = regex.match(text.strip().rstrip("?.!"))
            if m:
                return re.sub(r"\{(\w+)\}", lambda s: m.group(s.group(1)).strip(), target)
        return None

    def match(self, text):
        """(known phrase, score), or (None, best score) when nothing is close enough."""
        scores = self.matrix @ self.embed([text])[0]
        order = np.argsort(-scores)
        best = order[0]
        # The runner-up must be clearly worse unless it points at the same phrase.
        rival = next((i for i in order[1:] if self.targets[i] != self.targets[best]), None)
        score = float(scores[best])
        self.nearest = (self.targets[best], score)  # for the miss log, even when it's not a match
        if score < self.threshold or (rival is not None and score - scores[rival] < self.margin):
            return None, score
        return self.targets[best], score
