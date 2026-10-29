"""Requests that leave out the one thing they need: "set a timer", "add a note", "set the volume", "switch the mic".
JARVIS asks for it ("Sure, for how long?") and carries on with the whole request ("set a timer for 5 minutes").

Without these the meaning matcher picked the nearest phrase it knew ("add a todo" -> "what's on my todo list",
"change the mic" -> "mute microphone"), and value patterns took a placeholder as the value (typing "something").
config/intents.yaml lists other wordings for each of these bare phrases, so "I'd like a timer" lands here too.
Reminders and alarms without a time or a text are asked about in skills/todos.py.
"""
import re

from skills.base import Skill

# "I want you to set a timer", "just add a note", "help me install something"
PRE = r"(?:(?:i want you to|i need you to|i'd like you to|i want to|i need to|i'd like to|go ahead and|just|let's|" \
      r"help me|to) )?"
NO = re.compile(r"(?:no|nope|nothing|never ?mind|cancel|forget it|leave it|none|not now)\b", re.I)
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
SITES = r"(youtube|github|amazon|wikipedia)"


def _number(answer):
    """"5", "workspace five", "the second one" -> 5 / 2; None if there's no number."""
    m = re.search(r"\b(\d+|%s)\b" % "|".join(NUMBERS), answer.lower())
    return m and (int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1)])


def _volume(answer, m):
    low = answer.lower()
    if re.search(r"\b(?:up|louder|higher|more|raise)\b", low):
        return "volume up"
    if re.search(r"\b(?:down|quieter|lower|less|softer)\b", low):
        return "volume down"
    if re.search(r"\bmute\b", low):
        return "mute"
    if re.search(r"\b(?:max|maximum|full)\b", low):
        return "set volume to 100"
    n = _number(answer)
    return "set volume to %d" % n if n is not None and 0 <= n <= 100 else None


def _brightness(answer, m):
    low = answer.lower()
    for words, level in ((r"full|max|maximum|brightest", "full"), (r"high|bright|brighter|up", "high"),
                         (r"medium|half|middle|normal", "medium"), (r"low|dim|darker|down", "low")):
        if re.search(r"\b(?:%s)\b" % words, low):
            return "brightness " + level
    n = _number(answer)
    if n is None:
        return None
    return "brightness %d" % min((100, 80, 60, 40), key=lambda b: abs(b - n))  # the levels the command list has


def _workspace(answer, m):
    n = _number(answer)
    return "go to workspace %d" % n if n else None


def _move_window(answer, m):
    low = answer.lower()
    for side in ("left", "right"):
        if re.search(r"\b%s\b" % side, low):
            return "snap this to the %s" % side
    n = _number(answer)
    return "move this to workspace %d" % n if n else None


def _resolution(answer, m):
    if re.search(r"\b(?:recommended|default|native|normal|best|usual)\b", answer, re.I):
        return "use the recommended resolution"
    return "set the resolution to %s" % answer


