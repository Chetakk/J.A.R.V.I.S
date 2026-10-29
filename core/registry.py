"""Loads commands.yaml and matches what the user said to a command."""
import difflib
import re
import shlex

import yaml

from voice.speech import pick

FUZZY_CUTOFF = 0.82
_SLOT = re.compile(r"\{(\w+)\}")
# Slots that only take a certain kind of value ("volume {level}" mustn't take "down, brightness low ...").
SLOT_PATTERNS = {"level": r"\d{1,3}"}
# "turn on airplane mode" is one letter away from "turn off airplane mode": never match across these.
_OPPOSITES = [("on", "off"), ("up", "down"), ("enable", "disable"), ("open", "close"), ("show", "hide"),
              ("start", "stop"), ("louder", "quieter"), ("brighter", "darker"), ("in", "out"), ("max", "min"),
              ("mute", "unmute"), ("connect", "disconnect"), ("lock", "unlock"), ("next", "previous"),
              ("start", "end"), ("begin", "end"), ("start", "finish")]


def opposite_direction(said, phrase):
    """True if `phrase` goes the other way from what was said ("on" said, "off" in the phrase)."""
    a, b = set(re.findall(r"[a-z]+", said.lower())), set(re.findall(r"[a-z]+", phrase.lower()))
    return any((x in a and y in b and y not in a) or (y in a and x in b and x not in a) for x, y in _OPPOSITES)
_GREETING = r"(?:hey|hello|hi|ok|okay)"
_TRAILING_FILLER = re.compile(r"[\s,]*(?:please|for me|now)?[\s.!?]*$", re.IGNORECASE)


def _names_regex(names):
    """One regex for all the assistant's names; "e v" also matches "E.V." and "EV."."""
    alts = [r"[\s.]*".join(re.escape(part) for part in n.lower().split()) + r"\.?" for n in names]
    # A whole word only: "ev" must not match the start of "every".
    return r"(?:%s)(?![a-z])" % "|".join(sorted(alts, key=len, reverse=True))


def clean(text, names=("jarvis",)):
    """Strip the assistant's name, politeness and punctuation; keeps the original case."""
    if isinstance(names, str):
        names = [names]
    name = _names_regex(names)
    text = text.strip()
    # Speech can pick up a stray word or two before the name ("page arguments. Hey Jarvis, ...").
    # Only cut when the name is used to address the assistant, not when it's part of a question.
    m = re.search(r"(?:[.!?,]\s*|\b%s[\s,]+)%s[\s,.!?]*" % (_GREETING, name), text[:40], re.IGNORECASE)
    if m:
        # ...unless nothing follows the name ("thank you, Jarvis"): then what came before is the request.
        text = text[m.end():] if text[m.end():].strip() else text[:m.start()]
    text = re.sub(r"^(?:%s[\s,.!]+)?(?:%s[\s,.!?]*)?(?:(?:can|could|would|will) you\s+)?(?:please\s+)?"
                  % (_GREETING, name), "", text, flags=re.IGNORECASE)
    text = _TRAILING_FILLER.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


class Match:
    def __init__(self, command, slots):
        self.command = command
        self.slots = slots

    @property
    def name(self):
        return self.command["name"]

    def shell(self):
        """The command's shell string with slot values safely quoted."""
        quoted = {k: shlex.quote(v) for k, v in self.slots.items()}
        return _SLOT.sub(lambda m: quoted.get(m.group(1), m.group(0)), self.command["run"])

    def reply(self, output=""):
        return self.fill(self.command.get("reply", "Done"), output)

    def fill(self, text, output=""):
        """`text` (or one of its alternatives) with the slot values filled in; None stays None."""
        if text is None:
            return None
        values = dict(self.slots, output=output)
        return _SLOT.sub(lambda m: values.get(m.group(1), m.group(0)), str(pick(text)))


class Registry:
    def __init__(self, path):
        self.path = path
        self.reload()

    def reload(self):
        with open(self.path) as f:
            data = yaml.safe_load(f) or {}
        self.browsers = data.get("browsers") or {}
        self.sites = data.get("sites") or {}
        self.tab_groups = data.get("tab_groups") or {}
        self.commands = data.get("commands") or []
        self._exact = {}     # phrase without slots -> command
        self._patterns = []  # (regex, command), longest literal text first
        for cmd in self.commands:
            for phrase in cmd.get("say") or []:
                phrase = str(phrase).lower().strip()
                if _SLOT.search(phrase):
                    parts = _SLOT.split(phrase)
                    regex = "".join(re.escape(p) if i % 2 == 0 else "(?P<%s>%s)" % (p, SLOT_PATTERNS.get(p, ".+?"))
                                    for i, p in enumerate(parts))
                    literal = len(_SLOT.sub("", phrase))
                    self._patterns.append((literal, re.compile("^%s$" % regex, re.IGNORECASE), cmd))
                else:
                    self._exact[phrase] = cmd
        self._patterns.sort(key=lambda p: -p[0])

    def match_exact(self, text):
        low = text.lower()
        if low in self._exact:
            return Match(self._exact[low], {})
        for _, regex, cmd in self._patterns:
            m = regex.match(text)
            if m:
                return Match(cmd, {k: v.strip() for k, v in m.groupdict().items()})
        return None

    def match_fuzzy(self, text):
        best = [p for p in difflib.get_close_matches(text.lower(), self._exact.keys(), n=5, cutoff=FUZZY_CUTOFF)
                if not opposite_direction(text, p)]
        return Match(self._exact[best[0]], {}) if best else None

    def has_name(self, name):
        return any(c.get("name") == name for c in self.commands)

    def add(self, name, phrases, run, reply, output=False):
        """Append a new command to commands.yaml (used when saving Claude's answers)."""
        entry = {"name": name, "say": list(phrases), "run": run, "reply": reply}
        if output:
            entry["output"] = True
        block = yaml.safe_dump([entry], sort_keys=False, default_flow_style=None, width=100)
        with open(self.path, "a") as f:
            f.write("\n" + "\n".join("  " + line for line in block.splitlines()) + "\n")
        self.reload()
