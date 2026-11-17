"""Names Parakeet misheard, put back by how they sound: "what do you know about Celantes" -> "... about Stellantis".

Parakeet takes no hints, so names it never learned come out as near-sounding words. Each heard word, or run of up to
four, is turned into sounds (espeak's IPA, as Kokoro uses) and compared with every name in the mind map (people,
projects, chat groups). Only a clear winner is swapped in: words that aren't English ("Celantes", "Bistil"),
loosely right where a name goes ("about ___", "from ___", "run ___") and closely elsewhere; ordinary words only for a
name they share a word with ("Plant Intelligent System"). Names it already knows, apps included ("VS Code"), are never
touched. On your 30 name recordings: 9 names right before, 18 after; nothing wrong on the 50 everyday commands; of
4,071 different lines in jarvis.log it would change 31, nearly all rightly. Each swap is a "[names] ..." log line.
"""
import re
import threading
import time

CUTOFF = 0.55  # how alike the sounds must be (1 = the same), where a name goes ("about ___")
OUTSIDE = 0.8  # elsewhere: odd words in ordinary talk come close to some name far more often
PLAIN_IN_SLOT = 0.65  # ordinary English words where a name goes, sharing a word with it ("run study vary")
MARGIN = 0.05  # and by how much the best name must beat the next one
REFRESH = 1800  # seconds: the mind map is rebuilt every 30 min (jarvis-brain.timer)
WORDLIST = "/usr/share/dict/words"
COMMON = set("what whats what's the a an is on in my me with for from of to about who open run call note say said last "
             "message chat project group space new next do you know catch up going how and".split())
SLOT = re.compile(r"(?:about|with|from|run|call|message|who is|note for)$")  # a name comes next ("the"/"my" may sit between)
HUBS = {"Apps", "People", "Projects", "Learning", "Chat groups", "Meetings", "Notepad notes", "Biztel work", "Personal",
        "What you told me", "yourself", "notes", "platform"}
VOWELS = set("aeiouæɑɒɔəɛɜɪʊʌɐyøœɘɵɤɚɝ")
ALIKE = [set(g) for g in ("pb", "td", "kgɡ", "fvθð", "szʃʒ", "mn", "lrɹɾ", "wv", "jɪi")]


def _key(text):
    return re.sub(r"[\W_]", "", text.lower())


def _cost(x, y):
    if x == y:
        return 0
    if x in VOWELS and y in VOWELS:
        return 0.3
    return 0.5 if any(x in g and y in g for g in ALIKE) else 1


def alike(a, b):
    """1 for the same sounds, down to 0: an edit distance where vowel swaps and voiced pairs (t/d, s/z) cost less."""
    row = [float(i) for i in range(len(b) + 1)]
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + _cost(x, y))
    return 1 - row[-1] / max(len(a), len(b), 1)


