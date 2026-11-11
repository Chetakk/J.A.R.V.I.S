"""The end-of-day wrap: "wrap up my day", "what's left for today", "before I log off" ("I'm done for the day" stops
the Day Tracker instead). On one card: emails and direct messages waiting on your answer, tomorrow's meetings, and
the to-dos still open."""
from skills.google_common import GoogleSkill, first_name, short
from skills.helpers import and_list
from system import google_account as google


class Wrap(GoogleSkill):
    group = "wrap"
    does = "The end-of-day wrap: emails and chats waiting on you, tomorrow's first meeting, open to-dos"
    phrases = ("wrap up my day", "what's left for today", "before I log off")
    gives = "a card and a short spoken line"
    permission = "auto"
    checks = "says which parts it couldn't reach (Google, the to-do list) rather than leaving them out silently"

    def patterns(self):
        return [
            (r"(?:wrap ?up|close out|round up) (?:my |the )?(?:day|work ?day)|(?:give me |do )?(?:my |the |an )?"
             r"(?:end of (?:the )?day|eod|daily) (?:wrap(?:[- ]?up)?|summary|recap|review)|what'?s (?:left|still open|"
             r"pending|waiting on me) (?:for |from )?today|before i (?:go|log off|leave|head out)|i'?m (?:done|logging "
             r"off|heading out|signing off) for (?:the day|today)", self.wrap),
        ]

    def wrap(self, m):
        lines, said, missing = [], [], []
        if google.connected():
            try:
                mail = self.j.skills.gmail.waiting_on_you()
                if mail:
                    lines += ["{warn} **Emails waiting on you** %d" % len(mail)] + ["- [ ] **%s** · %s" % (n, s)
                                                                                for n, s in mail] + [""]
                    said.append("%s waiting on you, from %s." % (
                        "One email" if len(mail) == 1 else "%d emails" % len(mail),
                        and_list(sorted({first_name(n) for n, _ in mail}))))
            except google.GoogleError:
                missing.append("Gmail")
            try:
                chats = self.j.skills.google_chat.waiting_on_you()
                if chats:
                    lines += ["{warn} **Chats waiting on you** %d" % len(chats)] + [
                        "- [ ] **%s** · %s" % (n, short(t, 30)) for n, t in chats] + [""]
                    names = and_list(first_name(n) for n, _ in chats)
                    said.append("%s %s waiting on Chat." % (names, "is" if len(chats) == 1 else "are"))
            except google.GoogleError:
                missing.append("Chat")
            meet_lines, meet_said = self.j.skills.calendar.for_briefing(day_offset=1)
            lines += meet_lines + ([""] if meet_lines else [])
            if meet_said:
                said.append(meet_said)
        else:
            missing.append("Google")
        todos = self.j.skills.todos.open_todos()
        if todos:
            lines += ["**Still open**"] + ["- %s" % t["text"] for t in todos[:8]]
            said.append("%s still open." % ("One to-do" if len(todos) == 1 else "%d to-dos" % len(todos)))
        if missing:
            lines += ["", "_Couldn't check %s._" % and_list(missing)]
        if lines:
            self.j.show({"id": "wrap", "title": "End of day", "text": "\n".join(lines).strip()})
        self.say(("Before you go: " + " ".join(said)) if said else
                 "All clear: nothing waiting on you." + (" I couldn't check %s, though." % and_list(missing)
                                                         if missing else ""))
