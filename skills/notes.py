"""Personal memory: what JARVIS knows about you and what it's been asked to remember.

- About you (memory.json "preferences"): "I prefer battery life", "my favourite editor is vim". Said in passing,
  JARVIS asks before saving (Claude can offer one too); "remember that I ..." saves at once. A new one on the
  same topic replaces the old ("I prefer light mode" after "I prefer dark mode").
- Notes (notes.json): "remember that ...", "what do you remember about ...", "forget ...". Project notes live
  here too (skills/projects.py).
- Secrets (the GNOME Keyring, system/keyring.py): "remember my locker code is 4512" goes there, not into notes;
  "what's my locker code" shows it for 30 seconds.
- Private: "remember privately that ...", "make that private". Private things and secrets never reach Claude:
  not in the context it's given, and the request and the reply are left out of the lines it sees next time.
- "what do you know about me": a card of all of it with Forget buttons; "forget number 3", "change number 2 to
  ...", "forget this conversation" (Claude's short-term memory).
Recall goes by meaning (the meaning matcher's MiniLM, a few ms per question); the few things that matter for
a question are passed on to Claude with it.
"""
import os
import re
import threading
import time

from core.store import load_json, save_json
from skills.base import Skill
from skills.cards import ORDINALS
from skills.helpers import HOME, words
from system import keyring

NOTES_FILE = os.path.join(HOME, ".local/share/jarvis/notes.json")
RECALL = 0.40  # meaning score for "what do you remember about X" (unrelated things score under 0.25)
ANSWER = 0.45  # ... for answering "where did I park" from a note without asking Claude
RELEVANT = 0.28  # ... for passing a note on to Claude with a question ("what should I eat" ~ "I'm vegetarian")
SURE = 0.60  # a secret found by meaning alone, without a shared word
SAME = 0.80  # a new preference this close to an old one replaces it ("I like jazz" / "I hate jazz")
SHOW_SECRET = 30  # seconds a secret stays on the card

_NTH = r"(%s|\d+)" % "|".join(ORDINALS)
_ITEM = r"(?:number |item |memory |note |the )?"
# "remember that I ..." is about you, not a note ("I'm meeting Raj" is a note: no -ing)
ABOUT_YOU = re.compile(
    r"^(?:i(?:'d| would)? (?:\w+ )?(?:prefer|rather|like|love|enjoy|hate|dislike|stand|want|use|drink|eat|wake|"
    r"sleep|work)\b|i(?:'m| am) (?:a |an )?(?!\w+ing\b)\w+(?: \w+)?$|i(?:'m| am) allergic|"
    r"my (?:favou?rite|preferred|usual|go[- ]to) )", re.IGNORECASE)
# Said in passing (no "remember"): asked about before saving. Never "I like it/this/you", complaints or questions.
CASUAL = (r"i (?:really |usually |generally |much |always )?(?:prefer|like|love|hate|dislike|don'?t like|can'?t stand) "
          r"(?!(?:you|it|this|that|these|those|them|him|her|when|how|what|the way)\b).+|"
          r"i'?d (?:much )?rather (?!not\b).+|my (?:favou?rite|preferred) .+? (?:is|are) .+")
PERSONAL = re.compile(r"^(?:where|when|what|which|who|how)\b.*\b(?:my|i|me)\b", re.IGNORECASE)
ASKING = re.compile(r"\b(?:what|which|how|why|should i|can you|could you|would you|recommend|suggest|any ideas|"
                    r"help me|tell me)\b", re.IGNORECASE)
SECRET = re.compile(r"\b(?:passwords?|passcodes?|passphrase|pins?|otp|cvv|ssn|aadhaar|pan|api key|token|secret|"
                    r"combination|codes?|account number|card number)\b", re.IGNORECASE)
HINT = re.compile(r"^(?:on|in|at|under|inside|behind|with|written|somewhere|the same|same as|saved|stored|kept)\b",
                  re.IGNORECASE)  # "the router password is on the fridge": where it is, not the password
