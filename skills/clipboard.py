""""What's on my clipboard", and Claude jobs on it ("summarise what I copied")."""
from claude.bridge import ClaudeError
from skills.base import Skill
from skills.helpers import clipboard, set_clipboard


class Clipboard(Skill):
    group = "clipboard"
    does = "Reads your clipboard, and has Claude rewrite what's on it"
    phrases = ("what's on my clipboard", "summarise what I copied", "fix the grammar in my clipboard",
               "copy what you said", "copy the text above", "copy our conversation")
    gives = "speech, or the result on your clipboard"
    permission = "auto"
    checks = "not checked; Claude failing leaves it alone"

    def patterns(self):
        return [
            (r"(?:copy|save|grab|put) (?:(?:all|the whole|the entire|all of) )?(?:of )?(?:our|this|the|my)(?: whole| entire| full)? "
             r"(?:conversation|chat(?: history| log)?|transcript|talk)(?: so far)?(?: to (?:the |my )?clipboard)?|"
             r"copy (?:everything|all) (?:we|you and i|i and you) (?:said|talked about|discussed)",
             lambda m: self.copy_conversation()),
            (r"copy (?:what you (?:just )?said|what you (?:just )?told me|your (?:last |previous )?(?:answer|reply|"
             r"response|message)|(?:that|the|the last|the previous) (?:answer|reply|response))"
             r"(?: to (?:the |my )?clipboard)?",
             lambda m: self.copy_said()),
            (r"copy (?:the )?(?:above|previous|last)(?: text| message| line)?|copy (?:the )?(?:text|message|line) above|"
             r"copy (?:the )?text (?:from )?above(?: this)?|copy what'?s above",
             lambda m: self.copy_above()),
            (r"(?:what'?s|what is) (?:on|in) (?:my|the) clipboard|read (?:my|the) clipboard|what did i (?:just )?copy",
             lambda m: self.read_clipboard()),
            (r"((?:summari[sz]e|explain|translate|fix|correct|rewrite|reword|shorten|improve|make|turn|"
             r"proofread|simplify)\b.*?)\b(?:what i (?:just )?copied|(?:my|the) clipboard|the copied text)\b(.*)",
             self.clipboard_job),
        ]

    def copy_conversation(self):
        lines = list(self.j.transcript)[:-1]  # the last line is this request itself
        if not lines:
            self.say("We haven't said anything yet.")
            return
        set_clipboard("\n".join(lines))
        self.say("Copied our conversation, %d lines. Just paste it." % len(lines))

    def copy_said(self):
        said = self.j.speaker.last
        if not said:
            self.say("I haven't said anything yet.")
            return
        set_clipboard(said)
        self.say(["Copied.", "It's on your clipboard."])

    def copy_above(self):
        """The card on screen is what's "above" when there is one; otherwise the last thing said."""
        ui = self.j.ui
        card = ui.current_card() if ui and ui.card_visible() else None
        if not (card and card.get("text")):
            self.copy_said()
            return
        set_clipboard(card["text"])
        self.say(["Copied.", "It's on your clipboard."])

    def read_clipboard(self):
        text = clipboard().strip()
        if not text:
            self.say("Your clipboard is empty.")
        elif len(text) <= 200:
            self.say("You copied: %s" % text)
        else:
            self.j.chat("sys", "clipboard:\n" + text[:3000])
            self.j.skills.reading.long(text, "Clipboard")

    def clipboard_job(self, m):
        text = clipboard().strip()
        if not text:
            self.say("Your clipboard is empty. Copy something first.")
            return
        instruction = (m.group(1) + " this " + m.group(2)).strip()
        self.say(["On it.", "Sure, one moment."])
        try:
            result = self.j.claude.rewrite(instruction, text)
        except ClaudeError as e:
            self.say("Sorry, that didn't work: %s." % e)
            return
        verb = m.group(1).split()[0].lower()
        if verb in ("summarise", "summarize", "explain") and len(result) < 400:
            self.j.claude_says(result)
        else:
            set_clipboard(result)
            self.j.chat("claude", result)
            self.say(["Done, it's on your clipboard. Just paste it.", "Done. Paste it wherever you like."])