class Names:
    def __init__(self, words=None, sounds=None):
        """words: {kind: names} (default: the mind map's, re-read every REFRESH); sounds: text -> IPA (default espeak)."""
        self._words, self._say = words, sounds
        self._lock = threading.Lock()
        self._built = 0.0
        self.names, self.known, self._ipa = [], set(), {}
        try:
            with open(WORDLIST) as f:
                self.english = {w.strip().lower() for w in f if "'" not in w}
        except OSError:
            self.english = set()  # shortcut: no word list = only slot words guard ordinary words; apt install wamerican

    def _build(self):
        if self._say is None:
            from voice.kokoro import _espeak
            self._say = _espeak()
        if self._words is not None:
            words = self._words
        else:
            from core.mind import Mind
            words = Mind().words()
        names = {n for k in ("people", "projects", "groups") for n in words.get(k, ())  # (playlists: plain English)
                 if n not in HUBS and not re.search(r"\d|—|:", n)}
        names |= {n.split()[0].title() for n in words.get("people", ()) if " " in n.strip()}  # "Rahul" for "Rahul K"
        one = {}
        for n in sorted(names, key=lambda n: (n.islower(), n)):  # one spelling per name: the capitalised one
            if " " in n.strip() or n.lower() not in self.english:  # "Stack", "Cloud" are just words
                one.setdefault(_key(n), n)
        self.names = sorted(set(one.values()) | {"Jarvis"})
        self.known = {_key(n) for k in words for n in words[k]} | {_key(n) for n in self.names}
        self._ipa = {n: self.ipa(n) for n in self.names}
        self._built = time.time()

    def ipa(self, text):
        return re.sub(r"[ˈˌː\s]", "", "".join(self._say(w) for w in re.sub(r"[-_()·,.]", " ", text).split()))

    def fix(self, text):
        """The text with misheard names put back, each swap logged."""
        if not text:
            return text
        with self._lock:
            if time.time() - self._built > REFRESH:
                try:
                    self._build()
                except Exception as e:  # never lose what was heard to the fixer
                    print("[names] couldn't read the names: %s" % e)
                    self._built = time.time()
            if not self.names:
                return text
            return self._fix(text)

    def _fix(self, text):
        toks = text.split()
        bare = [re.sub(r"[^\w'-]", "", t).lower() for t in toks]
        found = []
        for i in range(len(toks)):
            k = i - 1
            while k >= 0 and bare[k] in ("the", "my"):
                k -= 1
            in_slot = k >= 0 and bool(SLOT.match(bare[k]) or k > 0 and SLOT.match(" ".join(bare[k - 1:k + 1])))
            for n in range(1, 5):
                span = bare[i:i + n]
                if len(span) < n or span[0] in COMMON or span[-1] in COMMON or not all(w.isalpha() or
                                                                                       "'" in w for w in span):
                    continue
                english = [w.split("'")[0] in self.english for w in span]  # "i'm", "don't" are English too
                plain = all(english)
                if not in_slot and any(english) and not plain:
                    continue
                if any(_key(" ".join(bare[a:b])) in self.known for a in range(max(0, i - 3), i + n)
                       for b in range(max(a, i) + 1, min(len(bare), i + n + 3) + 1)):
                    continue  # a known name is already in it or runs into it
                sound = self.ipa(" ".join(span))
                if len(sound) < 4:
                    continue
                # Ordinary words only become a name they share a word with: "study vary" -> Study Buddy, "how sleep
                # annotations" -> hub sleeve annotations, never "from suffering" -> O-ring or "call chicken" -> Chetakk
                need = (PLAIN_IN_SLOT if in_slot else OUTSIDE) if plain else CUTOFF if in_slot else OUTSIDE
                ranked = sorted(((alike(sound, s), m) for m, s in self._ipa.items()  # (too long or short can't reach it)
                                 if abs(len(sound) - len(s)) <= (1 - CUTOFF) * max(len(sound), len(s))
                                 and (not plain or set(span) & set(m.lower().split()))), reverse=True)
                if not ranked:
                    continue
                (best, name), second = ranked[0], ranked[1][0] if len(ranked) > 1 else 0
                if best >= need and best - second >= MARGIN:
                    found.append((best, i, n, name))
        out, used = list(toks), set()
        for _, i, n, name in sorted(found, reverse=True):
            if used & set(range(i, i + n)):
                continue
            used |= set(range(i, i + n))
            print("[names] %s -> %s" % (" ".join(toks[i:i + n]).strip(".,!?"), name))
            out[i] = name + re.sub(r"[\w'-]+", "", toks[i + n - 1])  # keeps the "?" or "." after it
            out[i + 1:i + n] = [None] * (n - 1)
        return " ".join(t for t in out if t is not None)


if __name__ == "__main__":
    fixer = Names({"people": ["Rahul K"], "projects": ["Stellantis", "stellantis", "Avtec", "Study Buddy"],
                   "apps": ["VS Code"], "learning": ["Stack"]})
    assert fixer.fix("What do you know about Celantes?") == "What do you know about Stellantis?", fixer.fix("What do you know about Celantes?")
    assert fixer.fix("Run study vary.") == "Run Study Buddy."
    assert fixer.fix("What's the weather today?") == "What's the weather today?"
    assert fixer.fix("Open VS Code.") == "Open VS Code."
    assert fixer.fix("Jarvis, are you listening?") == "Jarvis, are you listening?"
    assert fixer.fix("Because we are, but I'm immune.") == "Because we are, but I'm immune."
    assert fixer.fix("What's going on with Rahul?") == "What's going on with Rahul?"
    assert fixer.fix("What did Rahool say on chat?") == "What did Rahul say on chat?", fixer.fix("What did Rahool say on chat?")
    assert "Stack" not in fixer.names and "Rahul" in fixer.names
    print("ok")
