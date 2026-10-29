"""The conversation so far: follow-ups that only make sense after what JARVIS just did.

  "volume up"        -> "a bit more" (again), "too much" (the other way)
  "set volume to 40" -> "make it 70"            (same thing, new number)
  "close chrome"     -> "and firefox too"       (same thing, new name)
  "X, or Y?"         -> "the other one"
Only for a short while after the last action (FRESH seconds); otherwise these phrases mean nothing here.
"""
import re
import time

FRESH = 90

MORE = re.compile(r"^(?:(?:a |just a )?(?:little|bit|tiny bit|touch)(?: bit)? more|more|even more|more please|"
                  r"some more|keep going|further|again please|one more time|a little further)$", re.IGNORECASE)
LESS = re.compile(r"^(?:(?:a |just a )?(?:little|bit|tiny bit|touch)(?: bit)? less|less|(?:that'?s |way )?too much|"
                  r"the other way|other way|not that much|go back a bit|back a bit|too far)$", re.IGNORECASE)
VALUE = re.compile(r"^(?:no,? |actually,? |make (?:it|that) |set it to |change it to |put it at |"
                   r"make it |go to )*(\d+)(?: ?(?:percent|%))?(?: instead| please)?$", re.IGNORECASE)
TOO = re.compile(r"^(?:and |also |same for |now |do the same for |and also |same with |the same for )+"
                 r"(?:the |my )?(.+?)(?: too| as well| also)?$|^(?:and )?(?:the |my )?(.+?) (?:too|as well)$",
                 re.IGNORECASE)
OTHER = re.compile(r"^(?:no,? )?(?:i meant )?(?:the other(?: one)?|other one|the second one instead)$", re.IGNORECASE)

# "volume up" <-> "volume down": the words swapped for "too much" / "the other way".
SWAP = [("up", "down"), ("in", "out"), ("next", "previous"), ("forward", "back"), ("louder", "quieter"),
        ("brighter", "darker"), ("faster", "slower"), ("high", "low"), ("bigger", "smaller"), ("on", "off"),
        ("left", "right"), ("top", "bottom")]
# What "a bit more" can repeat: steps, not one-off actions (no "more" after "shut down").
STEP = re.compile(r"\b(?:up|down|louder|quieter|in|out|next|previous|forward|back|brighter|darker|scroll|skip|"
                  r"high|low|bigger|smaller|faster|slower)\b", re.IGNORECASE)


class Context:
    def __init__(self):
        self.text = None   # the phrase JARVIS last acted on ("volume up", "close firefox")
        self.value = None  # the name or number in it, if any ("firefox", "40")
        self.at = 0.0
        self.choice = None  # (options, picked) from the last "X, or Y?"

    def done(self, text, value=None):
        self.text, self.value, self.at = text, value, time.time()

    def chose(self, options, picked):
        self.choice = (list(options), picked)

    def follow_up(self, said):
        """What a follow-up means, as a phrase JARVIS can handle, or None."""
        if not self.text or time.time() - self.at > FRESH:
            return None
        said = said.strip().rstrip(".!?")
        if OTHER.match(said) and self.choice:
            options, picked = self.choice
            others = [o for o in options if o != picked]
            return others[0] if len(others) == 1 else None
        if MORE.match(said):
            return self.text if STEP.search(self.text) else None
        if LESS.match(said):
            swapped = _swap(self.text)
            return swapped if swapped != self.text else None
        m = VALUE.match(said)
        if m and self.value and self.value.isdigit():
            return re.sub(r"\b%s\b" % re.escape(self.value), m.group(1), self.text, count=1)
        m = TOO.match(said)
        if m and self.value and not self.value.isdigit():
            new = (m.group(1) or m.group(2)).strip()
            # a name, not a sentence ("me too", "i love you too" aren't "close me")
            if (new.lower() != self.value.lower() and len(new.split()) <= 3
                    and not re.match(r"^(?:me|you|i|it|that|this|us|them|we|same|so)\b", new, re.IGNORECASE)):
                return re.sub(re.escape(self.value), new, self.text, count=1, flags=re.IGNORECASE)
        return None


def sounds_like_follow_up(said):
    """"a bit more", "too much", "the other one": only meaningful right after an action. (Not numbers:
    "make it 40 percent" on its own still means the volume.)"""
    said = said.strip().rstrip(".!?")
    return any(p.match(said) for p in (MORE, LESS, OTHER))


def _swap(text):
    pairs = dict(SWAP + [(b, a) for a, b in SWAP])
    return re.sub(r"\b(%s)\b" % "|".join(pairs), lambda m: pairs[m.group(1).lower()], text, count=1,
                  flags=re.IGNORECASE)
