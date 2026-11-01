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

from core.registry import _TRAILING_FILLER, opposite_direction

MODELS = os.path.expanduser("~/.local/share/jarvis/models")
MODEL_DIR = os.path.join(MODELS, "minilm")  # the model as downloaded (chat replies always use this one)
# A small embedding model scores by topic, not by action: "make me a reminder" sits right next to "what are my
# reminders". So a request that does something never becomes a phrase that only asks something.
DOING = re.compile(r"^(?:(?:i want you to|i need you to|i'd like you to|i want to|i need to|i'd like to|please|just|"
                   r"go ahead and|let's) )?(?:set|add|make|create|start|new|put|save|schedule|book|write|record|change|"
                   r"switch|adjust|connect|join|install|remind|(?:give|get) me an?|(?:i'd like|i want|i need) an?)\b", re.I)
ASK_FOR_VALUE = "ask for the value"
# A learned wording that "means" this is near one of JARVIS's phrases but means none of them ("what's the volume
# of a sphere" by "volume up"): when it's the nearest, the matcher leaves the request alone, for Claude.
LEAVE = "(leave it)"
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
    """For comparing word for word. What you say has a trailing "please", "for me" or "now" taken off before it gets
    here (core/registry.py), so a wording ending in one has to compare without it or it could never match."""
    text = text.lower()
    return " ".join(re.findall(r"[\w']+|:", _TRAILING_FILLER.sub("", text) or text))


def wrong_kind(said, phrase):
    """True if `said` asks to do something and `phrase` only asks a question ("add a todo" / "what's on my todo list")."""
    return bool(DOING.match(said)) and bool(ASKING.match(phrase))


# A loose match never turns the computer off: "please shut off" is "be quiet", not "shut down".
POWER = {"shut down", "reboot"}
COMPUTER = re.compile(r"\b(?:computer|pc|laptop|machine|system|jetson|desktop|down|power|reboot|restart)\b", re.I)
# Nor is the weather the CPU's temperature: "temperature outside", "what's the temperature in delhi".
HEAT = {"temperature", "gpu temperature"}
WEATHER = re.compile(r"\b(?:outside|weather|forecast|today|tonight|tomorrow|rain|cold|warm|in (?!the (?:cpu|gpu)))\b",
                     re.I)
CHIP = re.compile(r"\b(?:cpu|gpu|computer|processor|chip|jetson|board|fan)\b", re.I)


def wrong_thing(said, phrase):
    """True if `phrase` would shut the computer down without it being named, or read the CPU's heat for the weather."""
    return (phrase in POWER and not COMPUTER.search(said)) or (
        phrase in HEAT and bool(WEATHER.search(said)) and not CHIP.search(said))


def _value(slot, value):
    """{when}: "5 pm" -> "at 5 pm", "20 minutes" -> "in 20 minutes" ("tomorrow at 9 am" stays).
    {level}: "seventy percent" -> "70"."""
    value = value.strip()
    if slot == "level":
        return _level(value)
    if slot != "when" or re.match(r"^(?:in|at|today|tonight|tomorrow)\b", value, re.IGNORECASE):
        return value
    if re.search(r"\d\s*(?:[ap]\.?\s?m\b|:\d)|\b(?:noon|midnight|o'?clock)\b", value, re.IGNORECASE):
        return "at " + value
    return "in " + value


_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
        "seventeen eighteen nineteen".split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
# 0-100 in digits or words: "70", "seventy", "fifty five", "a hundred"; "percent" after it is allowed
_LEVEL = r"(?:\d{1,3}|(?:%s)(?:[ -](?:%s))?|%s|(?:a |one )?hundred)(?: ?(?:percent|per cent|%%))?" % (
    "|".join(_TENS), "|".join(_ONES[1:10]), "|".join(_ONES))


def _level(value):
    """"seventy five percent" -> "75" (digits stay as they are)."""
    value = re.sub(r" ?(?:percent|per cent|%)$", "", value.lower()).strip()
    if value.isdigit():
        return value
    if value.endswith("hundred"):
        return "100"
    words = re.split(r"[ -]", value)
    if words[0] in _TENS:
        return str(10 * (_TENS.index(words[0]) + 2) + (_ONES.index(words[1]) if len(words) > 1 else 0))
    return str(_ONES.index(value))


_NUM = (r"(?:\d[\d:.]*|an?|half an|a couple of|a few|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
        r"twelve|fifteen|twenty|thirty|forty(?: five)?|fifty|ninety)")
