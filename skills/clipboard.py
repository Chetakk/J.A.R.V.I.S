""""What's on my clipboard", and Claude jobs on it ("summarise what I copied")."""
from claude.bridge import ClaudeError
from skills.base import Skill
from skills.helpers import clipboard, set_clipboard


class Clipboard(Skill):
    def patterns(self):
        return [
            (r"(?:what'?s|what is) (?:on|in) (?:my|the) clipboard|read (?:my|the) clipboard|what did i (?:just )?copy",
             lambda m: self.read_clipboard()),
            (r"((?:summari[sz]e|explain|translate|fix|correct|rewrite|reword|shorten|improve|make|turn|"
             r"proofread|simplify)\b.*?)\b(?:what i (?:just )?copied|(?:my|the) clipboard|the copied text)\b(.*)",
             self.clipboard_job),
        ]

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
            self.j.chat("claude", result)
            self.say(result)
        else:
            set_clipboard(result)
            self.j.chat("claude", result)
            self.say(["Done, it's on your clipboard. Just paste it.", "Done. Paste it wherever you like."])
