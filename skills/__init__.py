"""Everything JARVIS does itself, before the command list and Claude: one module per skill.

The order matters: the first skill whose phrase matches (and doesn't hand it on) wins. Cards come first
("the second one" is about the card on screen), then the instant local extras, then the rest; the day
tracker and todos go before projects ("start work" isn't a project); the browser agent before projects
and the screen ("what's this page about" in a browser reads the page itself); copying between apps before the
clipboard ("summarise my clipboard and put it in Slack"); clicking in a window and window focus go last.
"""
from skills.browser_tabs import BrowserTabs
from skills.cards import Cards
from skills.checks import Checks
from skills.clipboard import Clipboard
from skills.clipboard_history import ClipboardHistory
from skills.controls import Controls
from skills.copy_between import CopyBetween
from skills.daytracker import DayTracker
from skills.dev import Dev
from skills.dictation import Dictation
from skills.files import Files
from skills.focus_mode import FocusMode
from skills.frozen import Frozen
from skills.layouts import Layouts
from skills.maths import Maths
from skills.media import Media
from skills.nano import Nano
from skills.notes import Notes
from skills.proactive import Proactive
from skills.projects import Projects
from skills.reading import Reading
from skills.screen import Screen
from skills.slow_computer import SlowComputer
from skills.status import Status
from skills.todos import Todos
from skills.troubleshoot import Troubleshoot
from skills.voice_prefs import VoicePrefs
from skills.web import Web
from skills.where_was_i import WhereWasI
from skills.windows import Windows

ORDER = [
    ("cards", Cards), ("reading", Reading), ("dictation", Dictation), ("voice_prefs", VoicePrefs),
    ("maths", Maths), ("focus_mode", FocusMode), ("proactive", Proactive),
    ("slow_computer", SlowComputer), ("frozen", Frozen), ("where_was_i", WhereWasI),
    ("clipboard_history", ClipboardHistory),
    ("status", Status), ("checks", Checks), ("daytracker", DayTracker), ("todos", Todos),
    ("troubleshoot", Troubleshoot), ("web", Web), ("projects", Projects), ("screen", Screen),
    ("browser_tabs", BrowserTabs), ("media", Media), ("copy_between", CopyBetween), ("clipboard", Clipboard),
    ("notes", Notes), ("files", Files),
    ("nano", Nano), ("layouts", Layouts), ("controls", Controls), ("windows", Windows), ("dev", Dev),
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
