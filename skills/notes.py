"""Notes: "remember that ...", "what do you remember", "forget ...". Project notes live here too."""
import json
import os
import re
import time

from skills.base import Skill
from skills.helpers import HOME, words

NOTES_FILE = os.path.join(HOME, ".local/share/jarvis/notes.json")


def load_notes():
    try:
        with open(NOTES_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_notes(notes):
    os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
    with open(NOTES_FILE, "w") as f:
        json.dump(notes, f, indent=1)


def _note_line(n):
    return "[%s] %s" % (n["project"], n["text"]) if n.get("project") else n["text"]


class Notes(Skill):
    def patterns(self):
        return [
            (r"(?:remember|note|make a note)(?: that)? (.+)", self.remember),
            (r"what did i (?:ask|tell) you to remember|what do you remember|(?:list |read )?my notes",
             lambda m: self.list_notes()),
            (r"(?:what do you (?:know|remember) about|do you remember) (.+)", self.recall),
            (r"forget (?:about |the note )?(?!.*\breminders?$)(.+)", self.forget),  # "forget the X reminder": todos
        ]

    def remember(self, m):
        note = m.group(1).strip()
        if re.match(r"^to\b", note):
            return False  # "remember to call mom at 5" is a reminder, not a note
        notes = load_notes()
        notes.append({"text": note, "at": time.time()})
        save_notes(notes)
        self.say(["Got it, I'll remember that.", "Okay, noted."])

    def list_notes(self):
        notes = load_notes()
        if not notes:
            self.say("You haven't asked me to remember anything yet.")
            return
        self.j.chat("sys", "notes:\n" + "\n".join("- " + _note_line(n) for n in notes))
        self.j.show({"title": "Your notes", "text": "\n".join("- " + _note_line(n) for n in notes)})
        recent = notes[-3:]
        self.say("You've asked me to remember %d thing%s. %s: %s." % (
            len(notes), "" if len(notes) == 1 else "s", "The latest" if len(notes) > 3 else "They are",
            "; ".join(n["text"] for n in reversed(recent))))

    def recall(self, m):
        hits = _search(load_notes(), m.group(1))
        if not hits:
            return False  # maybe a general question: let Claude have it
        self.say("You told me: %s." % "; and ".join(n["text"] for n in hits[:2]))

    def forget(self, m):
        what = m.group(1).strip()
        notes = load_notes()
        if re.match(r"^(?:everything|all(?: (?:my|the))? notes|all of it)$", what, re.IGNORECASE):
            if notes and self.j.ask("Forget all %d notes?" % len(notes)):
                save_notes([])
                self.say("Done, all forgotten.")
            return
        hits = _search(notes, what)
        if not hits:
            return False
        if self.j.ask('Forget "%s"?' % hits[0]["text"]):
            notes.remove(hits[0])
            save_notes(notes)
            self.say("Forgotten.")


def _search(notes, query):
    wanted = set(words(query))
    scored = [(len(wanted & set(words(n["text"]))), n) for n in notes]
    return [n for score, n in sorted(scored, key=lambda s: -s[0]) if score]