class Missing(Skill):
    def patterns(self):
        some = r"(?:something|anything|some stuff|stuff|a thing)"
        app = r"(?:an? |some |another |a new )?(?:app|application|program|something)"
        rules = [
            ("timer", r"(?:(?:set|start|make|create|give me|get me|put on)(?: up| me)? (?:a |an |the |one |my |our )?"
                      r"(?:new )?|i'd like a |i want a |i need a |new |a )?(?:timer|countdown)(?: please)?",
             "Sure, for how long?", "set a timer for {0}", "for|about"),
            ("note", r"(?:(?:add|take|make|write|create|save|start|leave)(?: me)? (?:a |an |another )?(?:new |quick )?note|"
                     r"new note|a note|note|(?:jot|note|write|put) (?:%s|this|that|it) down|"
                     r"(?:remember|note) (?:%s|something for me)|i have a note|take notes?)(?: for me| please)?" % (some, some),
             "Sure, what should the note say?", "remember that {0}", "that|to"),
            ("todo", r"(?:(?:add|make|create|start|put|write)(?: me)? (?:a |an |another )?(?:new )?(?:to ?-?do|task|todo item)"
                     r"(?: to my (?:to ?-?do |task )?list)?|new (?:to ?-?do|task)|"
                     r"(?:add|put) (?:%s |an item |one )?(?:to|on|in) (?:my |the )?(?:to ?-?do|task)(?: list)?|"
                     r"(?:add|put) %s (?:on|to|in) my list|i have a (?:new )?task)(?: for me| please)?" % (some, some),
             "Sure, what should I add?", "add {0} to my todo list", "to"),
            ("volume", r"(?:set|change|adjust|put)(?: the| my)? volume(?: level)?",
             "Sure, to what level?", _volume, "to|at"),
            ("brightness", r"(?:set|change|adjust|put)(?: the| my)?(?: screen)? brightness(?: level)?",
             "Sure, how bright? Full, high, medium or low?", _brightness, "to|at"),
            ("wifi", r"(?:(?:connect|switch|change|move)(?: me)? (?:to )?(?:a |the |another |a different |a new )?"
                     r"(?:wifi|wi-fi|wireless|network)(?: network)?|join (?:a |another |a different )?(?:wifi |wi-fi )?"
                     r"network|switch wifi|change (?:the |my )?wifi(?: network)?)",
             "Sure, which network?", "connect to wifi {0}", "to|the"),
            ("open_app", r"(?:open|launch|start|run)(?: up)? %s" % app, "Sure, which app?", "open {0}", "the"),
            ("close_app", r"(?:close|quit|shut)(?: down)? %s" % app, "Which app should I close?", "close {0}", "the"),
            ("search", r"(?:search|google|look up|search for|do a search|search the web|search online|search the internet|"
                       r"look %s up|search (?:for )?%s|google %s)" % (some, some, some),
             "Sure, what should I search for?", "search for {0}", "for"),
            ("search_site", r"(?:search|search on|look up on|look %s up on|find %s on) %s" % (some, some, SITES),
             "Sure, what should I look up?", "search {1} for {0}", "for"),
            ("maps", r"(?:search|look up on|search on) (?:google )?maps|(?:open )?(?:google )?maps search",
             "Sure, what should I look for on the map?", "search maps for {0}", "for"),
            ("directions", r"(?:get |give me |find )?directions|navigate(?: somewhere| me)?|how do i get somewhere",
             "Sure, where to?", "directions to {0}", "to"),
            ("find_file", r"(?:search|look through) (?:my |the )?(?:files|documents|computer)|"
                          r"find (?:a |my |some |the )?(?:file|document)",
             "Sure, which file?", "find my {0}", "the|my|a"),
            ("type", r"type (?:%s|this|that|for me|some text|out %s)(?: for me| here)?|"
                     r"write %s(?: for me| here)?|start typing for me" % (some, some, some),
             "Sure, what should I type?", "type: {0}", None),
            ("workspace", r"(?:go|switch|change|move)(?: me)? (?:to )?(?:a |another |a different |the other )?workspace|"
                          r"(?:switch|change) workspaces?",
             "Sure, which workspace? Say a number.", _workspace, "to"),
            ("move_window", r"(?:move|send|put) (?:this|it|this window|the window) (?:to )?(?:a |another |a different |"
                            r"the other )?workspace|move (?:this|the) window(?: somewhere)?",
             "Sure, where to? Left, right, or a workspace number?", _move_window, "to"),
            ("resolution", r"(?:set|change|adjust|switch)(?: the| my)?(?: screen| display| monitor)? resolution",
             "Sure, to what? Say something like 1920 by 1080, or the recommended one.", _resolution, "to"),
            ("refresh_rate", r"(?:set|change|adjust|switch)(?: the| my)?(?: screen| display| monitor)? refresh rate",
             "Sure, to what rate? Like 60.", "change the refresh rate to {0}", "to"),
            ("audio_output", r"(?:switch|change|set|move)(?: the| my)? (?:audio|speakers?|output|audio output|sound output|"
                             r"output device|audio device|playback)(?: device)?|use (?:a |another |a different )"
                             r"(?:speaker|output)",
             "Sure, to which speaker?", "switch audio to {0}", "to|the"),
            ("mic", r"(?:switch|change|set|use)(?: the| my)? (?:mic|microphone|mike|input|input device)(?: device)?|"
                    r"use (?:a |another |a different )(?:mic|microphone)",
             "Sure, which mic?", "switch the mic to {0}", "to|the"),
            ("install", r"(?:install|download and install) (?:%s|an app|a program|an application|a package|some software|"
                        r"software|a new app)" % some,
             "Sure, what should I install?", "install {0}", None),
            ("uninstall", r"(?:uninstall|remove) (?:%s|an app|a program|an application|a package|some software)" % some,
             "Sure, what should I uninstall?", "uninstall {0}", None),
            ("folder", r"(?:make|create|add)(?: me)? (?:a )?(?:new )?folder",
             "Sure, what should I call it?", "make a folder called {0}", "call it|it's|its|called|name it"),
            ("rename_file", r"rename (?:this|that|the) file", "Sure, what should the new name be?",
             "rename this file to {0}", "to|call it"),
            ("delete_file", r"(?:delete|trash|remove) (?:a |some )?files?|(?:delete|trash) %s" % some,
             "Sure, which file?", "delete {0}", None),
        ]
        return [(PRE + pattern + r"(?: for me| please)?", self._asker(name, question, fill, lead))
                for name, pattern, question, fill, lead in rules]

    def _asker(self, name, question, fill, lead):
        """A handler that asks `question`, then handles `fill` with the answer in it."""
        def fn(m):
            answer = re.sub(r"\s+", " ", (self.j._answer(question) or "").replace(",", " ")).strip(" .!?")
            if lead:
                answer = re.sub(r"^(?:%s) " % lead, "", answer, flags=re.I)
            if not answer or NO.match(answer):
                self.say(["Okay, never mind.", "Okay."])
                return
            phrase = fill(answer, m) if callable(fill) else fill.format(answer, *[g or "" for g in m.groups()])
            if not phrase:
                self.say("Sorry, I didn't get that.")
                return
            self.j._reword(phrase)
        fn.__name__ = "ask_" + name  # what the dry run shows: skill:ask_timer
        return fn