KEEP_PRIVATE = re.compile(r",? (?:and )?(?:keep (?:it|that|this) (?:private|secret|to yourself|between us)|"
                          r"privately|(?:but )?don'?t tell (?:claude|anyone))$", re.IGNORECASE)
STOP = {"i", "me", "you", "your", "is", "are", "was", "to", "in", "on", "at", "it", "that", "this", "what", "where",
        "when", "s", "do", "did", "and", "for", "about", "with", "remember", "know",
        "code", "password", "pin", "number", "secret"}  # every secret has these: "zip code" isn't "locker code"


def load_notes():
    return load_json(NOTES_FILE, [])


def save_notes(notes):
    save_json(NOTES_FILE, notes, indent=1)


def _note_line(n):
    return "[%s] %s" % (n["project"], n["text"]) if n.get("project") else n["text"]


_YOU = {"i": "you", "i'm": "you're", "i've": "you've", "i'd": "you'd", "i'll": "you'll", "my": "your",
        "me": "you", "mine": "yours", "myself": "yourself"}


def _you(text):
    """Your words, said back to you: "I prefer my laptop quiet" -> "you prefer your laptop quiet"."""
    text = re.sub(r"\bi am\b", "you are", text, flags=re.IGNORECASE)
    text = re.sub(r"\bi was\b", "you were", text, flags=re.IGNORECASE)
    return re.sub(r"\b(?:i'm|i've|i'd|i'll|i|my|me|mine|myself)\b", lambda m: _YOU[m.group(0).lower()], text,
                  flags=re.IGNORECASE)


def _topic(text):
    """"my favourite editor is vim" -> "favourite editor" (a later one replaces it); else None."""
    m = re.match(r"my (favou?rite|preferred|usual|go[- ]to) (.+?) (?:is|are)\b", text, re.IGNORECASE)
    return "%s %s" % (m.group(1).lower().replace("favorite", "favourite"), m.group(2).lower()) if m else None


def _secret(note):
    """("locker code", "4512") when `note` gives a password, PIN or code itself; None otherwise."""
    m = re.match(r"^(?:my |the |our )?(.+?)(?: is| are| =|:) (.+)$", note, re.IGNORECASE)
    if not m or not SECRET.search(m.group(1)) or HINT.match(m.group(2)):
        return None
    return m.group(1).strip().lower(), m.group(2).strip()


