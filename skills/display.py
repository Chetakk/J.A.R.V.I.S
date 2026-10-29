""""What's my screen resolution", "set the resolution to 1280 by 720", "change the refresh rate to 60", "use the
recommended resolution": through xrandr (system/display.py). A change asks first, then JARVIS asks whether you can
see the screen: only a yes keeps it; no answer, or a no, puts the old mode back, and so does a timer after
REVERT_SECONDS even if JARVIS is stuck. Brightness and rotation stay commands in config/commands.yaml."""
import re
import time

from skills.base import Skill
from system import display

REVERT_SECONDS = 15
SCREEN = r"(?:the |my )?(?:screen|display|monitor)"


class Display(Skill):
    def patterns(self):
        return [
            (r"what(?:'s| is) (?:my |the )?(?:screen |display |monitor )?(?:resolution|refresh rate)|what (?:resolution|"
             r"refresh rate) is %s(?: at| using| set to)?|(?:what|which) (?:screens|displays|monitors) (?:are|do i have)"
             r"(?: connected| plugged in)?|(?:screen|display|monitor) (?:info|settings|details)" % SCREEN,
             lambda m: self.info()),
            (r"(?:set|change|switch|put|make) (?:the |my )?(?:screen |display |monitor )?resolution (?:to |at |back to )?"
             r"(.+)|(?:set|change|switch|put) %s (?:resolution )?to (.+?)(?: resolution)?" % SCREEN, self.resolution),
            (r"(?:set|change|switch|put|make) (?:the |my )?(?:screen'?s? |display'?s? |monitor'?s? )?refresh rate "
             r"(?:to |at |back to )?(.+)|(?:use|set|switch to) (?:the )?(highest|max(?:imum)?|lowest) refresh rate",
             self.refresh),
            (r"(?:use|set|switch to|go back to|reset to) (?:the )?(?:recommended|default|native|normal|best) "
             r"(?:screen |display )?resolution|reset (?:the |my )?(?:screen |display )?resolution",
             lambda m: self.recommended()),
        ]

    def _screen(self):
        found = display.screens()
        if not found:
            self.say("I can't read the screen settings.")
        return found[0] if found else None

    def info(self):
        found = display.screens()
        if not found:
            self.say("I can't read the screen settings.")
            return
        self.j.show({"title": "Screens", "text": "\n\n".join(
            "**%s**%s: %s at %s Hz\n%s" % (s.name, " (main)" if s.primary else "", s.mode, s.rate, "\n".join(
                "- %s: %s Hz%s" % (mode, ", ".join(rates), " (recommended)" if s.preferred and s.preferred[0] == mode
                                   else "") for mode, rates in list(s.modes.items())[:12])) for s in found)})
        s = found[0]
        self.say("%s is at %s, %s hertz%s.%s" % (
            "Your screen" if len(found) == 1 else "The main screen", _spoken(s.mode), _hz(s.rate),
            "" if not s.preferred or s.preferred[0] == s.mode else "; it recommends %s" % _spoken(s.preferred[0]),
            " %d screens are connected." % len(found) if len(found) > 1 else ""))

    def resolution(self, m):
        words = next(g for g in m.groups() if g)
        mode = display.resolution(words)
        if not mode:
            if re.search(r"recommend|default|native|normal|best", words, re.I):
                return self.recommended()
            self.say("Which resolution? Say it like 1280 by 720.")
            return
        s = self._screen()
        if not s:
            return
        if mode not in s.modes:
            self.say("%s can't do %s. It can do %s." % (s.name, _spoken(mode), ", ".join(
                _spoken(x) for x in list(s.modes)[:5])))
            return
        self._try(s, mode, display.best_rate(s, mode))

    def refresh(self, m):
        words = next(g for g in m.groups() if g).lower()
        s = self._screen()
        if not s:
            return
        rates = s.modes.get(s.mode) or []
        if re.match(r"high|max", words):
            rate = max(rates, key=float, default=None)
        elif words.startswith("low"):
            rate = min(rates, key=float, default=None)
        else:
            number = re.search(r"\d+(?:\.\d+)?", words)
            if not number:
                self.say("Which refresh rate? %s can do %s hertz." % (s.name, ", ".join(_hz(r) for r in rates)))
                return
            rate = display.best_rate(s, s.mode, number.group(0))
            if rate and abs(float(rate) - float(number.group(0))) > 1:
                self.say("%s can't do %s hertz at this resolution; it can do %s." % (
                    s.name, number.group(0), ", ".join(_hz(r) for r in rates)))
                return
        self._try(s, s.mode, rate)

    def recommended(self):
        s = self._screen()
        if s and not s.preferred:
            self.say("%s doesn't say which resolution it recommends." % s.name)
        elif s:
            self._try(s, *s.preferred)

    def _try(self, s, mode, rate):
        if (mode, rate) == (s.mode, s.rate):
            self.say("It's already at %s, %s hertz." % (_spoken(mode), _hz(rate)))
            return
        if not self.j.ask("Change the screen to %s at %s hertz? If it goes black, I'll put it back in %d seconds." % (
                _spoken(mode), _hz(rate), REVERT_SECONDS)):
            self.say("Okay, I'll leave it.")
            return
        trial = display.Trial(s, mode, rate, REVERT_SECONDS)
        if not trial.ok:
            self.say("The screen didn't take it, so nothing changed.")
            return
        time.sleep(2)  # the monitor needs a moment to show the new mode
        if self.j.ask("Can you see the screen properly? Say yes to keep it."):
            if trial.keep():
                self.j.last_undo = lambda: display.apply(*trial.old) and self.say("The screen is back how it was.")
                self.say("Kept. Say undo that to go back.")
            else:
                self.say("Too late, I'd already put it back. Ask again and say yes a bit sooner.")
            return
        trial.revert()
        self.say("Okay, I've put the screen back to %s, %s hertz." % (_spoken(s.mode), _hz(s.rate)))


def _spoken(mode):
    return mode.replace("x", " by ")


def _hz(rate):
    return ("%.2f" % float(rate)).rstrip("0").rstrip(".")
