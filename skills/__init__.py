"""Everything JARVIS does itself, before the command list and Claude: one module per skill.

The order matters: the first skill whose phrase matches (and doesn't hand it on) wins. Cards come first
("the second one" is about the card on screen), then the instant local extras, then the rest; the day
tracker and todos go before projects ("start work" isn't a project) and window focus goes last.
"""
from skills.browser_tabs import BrowserTabs
from skills.cards import Cards
from skills.clipboard import Clipboard
from skills.clipboard_history import ClipboardHistory
from skills.daytracker import DayTracker
from skills.dev import Dev
from skills.dictation import Dictation
from skills.files import Files
from skills.focus_mode import FocusMode
from skills.layouts import Layouts
from skills.maths import Maths
from skills.media import Media
from skills.nano import Nano
from skills.notes import Notes
from skills.projects import Projects
from skills.screen import Screen
from skills.slow_computer import SlowComputer
from skills.status import Status
from skills.todos import Todos
from skills.where_was_i import WhereWasI
from skills.windows import Windows

ORDER = [
    ("cards", Cards), ("dictation", Dictation), ("maths", Maths), ("focus_mode", FocusMode),
    ("slow_computer", SlowComputer), ("where_was_i", WhereWasI), ("clipboard_history", ClipboardHistory),
    ("status", Status), ("daytracker", DayTracker), ("todos", Todos), ("projects", Projects), ("screen", Screen),
    ("browser_tabs", BrowserTabs), ("media", Media), ("clipboard", Clipboard), ("notes", Notes), ("files", Files),
    ("nano", Nano), ("layouts", Layouts), ("windows", Windows), ("dev", Dev),
]


class Skills:
    def __init__(self, jarvis):
        self.all = []
        for name, cls in ORDER:
            skill = cls(jarvis)
            setattr(self, name, skill)
            self.all.append(skill)

    def first(self, text):
        """Before everything else: types the words while dictating, and remembers the request for "where was I"."""
        if self.dictation.typing(text):
            return True
        self.where_was_i.log(text)
        return False

    def handle(self, text):
        """True if a skill took care of `text`."""
        return any(skill.handle(text) for skill in self.all)

    def focusing(self):
        return self.focus_mode.focusing()