# Slots that only take a certain kind of value; any other {slot} takes any words.
SLOTS = {
    # "20 minutes", "5 pm", "in an hour", "tomorrow at 9 am", "noon"
    "when": r"(?:(?:in|at) )?(?:(?:today|tonight|tomorrow)(?: at)? ?)?(?:%s(?: ?(?:[ap]\.? ?m\.?|minutes?|mins?|"
            r"hours?|hrs?|seconds?|secs?|o'?clock))?|noon|midnight)" % _NUM,
    "level": _LEVEL,  # a volume or brightness ("set volume to {level}" mustn't take "30 and open spotify")
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


class Embedder:
    """A sentence -> its vector, with one of the MiniLM models in ~/.local/share/jarvis/models: "minilm" as
    downloaded, or "minilm-tuned", trained on JARVIS's own wordings (tools/finetune.py)."""

    def __init__(self, model_dir=MODEL_DIR):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.model_dir = model_dir
        self.session = ort.InferenceSession(os.path.join(model_dir, "model_quantized.onnx"), opts,
                                            providers=["CPUExecutionProvider"])
        self.tok = WordPiece(os.path.join(model_dir, "vocab.txt"))

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


class IntentMatcher:
    def __init__(self, path, threshold=0.72, margin=0.03, extra=None, learned=None, wrong=None, model=None):
        """extra: more {phrase: [other wordings]}, e.g. the command list's own "say" phrases.
        learned: {what you said: phrase} learned from Claude (core/learned.py); treated like this file's own.
        wrong: {what you said: [phrases]} it must never mean (core/learned.py), word for word.
        model: the folder in ~/.local/share/jarvis/models to use ("minilm" when not given; settings intents.model)."""
        self.embedder = Embedder(os.path.join(MODELS, model) if model else MODEL_DIR)
        self.model_dir = self.embedder.model_dir
        self.embed = self.embedder.embed
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
        own = {k: set(v or []) for k, v in intents.items() if k != LEAVE}  # only these get the word-for-word check
        for target, says in (extra or {}).items():
            intents[target] = list(intents.get(target) or []) + list(says)
        self.targets, self.examples = [], []  # one row each: the phrase, and the wording that stands for it
        self.mine = {_norm(s) for s in learned or {}}  # the wordings that came from learned.yaml (they can be forgotten)
        self.wrong = {}
        for said, phrases in (wrong or {}).items():
            self.wrong.setdefault(_norm(said), set()).update(phrases)
        self.templates = []  # (regex of another wording with {slots}, known phrase with the same {slots})
        self.exact = {}  # another wording, word for word -> the known phrase (checked before anything else)
        for target, says in intents.items():
            if "{" in target:
                for s in [target] + list(says or []):
                    self.templates.append((template_regex(s), target))
                continue
            for s in [target] + list(says or []):
                self.targets.append(target)
                self.examples.append(s)
                if s in own.get(target, ()):
                    self.exact.setdefault(_norm(s), target)
        self.templates.sort(key=lambda t: -len(re.sub(r"\(\?.*?\)", "", t[0].pattern)))  # most fixed words first
        for said in self.wrong:
            if self.exact.get(said) in self.wrong[said]:
                del self.exact[said]  # (a code wording found wrong: it's still a row, just never taken word for word)
        self.matrix = self._cached(self.examples)
        self.nearest = (None, 0.0)
        self.winner = None  # the wording that won the last match(): what to forget if it turns out wrong

    def _cached(self, examples):
        """Phrase vectors, reused from disk while the phrase list is unchanged (saves ~2 s at startup)."""
        key = hashlib.sha1("\n".join(["one by one"] + examples).encode()).hexdigest()  # (see embed)
        cache = os.path.join(self.model_dir, "phrases.npz")  # (each model its own: their vectors differ)
        try:
            data = np.load(cache)
            if str(data["key"]) == key:
                return data["matrix"]
        except (OSError, KeyError, ValueError):
            pass
        matrix = self.embed(examples)
        try:
            np.savez(cache, key=key, matrix=matrix)
        except OSError as e:
            print("[intent] can't cache phrase vectors: %r" % e)
        return matrix

    def learn(self, said, target):
        """Add one wording while running (word for word, and by meaning); no rebuild needed."""
        if target != LEAVE:  # (leaving it alone word for word would stop nothing: only by meaning counts)
            self.exact[_norm(said)] = target
        self.mine.add(_norm(said))
        self.targets.append(target)
        self.examples.append(said)
        self.matrix = np.vstack([self.matrix, self.embed([said])])

    def forget(self, said):
        """Take back a learned wording, word for word and by meaning. The phrase it stood for, or None if it wasn't
        one of the learned ones (config/intents.yaml's own wordings are the code's: those can only be blocked)."""
        said = _norm(said)
        if said not in self.mine:
            return None
        keep = [i for i, s in enumerate(self.examples) if _norm(s) != said]
        gone = next((self.targets[i] for i in range(len(self.examples)) if i not in set(keep)), None)
        self.targets = [self.targets[i] for i in keep]
        self.examples = [self.examples[i] for i in keep]
        self.matrix = self.matrix[keep]
        self.mine.discard(said)
        if self.exact.get(said) == gone:
            del self.exact[said]
        return gone

    def block(self, said, phrase):
        """`said`, word for word, never means `phrase` again."""
        said = _norm(said)
        self.wrong.setdefault(said, set()).add(phrase)
        if self.exact.get(said) == phrase:
            del self.exact[said]

    def early(self, text):
        """A listed other wording, said word for word: its known phrase, before any pattern can misread it
        ("kill the wifi" isn't "kill {app}", "start working" isn't a project)."""
        phrase = self.exact.get(_norm(text))
        if phrase:
            self.winner = text
        return phrase

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
        return opposite_direction(text, phrase) or wrong_kind(text, phrase) or wrong_thing(text, phrase) or (
            phrase in self.bare and bool(re.search(r"\d", text))) or phrase in self.wrong.get(_norm(text), ())

    def candidates(self, text, n=2):
        """The n best different phrases for `text`, as [(phrase, score)] (going the same way as what was said)."""
        scores = self.matrix @ self.embed([text])[0]
        out = []
        for i in np.argsort(-scores):
            if self.targets[i] != LEAVE and self.targets[i] not in [t for t, _ in out] \
                    and not self._against(text, self.targets[i]):
                out.append((self.targets[i], float(scores[i])))
                if len(out) == n:
                    break
        return out

    def left(self, text):
        """True if the nearest row is a "leave it" one: this is for Claude, so don't ask "did you mean" either."""
        scores = self.matrix @ self.embed([text])[0]
        best = next((i for i in np.argsort(-scores) if not self._against(text, self.targets[i])), None)
        return best is not None and self.targets[best] == LEAVE

    def match(self, text):
        """(known phrase, score), or (None, best score) when nothing is close enough."""
        phrase, score, best = self.pick(text, self.matrix @ self.embed([text])[0])
        self.nearest = (self.targets[best], score) if best is not None and self.targets[best] != LEAVE \
            else (None, 0.0)  # for the miss log
        self.winner = self.examples[best] if phrase else None
        return phrase, score

    def pick(self, text, scores):
        """(phrase, score, row) from `text`'s scores against every row; phrase is None when nothing is close enough
        (row is still the nearest one), row is None when nothing goes the same way."""
        order, against = (i for i in np.argsort(-scores)), {}  # (checked lazily: only the top few ever are)
        fits = lambda i: not against.setdefault(self.targets[i], self._against(text, self.targets[i]))  # noqa: E731
        best = next((i for i in order if fits(i)), None)
        if best is None:
            return None, 0.0, None
        # The runner-up must be clearly worse unless it points at the same phrase.
        rival = next((i for i in order if self.targets[i] != self.targets[best] and fits(i)), None)
        score = float(scores[best])
        if self.targets[best] == LEAVE or score < self.threshold or (
                rival is not None and score - scores[rival] < self.margin):
            return None, score, best
        return self.targets[best], score, best

    def predict(self, texts, vectors):
        """[(phrase or None, the wording that won or None)] for many sentences whose vectors are already known
        (a "leave it" row that won gives (None, it)):
        what early() then match() would give, without embedding anything (core/heldout.py)."""
        out, all_scores = [], vectors @ self.matrix.T
        for text, scores in zip(texts, all_scores):
            phrase = self.exact.get(_norm(text))
            if phrase:
                out.append((phrase, text))
                continue
            phrase, _, best = self.pick(text, scores)
            left = best is not None and self.targets[best] == LEAVE  # (a "leave it" row did that: it can be blamed)
            out.append((phrase, self.examples[best]) if phrase or left else (None, None))
        return out