class Notes(Skill):
    group = "memory"
    does = "What you told JARVIS to remember: notes, preferences, secrets (in your keyring)"
    phrases = ("remember that <x>", "what do you remember about <x>", "forget <x>", "what do you know about me (a card of everything kept, with Forget buttons)",
               "forget number <n>", "forget this conversation")
    gives = "speech and a card"
    permission = "confirm"
    asks = "before forgetting, and before keeping something said in passing"
    checks = "saved atomically and read back; secrets and private notes never reach Claude"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self._vecs = {}  # text -> meaning vector, so each note is embedded once
        self.shown = []  # the items on the last "what do you know about me" card, by number
        self.last = None  # the item just saved or recalled, for "make that private"

    def patterns(self):
        return [
            (r"what do you (?:know|remember) about me|what have you (?:learned|remembered) about me|"
             r"(?:show|open|list)(?: me)? (?:your|my) memor(?:y|ies)|what'?s in your memory|"
             r"tell me what (?:you|do you) (?:know|remember) about me|what have you (?:saved|stored|kept) about me|"
             r"everything you (?:know|remember)(?: about me)?",
             lambda m: self.about_me()),
            (r"(?:forget|clear|reset|wipe|erase) (?:this|our|the) (?:conversation|chat)(?: history)?|"
             r"forget what (?:we|i) (?:just )?(?:talked|said|spoke) about|"
             r"start (?:a )?(?:fresh|new) conversation", lambda m: self.forget_conversation()),
            (r"forget %s%s(?: one)?" % (_ITEM, _NTH), self.forget_nth),
            (r"(?:change|update|edit|correct) %s%s (?:to|so it says) (.+)" % (_ITEM, _NTH), self.edit_nth),
            (r"(?:make|keep|mark) (?:that|it|this|the last one|%s%s)(?: one)? (private|secret|public|not private)|"
             r"(?:you can )?share (?:that|it|this|%s%s) with claude" % (_ITEM, _NTH, _ITEM, _NTH), self.set_private),
            (r"(?:what(?:'s| is| was)|tell me|show me|give me|remind me of) (?:my |the |our )(.+?(?:password|passcode|"
             r"passphrase|pin|code|otp|combination|secret))", lambda m: self.secret(m.group(1))),
            (r"(?:list |show |what are )?(?:my |your )?secrets|what secrets do you (?:have|keep)",
             lambda m: self.list_secrets()),
            (r"(?:remember|note|make a note) (?:privately|secretly|in private)(?: that)? (.+)|"
             r"(?:privately|secretly) (?:remember|note)(?: that)? (.+)|make a private note(?: that)? (.+)",
             lambda m: self.remember(m, private=True)),
            (r"(?:remember|note|make a note)(?: that)? (.+)", self.remember),
            (r"what did i (?:ask|tell) you to remember|what do you remember|(?:list |read )?my notes",
             lambda m: self.list_notes()),
            (r"(?:what do you (?:know|remember) about|do you remember) (.+)", self.recall),
            (r"forget (?:about |the note )?(?!.*\breminders?$)(.+)", self.forget),  # "forget the X reminder": todos
            (CASUAL, self.casual),
        ]

    # --- saving ---

    def remember(self, m, private=False):
        note = next(g for g in m.groups() if g).strip()
        if re.match(r"^to\b", note):
            return False  # "remember to call mom at 5" is a reminder, not a note
        if KEEP_PRIVATE.search(note):
            note, private = KEEP_PRIVATE.sub("", note).strip(), True
        secret = _secret(note)
        if secret and keyring.available():
            return self._lock(*secret)
        private = private or bool(SECRET.search(note))  # a hint about a password is still nobody else's business
        if private:
            self.j.keep_private()
        if ABOUT_YOU.match(note):
            return self._save_pref(note, private)
        notes = load_notes()
        entry = dict({"text": note, "at": time.time()}, **({"private": True} if private else {}))
        notes.append(entry)
        save_notes(notes)
        item = self.last = self._item("note", entry)
        self.j.last_undo = lambda: self._drop(item, quiet=True) or self.say("Okay, I've forgotten that.")
        if secret:
            self.say("Noted, as a private note. Your keyring isn't reachable, so it isn't encrypted.")
        else:
            self.say("Noted, and kept private." if private else ["Got it, I'll remember that.", "Okay, noted."])

    def _lock(self, name, value):
        self.j.keep_private()
        if not keyring.store(name, value):
            self.say("Sorry, I couldn't put that in your keyring.")
            return
        self.last = self._item("secret", name)
        self.j.last_undo = lambda: keyring.delete(name) and self.say("Okay, it's out of your keyring.")
        self.say("That's a secret, so I've locked your %s in your keyring. It never goes to Claude." % name)

    def _save_pref(self, text, private=False):
        prefs = self.j.memory.preferences()
        topic = _topic(text)
        old = next((p for p in prefs if topic and p.get("topic") == topic), None) or self._closest(text, prefs)
        if old and old["value"].lower() == text.lower():
            self.say("I already know that.")
            return
        new = {"topic": topic, "value": text, "at": time.time(), "private": private or bool(old and old.get("private"))}
        before = list(prefs)
        if old:
            prefs[prefs.index(old)] = new
        else:
            prefs.append(new)
        self.j.memory.set_preferences(prefs)
        self.last = self._item("pref", new)
        self.j.last_undo = lambda: self.j.memory.set_preferences(before) or self.say("Okay, back to how it was.")
        if old:
            self.say("Got it, I've updated that: %s." % _you(text))
        else:
            self.say(["Got it, I'll remember that %s." % _you(text), "Okay, noted: %s." % _you(text)])

    def _closest(self, text, prefs):
        vecs = self._vectors([text] + [p["value"] for p in prefs]) if prefs else None
        if vecs is None:
            return None
        scores = vecs[1:] @ vecs[0]
        best = int(scores.argmax())
        return prefs[best] if scores[best] >= SAME else None

    def casual(self, m):
        """"I prefer battery life over performance", said in passing: ask before keeping it."""
        said = m.group(0).strip()
        if self.j.from_claude or ASKING.search(said):
            return False  # a question with a preference in it: Claude answers (and may offer to remember it)
        if any(p["value"].lower() == said.lower() for p in self.j.memory.preferences()):
            self.say("I know.")
            return
        if self.j.ask(["Want me to remember that %s?" % _you(said), "Should I remember that %s?" % _you(said)]):
            self._save_pref(said)
        else:
            self.say(["Okay, I won't.", "Sure, not saved."])

    def offer(self, item):
        """Claude noticed something lasting about you ("remember": {"text", "kind"}): keep it if you say yes."""
        text = str((item or {}).get("text") or "").strip().rstrip(".") if isinstance(item, dict) else ""
        if not text or SECRET.search(text):
            return  # secrets never come from Claude
        known = [i["text"] for i in self._items(projects=True)]
        if known and self._best(text, known)[1] >= 0.85:
            return
        if not self.j.ask(["Should I remember that %s?" % _you(text), "Want me to remember that %s?" % _you(text)]):
            self.say(["Okay, I won't.", "Sure, not saved."])
            return
        if item.get("kind") == "note" and not ABOUT_YOU.match(text):
            notes = load_notes()
            notes.append({"text": text, "at": time.time()})
            save_notes(notes)
            self.say(["Got it, I'll remember that.", "Okay, noted."])
        else:
            self._save_pref(text)

    # --- recalling ---

    def list_notes(self):
        notes = load_notes()
        if not notes:
            self.say("You haven't asked me to remember anything yet.")
            return
        if any(n.get("private") for n in notes):
            self.j.keep_private()
        self.j.chat("sys", "notes:\n" + "\n".join("- " + _note_line(n) for n in notes))
        self.j.show({"title": "Your notes", "text": "\n".join("- " + _note_line(n) for n in notes),
                     "private": any(n.get("private") for n in notes)})
        public = [n for n in notes if not n.get("private")]
        recent = public[-3:]
        self.say("You've asked me to remember %d thing%s.%s" % (
            len(notes), "" if len(notes) == 1 else "s", " %s: %s." % (
                "The latest" if len(public) > 3 else "They are", "; ".join(_you(n["text"]) for n in reversed(recent)))
            if recent else " They're on your screen."))

    def recall(self, m):
        hits = self._find(m.group(1), self._items(projects=True, secrets=True))
        if not hits:
            return False  # maybe a general question: let Claude have it
        top = hits[0]
        if top["kind"] == "secret":
            return self.secret(top["text"])
        hits = [h for h in hits[:2] if h["kind"] != "secret" and h["score"] >= top["score"] - 0.1]
        self.last = hits[0]
        if any(h["private"] for h in hits):
            self.j.keep_private()
        self.say("You told me %s." % "; and ".join(_you(h["text"]) for h in hits))

    def answer(self, text):
        """A question about your own things that a note answers ("where did I park" -> "my car is on level 2"):
        said from the note, before asking Claude. False when no note is close enough."""
        if not PERSONAL.search(text):
            return False
        items = self._items(projects=True)
        best, score = self._best(text, [i["text"] for i in items]) if items else (None, 0.0)
        if score < ANSWER:
            return False
        item = self.last = next(i for i in items if i["text"] == best)
        if item["private"]:
            self.j.keep_private()
        self.say("You told me %s." % _you(item["text"]))
        return True

    def secret(self, name):
        """Show one of your keyring secrets on the card for a little while (never spoken or sent anywhere)."""
        names = keyring.names()
        hit = [h for h in self._find(name, [self._item("secret", n) for n in names])
               if h["overlap"] or h["score"] >= SURE]  # "zip code" isn't the "locker code"
        if not hit:
            return False  # "what's my zip code": not one of yours, maybe Claude knows
        name = hit[0]["text"]
        value = keyring.lookup(name)
        if value is None:
            return False
        self.j.keep_private()
        self.last = hit[0]
        if not self.j.ui:
            self.say("Your %s is %s." % (name, value))
            return
        self.j.show({"id": "secret", "title": name.capitalize(), "text": "`%s`\n\n*Hidden again in %d seconds.*" % (
            value, SHOW_SECRET), "private": True})
        self.say("Your %s is on your screen for %d seconds." % (name, SHOW_SECRET))
        threading.Timer(SHOW_SECRET, self._hide_secret, [name]).start()

    def _hide_secret(self, name):
        card = self.j.ui.current_card() if self.j.ui else None
        if card and card.get("id") == "secret":  # replaced in place, so it's gone from the card history too
            self.j.ui.show_card({"id": "secret", "title": name.capitalize(),
                                 "text": "Hidden. Say “what's my %s” to see it again." % name})
            self.j.ui.close_card()

    def list_secrets(self):
        if not keyring.available():
            self.say("I can't reach your keyring, so there are no secrets kept there.")
            return
        names = keyring.names()
        self.j.keep_private()
        if not names:
            self.say("There are no secrets in your keyring yet. Say something like “remember my locker code "
                     "is 4512” and I'll lock it away.")
            return
        self.say("You've got %d secret%s in your keyring: %s." % (len(names), "" if len(names) == 1 else "s",
                                                                  ", ".join(names)))

    def about_me(self, speak=True):
        """A card of everything JARVIS keeps about you, numbered, with Forget buttons for the first few."""
        items = self.shown = self._items(secrets=True)
        projects = {}
        for n in load_notes():
            if n.get("project"):
                projects[n["project"]] = projects.get(n["project"], 0) + 1
        if not items and not projects:
            self.say("I don't know much about you yet. Tell me what you prefer, or say “remember that ...”.")
            return
        lines, group, labels = [], None, []
        heads = {"pref": "**About you**", "note": "**Notes**", "secret": "**Locked in your keyring** (values hidden)"}
        for n, item in enumerate(items, 1):
            if item["kind"] != group:
                group = item["kind"]
                lines.append(heads[group])
            text = _you(item["text"]) if group == "pref" else item["text"]
            text = text[:1].upper() + text[1:]
            labels.append(text if len(text) <= 24 else text[:23] + "\u2026")
            lines.append("%d. %s%s" % (n, text, " *(private)*" if item["private"] and group != "secret" else ""))
        if projects:
            lines.append("**Project notes:** %s (say “notes for <project>”)" % ", ".join(
                "%s %d" % kv for kv in sorted(projects.items())))
        lines += ["**This conversation:** Claude keeps it until %d quiet minutes; “forget this conversation” "
                  "clears it now." % round(self.j.claude.idle / 60), "---",
                  "Say “forget number 2”, “change number 1 to ...”, “make number 3 "
                  "private”. Private ones and secrets never go to Claude."]
        self.j.show({"id": "memory", "title": "What I know about you", "text": "\n".join(lines),
                     "private": any(i["private"] for i in items),
                     "choices": [{"label": "Forget: %s" % label, "do": "forget number %d" % n}
                                 for n, label in enumerate(labels[:6], 1)]})
        if not speak:
            return
        kinds = [(sum(i["kind"] == k for i in items), names) for k, names in (
            ("pref", ("thing about you", "things about you")), ("note", ("note", "notes")),
            ("secret", ("secret", "secrets")))]
        said = ["%d %s" % (c, names[c != 1]) for c, names in kinds if c]
        if said:
            self.say("I know %s. They're on your screen." % (" and ".join(said) if len(said) < 3 else
                                                              "%s, %s and %s" % tuple(said)))
        else:
            self.say("Only project notes so far. They're on your screen.")

    def for_claude(self, text):
        """The few things you've told JARVIS that matter for `text`, as a line for Claude ("" if none).
        Private ones and secrets are never in it."""
        items = [i for i in self._items(projects=True) if not i["private"]]
        if not items:
            return ""
        vecs = self._vectors([text] + [i["text"] for i in items])
        if vecs is None:
            return ""
        scores = vecs[1:] @ vecs[0]
        hits = [items[i] for i in scores.argsort()[::-1][:3] if scores[i] >= RELEVANT]
        return "What %s told JARVIS before that may matter here: %s" % (
            self.j.settings.get("user_name") or "the user", "; ".join(
                "\"%s\"" % h["text"] for h in hits)) if hits else ""

    # --- changing and forgetting ---

    def forget(self, m):
        what = m.group(1).strip()
        notes = load_notes()
        if re.match(r"^(?:everything|all(?: (?:my|the))? notes|all of it)$", what, re.IGNORECASE):
            if notes and self.j.ask("Forget all %d notes?" % len(notes)):
                save_notes([])
                self.say("Done, all forgotten.")
            return
        hits = self._find(what, self._items(projects=True, secrets=True))
        if not hits:
            return False
        if hits[0]["private"]:
            self.j.keep_private()
        if self.j.ask('Forget "%s"?' % (("your %s secret" % hits[0]["text"]) if hits[0]["kind"] == "secret"
                                        else hits[0]["text"])):
            self._drop(hits[0])
            self.say("Forgotten.")

    def forget_nth(self, m):
        item = self._nth(m.group(1))
        if not item:
            return False
        if item["private"]:
            self.j.keep_private()
        self._drop(item)
        self.say(["Forgotten. Say undo that if you didn't mean it.", "Done, it's gone."])

    def edit_nth(self, m):
        item, text = self._nth(m.group(1)), m.group(2).strip()
        if not item:
            return False
        if item["kind"] == "secret":
            name = item["text"]
            old = keyring.lookup(name)
            self.j.keep_private()
            keyring.store(name, text)
            self.j.last_undo = lambda: keyring.store(name, old) and self.say("Okay, it's back to the old one.")
        else:
            if item["private"]:
                self.j.keep_private()
            self._replace(item, text)
        self.say(["Updated.", "Changed."])
        self._refresh_card()

    def set_private(self, m):
        groups = [g for g in m.groups() if g]
        nth = next((g for g in groups if g.lower() in ORDINALS or g.isdigit()), None)
        item = self._nth(nth) if nth else self.last
        if not item:
            return False
        private = not re.search(r"public|not private|share", m.group(0), re.IGNORECASE)
        if item["kind"] == "secret":
            self.say("Secrets are always private.")
            return
        self.j.keep_private()
        self._replace(item, item["text"], private=private)
        self.say("Okay, that stays between us. It won't go to Claude." if private else
                 "Okay, Claude can see that one now.")
        self._refresh_card()

    def forget_conversation(self):
        """Short-term memory: Claude's current conversation and what it'd be told next; notes stay."""
        self.j.claude.new_session()
        self.j.recent.clear()
        self.j.talk.clear()
        self.say("Okay, I've forgotten this conversation. Your notes and preferences are still here.")

    def _nth(self, word):
        items = self.shown or self._items(secrets=True)
        word = word.lower()
        n = ORDINALS.get(word) or (int(word) if word.isdigit() else 0)
        n = len(items) if n == -1 else n
        return items[n - 1] if 0 < n <= len(items) else None

    def _drop(self, item, quiet=False):
        """Forget one item; "undo that" brings it back."""
        if item["kind"] == "secret":
            value = keyring.lookup(item["text"])
            keyring.delete(item["text"])
            restore = lambda: value is not None and keyring.store(item["text"], value)
        elif item["kind"] == "pref":
            before = self.j.memory.preferences()
            self.j.memory.set_preferences([p for p in before if p != item["ref"]])
            restore = lambda: self.j.memory.set_preferences(before)
        else:
            before = load_notes()
            save_notes([n for n in before if n != item["ref"]])
            restore = lambda: save_notes(before)
        self.shown = [i for i in self.shown if i["ref"] != item["ref"]]
        if not quiet:
            self.j.last_undo = lambda: restore() or self.say("Okay, I remember it again.")
        self._refresh_card()

    def _replace(self, item, text, private=None):
        private = item["private"] if private is None else private
        if item["kind"] == "pref":
            before = self.j.memory.preferences()
            new = dict(item["ref"], value=text, topic=_topic(text), private=private)
            self.j.memory.set_preferences([new if p == item["ref"] else p for p in before])
            restore = lambda: self.j.memory.set_preferences(before)
        else:
            before = load_notes()
            new = dict(item["ref"], text=text)
            new.pop("private", None)
            if private:
                new["private"] = True
            save_notes([new if n == item["ref"] else n for n in before])
            restore = lambda: save_notes(before)
        self.last = self._item(item["kind"], new)
        self.shown = [self.last if i["ref"] == item["ref"] else i for i in self.shown]
        self.j.last_undo = lambda: restore() or self.say("Okay, back to how it was.")

    def _refresh_card(self):
        card = self.j.ui.current_card() if self.j.ui and self.j.ui.card_visible() else None
        if card and card.get("id") == "memory":
            self.about_me(speak=False)

    # --- items and meaning ---

    @staticmethod
    def _item(kind, ref):
        if kind == "secret":
            return {"kind": kind, "text": ref, "private": True, "ref": ref}
        text = ref["value"] if kind == "pref" else ref["text"]
        return {"kind": kind, "text": text, "private": bool(ref.get("private")), "ref": ref}

    def _items(self, projects=False, secrets=False):
        """Everything kept about you, in card order: preferences, notes, then (asked for) keyring secrets."""
        out = [self._item("pref", p) for p in self.j.memory.preferences()]
        out += [self._item("note", n) for n in load_notes() if projects or not n.get("project")]
        if secrets:
            out += [self._item("secret", n) for n in keyring.names()]
        return out

    def _vectors(self, texts):
        """Meaning vectors for `texts` (cached per text), or None when the meaning matcher is off."""
        intents = self.j.intents
        if not intents:
            return None
        missing = [t for t in dict.fromkeys(texts) if t not in self._vecs]
        if missing:
            for t, v in zip(missing, intents.embed(missing)):
                self._vecs[t] = v
        import numpy as np
        return np.array([self._vecs[t] for t in texts])

    def _best(self, text, texts):
        vecs = self._vectors([text] + texts)
        if vecs is None:
            return None, 0.0
        scores = vecs[1:] @ vecs[0]
        return texts[int(scores.argmax())], float(scores.max())

    def _find(self, query, items):
        """Items about `query`, best first, each with its "score": by shared words, and by meaning
        ("where did I park" finds "my car is on level 2")."""
        if not items:
            return []
        wanted = set(words(query)) - STOP
        vecs = self._vectors([query] + [i["text"] for i in items])
        out = []
        for n, item in enumerate(items):
            overlap = len(wanted & set(words(item["text"])))
            meaning = float(vecs[n + 1] @ vecs[0]) if vecs is not None else 0.0
            if overlap or meaning >= RECALL:
                out.append(dict(item, score=meaning + 0.15 * overlap, overlap=overlap))
        return sorted(out, key=lambda i: -i["score"])
