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
                  r"(?:a lot|lots|way|much|a bit|a little) more|"
                  r"some more|keep going|further|again please|one more time|a little further)$", re.IGNORECASE)
LESS = re.compile(r"^(?:no,? |oops,? )?(?:(?:a |just a )?(?:little|bit|tiny bit|touch)(?: bit)? less|less|"
                  r"(?:that'?s |way |it'?s )?too (?:much|far|loud|quiet|bright|dark|big|small|fast|slow)|"
                  r"the other way|other way|not that much|go back a bit|back a bit)$", re.IGNORECASE)
VALUE = re.compile(r"^(?:no,? |actually,? |make (?:it|that) |set it to |change it to |put it at |"
                   r"make it |go to )*(\d+|[a-z]+(?:[ -][a-z]+)?)(?: ?(?:percent|%))?(?: instead| please)?$",
                   re.IGNORECASE)
TOO = re.compile(r"^(?:and |also |same for |now |do the same for |and also |same with |the same for )+"
                 r"(?:the |my )?(.+?)(?: too| as well| also)?$|^(?:and )?(?:the |my )?(.+?) (?:too|as well)$",
                 re.IGNORECASE)
OTHER = re.compile(r"^(?:no,? )?(?:i meant )?(?:the other(?: one)?|other one|the second one instead)$", re.IGNORECASE)

# "volume up" <-> "volume down": the words swapped for "too much" / "the other way".
SWAP = [("up", "down"), ("next", "previous"), ("louder", "quieter"), ("brighter", "darker"), ("faster", "slower"),
        ("high", "low"), ("bigger", "smaller"), ("left", "right"), ("top", "bottom")]
# Also everyday little words ("in the hall", "back to"): swapped only when none of the above is there.
SWAP_WEAK = [("on", "off"), ("in", "out"), ("forward", "back")]
NUMBERS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                       "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
NUMBERS.update({w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split())
                if w != "_"})
NUMBERS["hundred"] = 100
# Not a name: "thank you too", "good night too", "and all apps too", "and please"
NOT_A_NAME = re.compile(r"\b(?:me|you|i|it|that|this|us|them|we|him|her|he|she|they|same|so|thanks?|love|night|"
                        r"morning|evening|day|please|all|everything|every|both|everyone|anything)\b", re.IGNORECASE)
# What follows "open" / "close" is the name, when JARVIS didn't say which part it was ("open firefox")
NAMED = re.compile(r"^(?:open|close|quit|kill|launch|start)\s+(?:the |my )?(.+)$", re.IGNORECASE)
ADDED = re.compile(r"^(?:add|put)\s+(.+?)\s+(?:to|on|in)\s+(?:my |the )?\S", re.IGNORECASE)
# What "a bit more" can repeat: steps, not one-off actions (no "more" after "shut down").
STEP = re.compile(r"\b(?:up|down|louder|quieter|in|out|next|previous|forward|back|brighter|darker|scroll|skip|"
                  r"high|low|bigger|smaller|faster|slower)\b", re.IGNORECASE)
# One-off actions with a step word in them: "shut down", "log out", "open X in VS Code" aren't repeated or reversed.
ONE_OFF = re.compile(r"^(?:shut|log|sign|power|open|close|launch|quit|kill|restart|reboot|lock|start|delete|"
                     r"send|install|run)\b", re.IGNORECASE)
# "and close firefox", "now open youtube": a new request, not the last one with a new name
VERB = re.compile(r"^(?:open|close|quit|kill|launch|start|stop|play|pause|resume|mute|unmute|set|turn|show|tell|"
                  r"what|how|search|take|go|make|put|add|remind|read|call|send|switch|find|give|is|are|can|do)\b",
                  re.IGNORECASE)


class Context:
    def __init__(self):
        self.text = None   # the phrase JARVIS last acted on ("volume up", "close firefox")
        self.value = None  # the name or number in it, if any ("firefox", "40")
        self.at = 0.0
        self.choice = None  # (options, picked) from the last "X, or Y?"

    def done(self, text, value=None):
        if value is None:
            m = NAMED.match(text) or ADDED.match(text)
            value = m and m.group(1)
        self.text, self.value, self.at = text, value, time.time()

    def forget(self):
        """Talk that wasn't an action: "keep going" after it isn't about the volume from before."""
        self.text = None

    def chose(self, options, picked):
        self.choice = (list(options), picked)

    def follow_up(self, said):
        """What a follow-up means, as a phrase JARVIS can handle, or None."""
        if not self.text or time.time() - self.at > FRESH:
            return None
        said = said.strip().rstrip(".!?")
        if OTHER.match(said) and self.choice and self.choice[1].lower() == self.text.lower():
            options, picked = self.choice  # (only while the pick is still the last thing done)
            others = [o for o in options if o.lower() != picked.lower()]
            return others[0] if len(others) == 1 else None
        if MORE.match(said):
            return self.text if STEP.search(self.text) and not ONE_OFF.match(self.text) else None
        if LESS.match(said):
            if ONE_OFF.match(self.text):
                return None
            swapped = _swap(self.text)
            return swapped if swapped != self.text else None
        m = VALUE.match(said)
        n = m and _number(m.group(1))
        if n is not None and self.value and self.value.isdigit():
            spot = r"\b%s\b" % re.escape(self.value)
            if len(re.findall(spot, self.text)) != 1:
                return None  # "make it seventy" yes; "set volume to 40 in 40 seconds": which 40?
            if n > 100 and re.search(r"volume|brightness|percent|%", self.text, re.IGNORECASE):
                return None  # not "volume to 400"
            return re.sub(spot, str(n), self.text, count=1)
        m = TOO.match(said)
        if m and self.value and not self.value.isdigit():
            new = (m.group(1) or m.group(2)).strip()
            verb = self.text.split()[0]
            if new.lower().startswith(verb.lower() + " "):
                new = new[len(verb):].strip()  # "close chrome", "and close firefox too": firefox
            elif VERB.match(new):
                return None  # "now open youtube" after "open chrome": its own request
            # a name, not a sentence ("me too", "i love you too" aren't "close me")
            if new.lower() != self.value.lower() and len(new.split()) <= 3 and not NOT_A_NAME.search(new):
                return re.sub(re.escape(self.value), lambda _: new, self.text, count=1, flags=re.IGNORECASE)
        return None


def sounds_like_follow_up(said):
    """"a bit more", "too much", "the other one": only meaningful right after an action. (Not numbers:
    "make it 40 percent" on its own still means the volume.)"""
    said = said.strip().rstrip(".!?")
    return any(p.match(said) for p in (MORE, LESS, OTHER))


def _swap(text):
    for swap in (SWAP, SWAP_WEAK):  # "scroll down in chrome": down, not in
        pairs = dict(swap + [(b, a) for a, b in swap])
        new = re.sub(r"\b(%s)\b" % "|".join(pairs), lambda m: pairs[m.group(1).lower()], text, count=1,
                     flags=re.IGNORECASE)
        if new != text:
            return new
    return text


def _number(said):
    """"70" or "seventy" or "twenty five" -> 70 / 25, else None."""
    if said.isdigit():
        return int(said)
    words = re.split(r"[ -]", said.lower())
    if not all(w in NUMBERS for w in words) or len(words) > 2:
        return None
    if len(words) == 2 and not (NUMBERS[words[0]] >= 20 and NUMBERS[words[0]] % 10 == 0 and NUMBERS[words[1]] < 10):
        return None
    return sum(NUMBERS[w] for w in words)
