#!/usr/bin/env python3
"""JARVIS: a light, fast voice/text assistant for OS and browser tasks.

Modes:
  /agent  command list first, then ask Claude
  /sys    command list only, no AI
"""
import fcntl
import json
import os
import queue
import random
import re
import shlex
import subprocess
import sys
import threading
import time
from collections import deque

import yaml

try:
    import readline  # noqa: F401  (arrow keys + history in the prompt)
except ImportError:
    pass

from core import context, learned, misslog, outcomes, persona, teaching, undo, verify
from system import apps, desktop, watchdog
from system.browser import Browser
from claude.bridge import WORKSPACE, Claude, ClaudeError, open_in_vscode
from core.executor import Executor
from skills import Skills, registry
from voice.listen import PushToTalk
from system.projects import Projects
from system.apps import Apps
from core.context import Context, sounds_like_follow_up
from core.chat import ChatReplies
from core.intent import IntentMatcher
from core.registry import Registry, _names_regex, clean
from core.safety import MANUAL, Safety
from voice.speech import Speaker, pick
from voice.stt import Whisper
from system.terminal import RUN_DIR
from voice.wake import WakeWord, WhisperWake

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config")

HELP = """Commands:
  /agent    command list, then Claude when nothing matches
  /sys      command list only (no AI)
  /list     show all commands
  /reload   reload the YAML files
  /new      start a fresh conversation with Claude
  /mute [voice|mic|sound] [on|off]   mute my voice, the microphone or the speaker (/mute alone: my voice)
  /chat [on|off]   show/hide the chat panel   (or say "show chat" / "hide chat")
  /orb [on|off]    show/hide the floating orb (or say "show orb" / "hide orb")
  /hearing [on|off]  show everything speech recognition hears in the chat ("show what you hear")
  /desk     go to JARVIS's workspace
  /check <command>   show what safety level a shell command gets
  /status   voice, speech recognition, Claude, disk and memory at a glance ("EV, status")
  /quit     exit
Anything else is treated as a request, e.g. "open youtube in firefox".
"open claude" opens Claude Code in a terminal, continuing JARVIS's conversation.
"open claude in vs code" / "ask claude in vs code to ..." uses the Claude Code panel in VS Code.
Built-in skills (skills/): "stop", "status", "what's on my screen", "close this tab", "next tab",
"switch to the youtube tab", "scroll down", "pause", "next song", "what's playing", "what did I copy",
"fix the grammar in what I copied", "remember that ...", "what did I ask you to remember", "find my resume",
"open the latest download", "maximise", "move this to workspace 2", "put chrome on the left", "switch to vs code",
"is the nano up", "run uptime on the nano", "git status of study buddy", "open the study buddy project",
"run the tests in jarvis", "ask claude to <task> in the <name> project".
Projects (~/Projects, system/projects.py; spoken names in config/projects.yaml): "run avtec hs" (biztel run, in a
terminal), "stop avtec hs", "what's running", "links for avtec hs", "open avtec hs in the browser", "biztel build",
"what's study buddy about", "list my projects", "what was I working on", "note for avtec hs: <text>",
"notes for avtec hs", "what's pending", "refresh projects". "run it" / "its links" = the project you last named."""

OPEN_CLAUDE = {"open claude", "open claude terminal", "open claude code", "talk to claude"}
OPEN_CLAUDE_VSCODE = {"open claude in vs code", "open claude in vscode", "open claude code in vs code",
                      "open claude code in vscode"}
# /mute <what>: the three things that can be muted, and the words for each.
MUTE_WHAT = {"voice": "voice", "speech": "voice", "me": "voice", "jarvis": "voice", "yourself": "voice",
             "mic": "mic", "microphone": "mic", "input": "mic",
             "sound": "sound", "speaker": "sound", "speakers": "sound", "audio": "sound", "output": "sound"}

ASK_CLAUDE_VSCODE = re.compile(r"^(?:ask|tell) claude(?: code)? in (?:vs ?code) (?:to )?(.+)$", re.IGNORECASE)
# JARVIS itself, not an app or a project called Jarvis (%s: its names): "close Jarvis", "restart yourself".
QUIT_ME = r"^(?:close|quit|exit|shut ?down|turn off|switch off|kill) (?:yourself|%s)$"
RESTART_ME = r"^(?:restart|reload|reboot|relaunch) (?:yourself|%s)$"
RESTART_CODE = 4  # tools/run_jarvis.sh starts JARVIS again straight away
# "Ask Claude to add a timer to Jarvis", "tell Claude in VS Code that the Jarvis icon doesn't show": a change to
# JARVIS itself when the request names it (or "yourself") anywhere.
ASK_CLAUDE_ME = re.compile(r"\b(?:ask|asks|tell|get|have) claude(?: code)?( in vs ?code)?(?: to| that|,)? (.+)",
                           re.IGNORECASE)
MENTIONS_ME = r"(?<![a-z])(?:yourself|your (?:code|project)|%s)"
# "Make these changes in Jarvis": the task points at what was just said, so Claude Code gets that too.
POINTS_BACK = re.compile(r"\b(?:this|that|these|those|it|them|what (?:we|you|i) (?:said|discussed|talked about))\b",
                         re.IGNORECASE)
CHAT_PHRASES = {"show what you hear": "/hearing on", "hide what you hear": "/hearing off",
                "stop showing what you hear": "/hearing off",
                "show chat": "/chat on", "open chat": "/chat on", "show the chat": "/chat on",
                "hide chat": "/chat off", "close chat": "/chat off", "hide the chat": "/chat off",
                "show orb": "/orb on", "hide orb": "/orb off",
                "show that again": "/card on", "show the card": "/card on", "show it again": "/card on",
                "close the card": "/card off", "hide the card": "/card off", "close card": "/card off",
                "hide that": "/card off",
                "go to your workspace": "/desk", "take me to your workspace": "/desk", "go to jarvis workspace": "/desk"}



# What JARVIS understands besides its skills, for Claude's "do" field. The skills describe themselves
# (skills/registry.py); sites, searches and saved commands are added from the YAML.
ABILITIES = (
    "small talk (a spoken reply): \"hi\", \"what's up\", \"how are you\", \"thanks\", \"good night\", \"bye\", "
    "\"tell me a joke\"",
    "itself: \"open claude\", \"show chat\", \"do that again\", \"undo that\" (reverses the last setting or file "
    "change)",
    "apps and folders: \"open <app>\", \"close <app>\", \"open the <name> folder\"",
)

# What Claude Code gets when asked to change JARVIS itself ("fix ... in jarvis").
FIX_PROMPT = ("You're changing JARVIS itself: the voice assistant in this folder (start at jarvis.py; settings and "
              "commands are in config/). It is running right now and picks up code changes after a restart. Keep "
              "the change small and in the style of the code around it, keep it light and fast, check that the "
              "files still compile, and say if JARVIS needs a restart.\n\nTask: %s")

FIX_ASKED = re.compile(r"\b(?:fix|change|improve|update|make (?:it|you|yourself)|add|remove|stop|don't|do not|"
                       r"should(?:n't)?|teach|set (?:it|yourself))\b", re.IGNORECASE)

# Words that alone don't make a request: "is", "put that", "what the", "okay okay" are mishearings, not questions.
FILLER = set("""a an the and but or so is it it's its that this those these what what's whats up um uh hmm
huh oh ok okay yeah yes no yep nope well like just i i'm me you your we put get got go do did be been was
there here then now hey hi hello right sure fine good why how when where who which""".split())
CODE = re.compile(r"[{}=;<>`$\\]|\b(?:import|def|return|sys\.\w+)\b")


def worth_asking(text, names):
    """False for speech-recognition junk that shouldn't cost a Claude call."""
    if "\n" in text or CODE.search(text):
        return False  # pasted or leaked code, not something the user said
    name_words = {w for n in names for w in n.split()}
    words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in name_words]
    real = [w for w in words if w not in FILLER]
    return bool(real) and (len(words) > 1 or len(real[0]) >= 4)


# Open questions are for Claude, even when they sound a bit like a command ("what time is it in London").
OPEN_QUESTION = re.compile(r"^(?:who|whom|whose|why|explain|describe|define|compare|write|summari[sz]e|translate|"
                           r"recommend|suggest|should i|tell me (?:about|a|how|why)|can you help|help me|"
                           r"what (?:is|are|was|were|does|do) (?:a|an|the)\b|how (?:do|does|did|can|could|should|"
                           r"would|to)\b)|\b(?:in|at) (?!a\b|the\b|my\b)[a-z]+$|\b(?:yesterday|last (?:week|month|year))\b",
                           re.IGNORECASE)
SPLIT = re.compile(r",? (?:and then|and also|and after that|after that|then|and) |, ", re.IGNORECASE)
UNSURE = 0.62  # from here up to the matcher's threshold, a guess is asked about (real questions scored lower)
YES = re.compile(r"^\W*(y|yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|please do|confirm|correct)\b", re.IGNORECASE)


def command_phrases(registry):
    """{first phrase: other phrases} for each command without {slots}; the first one runs it exactly."""
    out = {}
    for cmd in registry.commands:
        says = [p for p in cmd.get("say") or [] if "{" not in p]
        if says:
            out[says[0]] = says[1:]
    return out


class Jarvis:
    def __init__(self, inbox):
        self.inbox = inbox  # queue of ("text" | "voice" | "follow" | "eof" | "quit", str) from keyboard, mic and UI
        self.ui = None
        self.rest = "starting"  # state to return to when not busy: starting / idle / off
        self.load()
        self.show_hearing = bool((self.settings.get("ui") or {}).get("show_hearing", True))
        self.mutes = {"voice": False, "mic": False, "speaker": False}  # what's muted now: the tray icon shows it
        self.deaf = False  # "mute the mic": I stop listening; the system microphone stays live for other apps
        self.muted_on_purpose = set()  # of those, the ones you asked for; system/health.py doesn't warn about those
        self.speaker = Speaker(self.settings.get("speech") or {})
        self.speaker.on_say = self._said
        self.speaker.on_message = lambda msg: self.inbox.put(("ctl", msg))  # handled on the main loop
        self.speaker.quiet = lambda: persona.quiet_hours(self.settings)
        self.speaker.quiet_volume = (self.settings.get("quiet_hours") or {}).get("volume", 40)
        self.executor = Executor(self.safety, self.speaker, self.settings, self.ask)
        self.executor.notify = lambda line: self.inbox.put(("say", line))  # from its checking thread
        self.whisper_port = int((self.settings.get("stt") or {}).get("port", 8178))
        self.projects = Projects()  # ~/Projects: names, READMEs; running them is biztel run
        self.skills = Skills(self)  # everything JARVIS does itself: one module per skill in skills/
        self.cards = self.skills.cards  # "the second one", "read it to me", "pin it", timers, live cards
        self.mode = self.settings.get("mode", "agent")
        self.memory = persona.Memory()
        self.skills.voice_prefs.apply()  # "talk faster", "be brief", private / quiet mode, from last time
        self.skills.proactive.apply()  # quiet hours set by voice
        self.warned = {}  # heads-up key -> when it was last mentioned
        self.last_request = None  # for "do that again"
        self.last_app = None  # process of the last app opened, for "close it"
        self.last_undo = None  # saved command (or a skill's function) that reverses the last one, for "undo that"
        self.context = Context()  # what JARVIS just did, for "a bit more", "make it 70", "and firefox too"
        self._text = ""
        self._splitting = False  # handling the parts of "X and Y" one by one (they aren't split again)
        self.handling_since = None  # when the request being handled started (the watchdog restarts a stuck one)
        self._rewording = False  # handling a phrase the meaning matcher rewrote; it isn't rewritten again
        self.do_missed = False  # Claude's "do" phrase matched nothing (then it isn't learned)
        self.whisper = self.wake = None
        self.ignored = False  # last follow-up was chatter, not a request
        self.follow_up = lambda: None  # wake word: listen once more without the name (set by start_voice)
        self.keep_following = lambda: None  # wake word: noise doesn't end the follow-up window (set by start_voice)
        self.recent = deque(maxlen=8)  # what happened without Claude since its last answer, passed on next time
        self.talk = deque(maxlen=12)  # the last few lines both ways, Claude's answers too, for "make these changes"
        self.transcript = deque(maxlen=400)  # this conversation, for "copy our conversation" (private mode isn't kept)
        self.exit_code = 0  # RESTART_CODE after "restart yourself"
        self.source = "text"  # how the current request came in: text / voice / follow
        self.from_claude = False  # handling one of Claude's "do" phrases: never send it back to Claude
        self._claude_saying = False  # what's being said is Claude's answer: the chat panel shows it as Claude's
        self.last_suggest = 0.0  # when Claude's last own suggestion was passed on, so it doesn't nag
        self.asking = None  # the question waiting for your answer, for the dashboard
        self.late_yes = None  # (until, fn): an offer that got no answer in time; a "yes" before `until` runs fn
        self._private = False  # this request is private: what's said in it isn't passed on to Claude

    def state(self, name):
        if self.ui:
            self.ui.set_state(name)

    def heard(self, text, outcome):
        """Show what speech recognition heard (and what JARVIS did with it) in the chat panel and the log."""
        print("  [heard] %s -> %s" % (text and repr(text), outcome))
        if self.show_hearing:
            self.chat("heard", ("heard \u201c%s\u201d  \u2192 %s" % (text, outcome)) if text else "\u2026 %s" % outcome)

    def claude_says(self, text):
        """Say Claude's own answer; the chat panel shows it as Claude's rather than JARVIS's."""
        self._claude_saying = True
        try:
            self.speaker.say(text)
        finally:
            self._claude_saying = False

    def _said(self, text):
        self.chat("claude" if self._claude_saying else "jarvis", text)
        if self._private:
            return
        self.recent.append("JARVIS said: %s" % text)
        self.talk.append("JARVIS said: %s" % text)
        self.transcript.append("JARVIS: %s" % text)

    def keep_private(self):
        """The request being handled is private (a secret, a private note): it and what JARVIS says about it
        are left out of the lines Claude sees next time, and out of "where was I"."""
        self._private = True
        for log in (self.recent, self.talk, self.transcript):
            lines = list(log)
            last = max((i for i, line in enumerate(lines) if line.startswith("User: ")), default=None)
            for _ in range(len(lines) - last if last is not None else 0):
                log.pop()
        self.skills.where_was_i.unlog()

    def chat(self, who, text):
        if self.ui:
            self.ui.add_chat(who, text)

    def load(self):
        """(Re)load the YAML files. Speech settings need a restart."""
        with open(os.path.join(CONFIG, "settings.yaml")) as f:
            self.settings = yaml.safe_load(f) or {}
        self.name = self.settings.get("assistant_name", "Jarvis")
        self.names = [n.lower() for n in self.settings.get("names") or [self.name]]
        self.safety = Safety(os.path.join(CONFIG, "safety.yaml"))
        self.registry = Registry(os.path.join(CONFIG, "commands.yaml"))
        self.apps = Apps()  # everything in the app menu
        self.intents = None  # meaning matcher: other wordings of known phrases, before Claude
        try:
            cfg = self.settings.get("intents") or {}
            self.intents = IntentMatcher(os.path.join(CONFIG, "intents.yaml"), cfg.get("threshold", 0.72),
                                         extra=dict(self.apps.intents(), **command_phrases(self.registry)),
                                         learned=learned.load()["wordings"])
        except Exception as e:
            print("[intent] meaning matcher off: %r" % e)
        self.chat_replies = None  # its own replies to chat ("do you have any pets?"), before Claude
        cfg = self.settings.get("chat_replies") or {}  # enabled: false turns them off; threshold: how close
        if self.intents and cfg.get("enabled", True):
            try:
                self.chat_replies = ChatReplies(os.path.join(CONFIG, "chat.yaml"), self.intents.embed,
                                                cfg.get("threshold", 0.8))
            except Exception as e:
                print("[chat] chat replies off: %r" % e)
        self.browser = Browser(self.registry)
        self.claude = Claude(self.settings.get("claude") or {}, self.name, self.settings.get("user_name") or "the user",
                             self._abilities())
        if hasattr(self, "executor"):
            self.executor.safety = self.safety

    def ask(self, question):
        """Ask a yes/no question; the answer can be typed or spoken."""
        return bool(YES.match(self._answer(question) or ""))

    def choose(self, question, options):
        """Ask "X, or Y?": the option picked ("the first", "Y", "pause"), or None for "neither"/no answer."""
        answer = (self._answer(question) or "").lower().strip(" .!?")
        if not answer or re.match(r"^(?:no|nope|neither|none|nothing|never ?mind|cancel)\b", answer):
            return None
        for words in (("first", "second", "third"), ("one", "two", "three")):  # "the second one": second, not one
            hit = [i for i, w in enumerate(words) if re.search(r"\b%s\b" % w, answer) and i < len(options)]
            if len(hit) == 1:
                return options[hit[0]]
        said = set(re.findall(r"[a-z']+", answer))
        overlap = [len(said & set(re.findall(r"[a-z']+", o.lower()))) for o in options]
        best = max(overlap)
        return options[overlap.index(best)] if best and overlap.count(best) == 1 else None

    def _answer(self, question):
        """Say `question` and wait for the typed or spoken answer (None if none came)."""
        self.asking = question if isinstance(question, str) else question[0]
        self.speaker.say(question)
        self.follow_up()  # "yes" without saying the name first
        timeout = self.settings.get("confirm_timeout", 8)
        sys.stdout.write("  [yes/no, typed or spoken] (%ds) > " % timeout)
        sys.stdout.flush()
        deferred = []  # reminders and heads-ups that arrive mid-question wait until it's answered
        deadline = self.speaker.done_at() + timeout
        try:
            while True:
                # Counted from when the question has been said: its real audio can outlast the first guess.
                deadline = max(deadline, self.speaker.done_at() + timeout)
                left = deadline - time.time()
                if left <= 0:
                    raise queue.Empty
                try:
                    kind, answer = self.inbox.get(timeout=min(left, 0.5))
                except queue.Empty:
                    continue
                if kind not in ("say", "offer", "task"):  # (a task's next step waits for the answer too)
                    break
                deferred.append((kind, answer))
        except queue.Empty:
            print()
            return None
        finally:
            self.asking = None
            for item in deferred:
                self.inbox.put(item)
        if kind in ("voice", "follow"):
            print(answer)
        if kind in ("text", "voice", "follow"):
            self.chat("you", answer)
            return answer
        return None

    def handle(self, raw, follow_up=False):
        """follow_up: said without the name after an answer; then only a clear request goes to Claude.
        Sets self.ignored when it was treated as chatter."""
        self.ignored = False
        raw = raw.strip()
        if not raw:
            return True
        if raw.startswith("/"):
            return self._slash(raw)

        self.memory.seen()
        self._private = False
        text = clean(raw, self.names)
        if not text:
            self.speaker.say(["Yes?", "I'm listening.", "Yeah, what's up?", "Go ahead."])
            return True

        late, self.late_yes = self.late_yes, None
        if late and time.time() < late[0] and YES.match(text) and len(text.split()) <= 3:
            self.heard(text, "a late yes to the question before")
            late[1]()
            return True

        self.recent.append("User: %s" % text)
        self.talk.append("User: %s" % text)
        self.transcript.append("User: %s" % text)
        low = text.lower()
        if undo.UNDO.match(text):
            return self._undo()
        phrase = None if self._rewording else self.context.follow_up(text)
        if phrase:  # "a bit more", "make it 70", "and firefox too": about what JARVIS just did
            self.heard(text, "means \"%s\"" % phrase)
            return self._reword(phrase)
        if not self._rewording and not self.from_claude and sounds_like_follow_up(text):
            self.heard(text, "a follow-up, but there's nothing recent to apply it to")
            self.speaker.say(["Sorry, I've lost track. What should I do?", "Hmm, more of what?"]
                             if context.MORE.match(text) or context.LESS.match(text)
                             else "Sorry, I've lost track. What should I do?")
            return True
        self._text = text  # what's being handled (the rewritten phrase, when one was), for self.context
        if low in CHAT_PHRASES:
            return self._slash(CHAT_PHRASES[low])
        phrase = self.intents.early(text) if self.intents and not self._rewording else None
        if phrase:  # a listed other wording, word for word: before any pattern can misread it
            self.heard(text, "means \"%s\"" % phrase)
            self.handled(text, "wording", phrase)
            return self._reword(phrase)
        # Before the skills: "close Jarvis" isn't an app, nor a project. Claude's "do" can't close JARVIS.
        about_me = None if self.from_claude else self._about_me(text)
        if about_me is not None:
            return about_me
        if low in OPEN_CLAUDE:  # before the skills: "open claude in vs code" isn't a project
            if self.claude.open_terminal():
                self.speaker.say(["Claude's already open, here it is.", "Here's Claude."])
            else:
                self.speaker.say(["Sure, opening Claude.", "Okay, bringing up Claude."])
            return True
        if low in OPEN_CLAUDE_VSCODE:
            open_in_vscode()
            self.speaker.say(["Sure, opening Claude in VS Code.", "Okay, Claude's coming up in VS Code."])
            return True
        m = ASK_CLAUDE_VSCODE.match(text)
        if m:
            open_in_vscode(m.group(1))
            self.speaker.say(["Done, I've passed that to Claude in VS Code.", "Okay, Claude in VS Code has it."])
            return True
        if self.skills.first(text) or self._personal(text):
            return True
        if self.skills.handle(text):
            self.context.done(text)
            return True
        self.last_request = text

        # 1. Other wording with a value ("put milk on my to-do list"), 2. exact command, 3. an installed app,
        # 4. browser phrase, 5. same meaning in other words, 6. close-enough spelling, 7. Claude.
        phrase = self.intents.fill(text) if self.intents and not self._rewording else None
        if phrase:
            self.heard(text, "means \"%s\"" % phrase)
            return self._reword(phrase)
        if self._close_app(text):  # before the command list, whose "close {app}" can't find real process names
            return True
        match = self.registry.match_exact(text)
        if match and not self._not_running(match):
            self.handled(text, "command", (match.command.get("say") or [""])[0], value=bool(match.slots))
            return self._run_match(match)
        if self._open_app(text):
            return True
        action = self.browser.parse(text)
        if action:
            if action.browser:
                run = self.registry.browsers[action.browser]
                self.last_app = run.get("process") or os.path.basename(run["run"].split()[0])
            self.executor.run(action.shell(self.registry.browsers), reply=[p % action.description for p in ("Opening %s", "Sure, opening %s", "Okay, pulling up %s")])
            return True
        # "Open YouTube and turn the volume down": two requests, if each part is understood on its own.
        # (Before the meaning match, which could take the whole sentence as just one of them.)
        parts = self._split(text)
        if parts:
            self.heard(text, "%d requests: %s" % (len(parts), " / ".join(parts)))
            self._splitting = True
            try:
                for part in parts:
                    self.handle(part)
            finally:
                self._splitting = False
            return True
        # Same meaning, other words: rewritten to a phrase JARVIS knows, then handled again.
        phrase, score = self.intents.match(text) if self.intents and not self._rewording else (None, 0)
        if phrase:
            self.heard(text, "means \"%s\" (%.2f)" % (phrase, score))
            self.handled(text, "meaning", phrase, score=score)
            return self._reword(phrase)
        match = self.registry.match_fuzzy(text)
        if match:
            self.handled(text, "fuzzy", (match.command.get("say") or [""])[0], value=bool(match.slots))
            return self._run_match(match)
        # Chat that isn't a request ("do you have any pets?"): its own reply, no Claude needed.
        reply = None if self.from_claude or not self.chat_replies else self.chat_replies.reply(
            text, self.settings.get("user_name"), self.name)
        if reply:
            self.heard(text, "chat (%s %.2f)" % self.chat_replies.last)
            self.speaker.say(reply)
            return True

        # In a conversation (a follow-up, no name needed) everything you say is meant for JARVIS, so it
        # goes through like any request. Only empty noise ("uh", a cough) is dropped, quietly.
        if follow_up and not worth_asking(text, self.names):
            self.heard(text, "ignored (just noise)")
            self.recent.append("JARVIS ignored that (just noise)")
            self.ignored = True
            return True
        if self.from_claude:
            self.do_missed = True  # (so it isn't learned as a wording)
            self.heard(text, "Claude's \"do\" phrase matched nothing; skipped")
            self._miss(text, "claude-do")
            return True
        if not worth_asking(text, self.names):
            self.heard(text, "ignored (too short or unclear to ask Claude)")
            self._miss(text, "unclear")
            self.recent.append("JARVIS ignored that (too short or unclear)")
            self.speaker.say(["Sorry, I didn't catch that.", "Sorry, could you say that again?"])
            return True
        if self._unsure(text):
            return True
        if self.skills.notes.answer(text):  # "where did I park": a note answers it, no Claude needed
            return True
        if self.skills.lookup.answer(text):  # "who is Alan Turing": Wikipedia answers it, no Claude needed
            return True
        self._miss(text, "claude" if self.mode != "sys" else "unknown")
        if self.mode == "sys":
            self.speaker.say(["Sorry, that one's not on my list, and I can't ask Claude in system mode.",
                              "Hmm, I don't know that one yet. Switch to agent mode and I can ask Claude."])
        else:
            self._ask_claude(text)
        return True

    def handled(self, text, how, phrase="", skill="", score=0.0, value=False):
        """Record a request JARVIS handled (core/outcomes.py), so teaching knows what you lean on and which of its
        matches only just got there. What was said is only kept when it could be taught from: no value in it, no
        number or secret, not a private request. `teaching.log: false` turns it off."""
        if self._rewording or self.from_claude or self._private \
                or not (self.settings.get("teaching") or {}).get("log", True):
            return
        if value or not teaching.fit_to_send(text):
            text = ""
        elif how == "pattern":
            phrase = text.lower().strip()  # no value in it: what you said is itself a phrase JARVIS knows
        outcomes.log(how, text, "" if "{" in phrase else phrase, skill, score)

    def _miss(self, text, outcome):
        guess, score = self.intents.nearest if self.intents else (None, 0.0)
        misslog.log(text, outcome, guess, score)

    def _personal(self, text):
        """Small talk, reminders, "do that again" and friends: answered instantly, without Claude."""
        user = self.settings.get("user_name")
        if persona.AGAIN.match(text):
            if self.last_request:
                return self.handle(self.last_request)
            self.speaker.say("Hmm, I haven't done anything yet.")
        elif persona.WHAT_DID_YOU_SAY.match(text):
            self.speaker.say(self.speaker.last or "I haven't said anything yet.")
        elif persona.CLOSE_IT.match(text):
            if self.ui and self.ui.card_visible():
                self.ui.close_card()  # the card on screen is the likeliest "it"
            elif self.last_app:
                return self.handle("close %s" % self.last_app)
            self.speaker.say("Sorry, I'm not sure what to close. Tell me the app's name.")
        else:
            # Reminders live in the day tracker now (skills/todos.py).
            reply = persona.small_talk(text, user, self.name, self.names)
            if not reply:
                return False
            self.speaker.say(reply)
        return True

    def offer(self, name):
        """Offer a command you usually run around this time of day."""
        cmd = next((c for c in self.registry.commands if c["name"] == name), None)
        if not (cmd and cmd.get("say")):
            return
        if self.ask("You usually %s around now. Want me to?" % cmd["say"][0]):
            self.handle(cmd["say"][0])
        else:
            self.speaker.say(["No problem.", "Okay, maybe later."])

    def _ask_claude(self, text, image=None):
        recent = list(self.recent)[:-1]  # the last line is this request itself
        print("  [claude] not in the command list, asking Claude (%s)..." % self.claude.model)
        self.chat("sys", "Asking Claude (%s)..." % self.claude.model)
        self.state("claude")
        if not image:
            self.speaker.say("Asking Claude.")  # always the same, so you know Claude is answering
        # If Claude is slow, say something now and then so the silence doesn't feel broken.
        fillers = [threading.Timer(delay, self.speaker.say, [lines]) for delay, lines in (
            (6, ["Still on it.", "Almost there.", "Just a moment more."]),
            (16, ["This one's taking a bit longer, hang on.", "Still thinking, sorry for the wait."]))]
        for timer in fillers:
            timer.daemon = True
            timer.start()
        def speak_early(line):
            # The spoken part streamed in first: say it now, while Claude finishes the rest.
            for timer in fillers:
                timer.cancel()
            print("  [claude] answered:")
            self.claude_says(line)

        try:
            known = " ".join(k for k in (self._focus_line(), self.projects.context(text),
                                         self.skills.notes.for_claude(text)) if k)
            answer = self.claude.ask(text + ("\n\n[%s]" % known if known else ""), recent, on_say=speak_early,
                                     image=image, about=self._about())
        except ClaudeError as e:
            self.speaker.say("Sorry, I ran into a problem: %s." % str(e).rstrip("."))
            return
        finally:
            self.recent.clear()  # Claude has seen it now
            for timer in fillers:
                timer.cancel()
            self.state("thinking")
        if answer["say"]:
            if not answer["spoken"]:
                print("  [claude] answered:")
                self.claude_says(answer["say"])
            self.recent.clear()  # Claude knows its own answer; what happens next (e.g. "Done.") is new to it
        if not answer["steps"] and answer["run_now"] and " && " in answer["command"]:
            # Claude chained commands anyway: run them as separate steps, so each can be checked and stopped.
            answer["steps"] = [{"command": part.strip()} for part in answer["command"].split(" && ")]
            answer["command"] = ""
        dos = (answer["do"] if isinstance(answer["do"], list) else [])[:5]
        # Several actions: one task, each step checked and the whole thing on a card (skills/tasks.py).
        plan = answer["task"] if isinstance(answer["task"], dict) else {"steps": answer["task"]} if isinstance(
            answer["task"], list) else {}
        steps = [{"do": str(d)} for d in dos] + list(plan.get("steps") or []) + list(answer["steps"] or [])
        if len(steps) > 1 or plan.get("steps"):
            dos = []
        for phrase in dos:
            self.chat("sys", "do: %s" % phrase)
            self.from_claude = True
            self.do_missed = False
            try:
                self.handle(str(phrase))
            finally:
                self.from_claude = False
        if len(dos) == 1 and not self.do_missed:
            self._learn(text, str(dos[0]))
        if answer["details"]:
            print("  " + str(answer["details"]).replace("\n", "\n  "))
            self.chat("claude", str(answer["details"]))
        command = answer["command"]
        if command:
            print("  command: %s" % command)
            self.chat("sys", "command: %s" % command)
            if answer["run_now"]:
                out = self.executor.run(command, reply=["Done.", "All done.", "There you go."],
                                        output=bool(answer["output"]),
                                        question=["Want me to run it?", "Should I go ahead and run it?"],
                                        on_output=lambda o: o if 0 < len(o) <= 120 else "")
                if out and len(out) > 120:
                    print("  " + out.replace("\n", "\n  "))
                    self._read_out(text, command, out)
                elif out:
                    self.recent.append("Command output: %s" % out)
        if answer["save"]:
            self._save(answer["save"], answer["command"])
        if answer["remember"]:
            self.skills.notes.offer(answer["remember"])
        if answer["suggest"]:
            self._suggest(answer["suggest"])
        if isinstance(answer["show"], dict):
            self.show(answer["show"])
        misheard = answer["misheard"]
        if isinstance(misheard, dict) and misheard.get("meant"):
            self.chat("sys", "misheard: \u201c%s\u201d, probably \u201c%s\u201d" % (misheard.get("heard") or text,
                                                                               misheard["meant"]))
        if answer["fix"]:
            # Fix straight away only if you asked for a change; a complaint gets asked about first.
            if FIX_ASKED.search(text) or self.ask(["Want me to get that fixed?", "Shall I have Claude fix that?"]):
                self._fix_jarvis(answer["fix"])
            else:
                self.speaker.say(["Okay, I'll leave it as it is.", "Sure, no fix for now."])
        if not dos and steps:  # last: its card goes over anything shown above, and its steps take a while
            self.skills.tasks.start(text, dict(plan, steps=steps))

    def _about_me(self, text):
        """Close or restart JARVIS, or have Claude Code change it: what handle() returns (False ends JARVIS),
        or None when the request isn't about JARVIS itself."""
        me = _names_regex(self.names)
        if re.match(QUIT_ME % me, text, re.IGNORECASE):
            if self.ask(["Close me? You'll have to start me again from the app menu.", "Shut me down completely?"]):
                return False
            self.speaker.say(["Okay, I'll stay.", "Sure, I'm staying."])
            return True
        if re.match(RESTART_ME % me, text, re.IGNORECASE):
            self.exit_code = RESTART_CODE
            return False
        m = ASK_CLAUDE_ME.search(text)
        if not m or not re.search(MENTIONS_ME % me, text, re.IGNORECASE):
            return None
        task = m.group(2).strip(" ,.")
        said = list(self.talk)[:-1]  # the last line is this request itself
        if POINTS_BACK.search(task) and said:
            task += "\n\nWhat was said with JARVIS just before this:\n" + "\n".join(said)
        self._fix_jarvis({"task": task, "size": "big" if m.group(1) else "small"})
        return True

    def _fix_jarvis(self, fix):
        """You asked for a change to JARVIS itself: Claude Code does it in this project, in a terminal for
        a small fix or in VS Code for a big one. Edits still go through the safety hook."""
        if not isinstance(fix, dict) or not str(fix.get("task") or "").strip():
            return
        task = FIX_PROMPT % str(fix["task"]).strip()
        self.chat("sys", "fix for %s (%s): %s" % (self.name, fix.get("size") or "small", fix["task"]))
        if fix.get("size") == "big":
            self.executor.run("code %s" % shlex.quote(HERE), reply="", window=True)
            threading.Timer(3, open_in_vscode, [task]).start()  # give VS Code time to open the project first
            self.speaker.say("That's a bigger job, so I've opened my project in VS Code with Claude on it.")
        else:
            self.claude.open_terminal(cwd=HERE, prompt=task,
                                      model=(self.settings.get("claude") or {}).get("fix_model"))
            self.speaker.say("Claude's fixing that in a terminal. Restart me when it's done.")

    def _abilities(self):
        """What JARVIS itself understands, for Claude's "do" (built into its system prompt)."""
        reg = self.registry
        searchable = [site for site, info in reg.sites.items() if info.get("search")]
        saved = [c["say"][0] for c in reg.commands if c.get("say")]
        return "\n  ".join(line for line in (
            *registry.abilities(),
            *ABILITIES,
            "sites: \"open <site>\" (several: \"open gmail and youtube\"), add \"in chrome\"/\"in firefox\": %s"
            % ", ".join(reg.sites),
            "search: \"search <site> for <thing>\" on %s; \"google <thing>\"" % ", ".join(searchable),
            "tab groups: \"open my <group> tabs\": %s" % ", ".join(reg.tab_groups) if reg.tab_groups else "",
            "saved commands (exact phrases): %s" % "; ".join(saved),
        ) if line)

    def _focus_line(self):
        """Which window the user is looking at, and its project, so "push" or "run the tests" means that one."""
        win = desktop.active_window()
        if win and "jarvis" in win.wm_class:
            win = next((w for w in reversed(desktop.windows()) if "jarvis" not in w.wm_class and w.title), None)
        p = self.projects.focused()
        if not (win or p):
            return ""
        line = "Focused window: %s (%s)." % (win.title or "untitled", win.wm_class.split()[-1]) if win else ""
        if p:
            line += " Its project: %s, folder %s. Run git and other commands there (git -C <folder>), not in %s." % (
                p.name, p.path, WORKSPACE)
        return line

    def _about(self):
        """The line that tells Claude who is asking, how, and how much it's been used today."""
        user = self.settings.get("user_name") or "the user"
        how = "typed" if self.source == "text" else "by voice"
        uses = sorted((self.memory.data.get("uses") or {}).items(), key=lambda kv: -kv[1])[:5]
        usage = self.claude.usage_today()
        parts = ["%s asking for %s, %s" % (self.name, user, how), time.strftime("%a %-d %b, %-I:%M %p")]
        if uses:
            parts.append("%s's most used commands: %s" % (user, ", ".join("%s (%d)" % kv for kv in uses)))
        parts.append("your use by %s today: %d questions, ~%dk tokens" % (
            self.name, usage["questions"], round(usage["tokens"] / 1000)))
        answers = self.memory.pref("answers")
        if answers == "brief":
            parts.append("%s wants brief answers: \"say\" is one short sentence" % user)
        elif answers == "detailed":
            parts.append("%s wants detailed answers: \"say\" can be 3-4 sentences, the rest in \"details\"" % user)
        if self.speaker.muted:
            parts.append("%s reads your answers in the chat instead of hearing them" % user)
        return " | ".join(parts)

    def _mute(self, arg):
        """/mute [voice|mic|sound] [on|off]: one thing at a time, one direction ("on" means muted). With no
        argument it toggles my own voice. The tray icon uses this, so clicking a box never asks a question
        you would have to answer out loud (and a muted mic couldn't hear the answer)."""
        words = arg.lower().split()
        what = next((MUTE_WHAT[w] for w in words if w in MUTE_WHAT), "voice")
        on = {"on": True, "off": False, "yes": True, "no": False}.get(
            next((w for w in words if w in ("on", "off", "yes", "no")), ""))
        if what == "voice":
            self.skills.voice_prefs.quiet(not self.speaker.muted if on is None else on)
        elif what == "mic":
            self.skills.audio.mic_mute(not self.mutes["mic"] if on is None else on, ask=False)
        else:
            self.skills.audio.sound_mute(not self.mutes["speaker"] if on is None else on)

    def show_mutes(self, _asked=True, **changed):
        """Remember what's muted (voice / mic / speaker) and put it on the tray icon. _asked=False when this is
        only what the system reports, not something you asked me for: health.py may still warn about those."""
        for key, value in changed.items():
            self.mutes[key] = bool(value)
            if not value:
                self.muted_on_purpose.discard(key)
            elif _asked:
                self.muted_on_purpose.add(key)
        if self.ui:
            self.ui.set_mutes(**self.mutes)

    def control(self, msg):
        """A message from tools/jarvis (the Claude terminal, a fix task, VS Code): show a card or do a phrase."""
        if isinstance(msg.get("show"), dict):
            self.show(msg["show"])
        elif msg.get("say"):  # e.g. a Claude task terminal finishing, with why for "why did you say that"
            self.skills.proactive.note("claude", str(msg["say"]), str(msg.get("why") or "Claude Code sent it."))
            self.speaker.say(str(msg["say"]))
        elif msg.get("do"):
            phrase = str(msg["do"])
            self.chat("sys", "Claude asked: %s" % phrase)
            reply = str(msg.get("reply") or "")
            if reply:  # `jarvis do --wait`: done and checked like a task step, the result written back
                result = self.skills.tasks.run_one(phrase)
                if os.path.dirname(os.path.abspath(reply)) == os.path.join(RUN_DIR, "replies"):
                    try:
                        with open(reply, "w") as f:
                            json.dump(result, f)
                    except OSError:
                        pass
                return
            if not self.skills.tasks.gate(phrase):  # nothing about JARVIS itself; risky ones asked about
                return
            self.from_claude = True  # never bounce it back to Claude
            try:
                self.handle(phrase)
            finally:
                self.from_claude = False

    def show(self, card):
        """Put a card on screen: {"title", "text" (markdown), "image", "choices", "id", "refresh"} (see ui/card.py)."""
        if not (card.get("text") or card.get("image") or card.get("choices")):
            return
        if self.ui:
            if card.get("refresh"):
                self.cards.live(card)  # shows it, then keeps it up to date
            else:
                self.ui.show_card(card)
        else:
            print("  [card] %s\n  %s" % (card.get("title") or "", str(card.get("text") or "").replace("\n", "\n  ")))

    def _read_out(self, request, command, out):
        """Long output: Claude picks out what you asked for and says it, instead of "it's on your screen"."""
        self.chat("sys", out[-1500:])
        self.state("claude")
        user = self.settings.get("user_name") or "the user"
        try:
            answer = self.claude.ask(
                "Your command ran: %s\nIts output:\n%s\n\nIn \"say\", tell %s what they asked for (\"%s\") from "
                "this output, with the actual numbers or names. If a small table or list would help them see it, "
                "put it in \"show\". Leave every other field empty or null."
                % (command, out[-3000:], user, request), about=self._about())
        except ClaudeError:
            self.speaker.say(["That's a long one, so I've put it on screen.", "Here you go, it's on your screen."])
            return
        finally:
            self.recent.clear()
            self.state("thinking")
        if answer["say"]:
            self.claude_says(answer["say"])
        else:
            self.speaker.say("It's on your screen.")
        self.show(answer["show"] if isinstance(answer["show"], dict) else
                  {"title": command, "text": "```\n%s\n```" % out[-3000:]})
        if answer["details"]:
            self.chat("claude", str(answer["details"]))

    def _suggest(self, suggest):
        """Pass on Claude's own recommendation: a tip, or a yes/no offer that runs a command or fixes JARVIS."""
        if not isinstance(suggest, dict) or not str(suggest.get("say") or "").strip():
            return
        gap = (self.settings.get("claude") or {}).get("suggest_every_minutes", 10) * 60
        if time.time() - self.last_suggest < gap or self.skills.proactive.holding():
            return
        self.last_suggest = time.time()
        say, command = str(suggest["say"]).strip(), str(suggest.get("command") or "").strip()
        self.skills.proactive.note("suggestion", say, "Claude suggested it while answering “%s”. I pass on "
                                                      "at most one suggestion every %d minutes." % (self._text, gap // 60))
        fix = suggest.get("fix") if isinstance(suggest.get("fix"), dict) else None
        self.chat("sys", "suggestion: %s%s" % (say, "  (command: %s)" % command if command else
                                                "  (fix: %s)" % fix.get("task") if fix else ""))
        if fix:
            if self.ask(say):
                self._fix_jarvis(fix)
            else:
                self.speaker.say(["Okay, I'll leave it as it is.", "Sure, no fix for now."])
        elif not command:
            self.claude_says(say)
        elif self.ask(say):
            self.executor.run(command, reply=["Done.", "There you go."], output=True)
        else:
            self.speaker.say(["Okay, no worries.", "Sure, never mind."])

    def _save(self, save, ran=""):
        """Offer to add Claude's suggested shortcut to commands.yaml; it's only saved if you say yes."""
        try:
            name = re.sub(r"\W+", "_", str(save["name"]).lower()).strip("_")
            phrases = [str(p).lower().strip() for p in save["phrases"] if str(p).strip()]
            run = str(save["run"]).strip()
        except (KeyError, TypeError):
            return
        slots_in_run = set(re.findall(r"\{(\w+)\}", run))
        if not (name and phrases and run) or any(not slots_in_run <= set(re.findall(r"\{(\w+)\}", p))
                                                 for p in phrases):
            return
        if self.registry.has_name(name) or any(self.registry.match_exact(p) for p in phrases if "{" not in p):
            return
        if self.safety.classify(re.sub(r"\{(\w+)\}", r"\1", run))[0] == MANUAL:
            return  # JARVIS could never run it itself, so it isn't worth a shortcut.
        print("  save as: %s  (say: %s)  run: %s" % (name, " | ".join(phrases), run))
        # Asked with the values from the command that just ran: "check port 8178", not "check port something".
        parts = re.split(r"\{(\w+)\}", run)
        m = re.match("^%s$" % "".join(re.escape(x) if i % 2 == 0 else "(?P<%s>.+?)" % x
                                      for i, x in enumerate(parts)), ran.strip()) if ran and len(parts) > 1 else None
        values = m.groupdict() if m else {}
        phrase = re.sub(r"\{(\w+)\}", lambda v: values.get(v.group(1)) or "something", phrases[0])
        self.chat("sys", "suggested command %s: %s\nrun: %s" % (name, " | ".join(phrases), run))
        answer = self._answer('Want me to remember "%s" for next time?' % phrase)
        if answer is None:  # no answer in time (still typing, say): a "yes" in the next minute still saves it
            self.late_yes = (time.time() + 60, lambda: self._keep(save, name, phrases, run, phrase))
            self.speaker.say(["I didn't get an answer, so it's not saved yet. Say yes if you want it.",
                              "No answer, so I haven't saved it. Just say yes if you'd like it."])
            return
        if not YES.match(answer):
            self.speaker.say(["Okay, I won't save it.", "No problem, not saved."])
            return
        self._keep(save, name, phrases, run, phrase)

    def _keep(self, save, name, phrases, run, phrase):
        """Save the shortcut _save offered, once you've said yes."""
        self.registry.add(name, phrases, run, str(save.get("reply") or "Done"), bool(save.get("output")))
        if self.intents:  # other wordings of it work at once, not only after a restart
            for p in phrases:
                if "{" not in p:
                    self.intents.learn(p, phrases[0] if "{" not in phrases[0] else p)
        self.chat("sys", "saved command %s: %s" % (name, " | ".join(phrases)))
        self.speaker.say(['I\'ve saved that. Next time just say "%s".' % phrase,
                          'Saved. Just say "%s" next time.' % phrase])

    def _run_match(self, match):
        cmd = match.command
        uses = self.memory.used(match.name, habit=bool(cmd.get("window")) and not match.slots)
        reply = match.reply()
        if uses > 5 and not cmd.get("output") and random.random() < 0.6:
            reply = ["Done.", "Got it.", "Okay."]  # You know this one by now; no need to spell it out.
        if cmd.get("window"):
            self.last_app = cmd.get("process", os.path.basename(cmd["run"].split()[0])) or None
        self.executor.run(match.shell(), reply=reply, output=cmd.get("output", False),
                          question=match.fill(cmd.get("ask")),
                          declared=cmd.get("risk"), window=cmd.get("window", False),
                          on_output=lambda out: match.reply(out))
        if self.executor.ok:
            self.last_undo = undo.opposite(match.name)
            values = [v for v in match.slots.values() if v]
            self.context.done(self._text, values[0] if len(values) == 1 else None)
        return True

    def vocabulary(self):
        """Words to help speech recognition with commands: JARVIS's own, your projects, unusual app names."""
        try:
            with open("/usr/share/dict/words") as f:
                common = {w.strip().lower() for w in f}
        except OSError:
            common = set()
        # Short app names with a word Whisper wouldn't know. Not web apps (WhatsApp, Spotify): they're said
        # like sites, and "WhatsApp" in the hint turned "what's on my to-do list" into "Huatson my to-do list".
        apps = [a["name"] for a in self.apps.apps
                if a["process"] and len(a["name"].split()) <= 2 and not re.search(r"\d|[^\w\s-]", a["name"])
                and any(w not in common for w in re.findall(r"[a-z]+", a["name"].lower()))]
        return (["To-do list", "Reminder", "Timer", "Workspace", "Jetson Nano", "biztel", "Claude"]
                + [p.spoken for p in self.projects.items] + apps)

    def _unsure(self, text):
        """Nearly understood: ask instead of guessing, or of waiting ~4 s for Claude.
        Two meanings tied: "Pause the music, or mute?". A likely one: "Did you mean pause the music?".
        A yes also teaches the wording (unless learn: off)."""
        if not self.intents or self._rewording or self.from_claude:
            return False
        (best, score), (other, second) = (self.intents.candidates(text) + [(None, 0.0)] * 2)[:2]
        if not best or best.startswith("/") or OPEN_QUESTION.search(text) or len(text.split()) < 2:
            return False
        threshold = (self.settings.get("intents") or {}).get("threshold", 0.72)
        if second >= UNSURE and score - second < 0.06 and not other.startswith("/"):  # two close meanings
            phrase = self.choose("%s, or %s?" % (best[0].upper() + best[1:], other), [best, other])
            if phrase:
                self.context.chose([best, other], phrase)  # for "the other one"
        elif UNSURE <= score < threshold:  # one likely meaning (no close second: that was the case above)
            phrase = best if self.ask("Did you mean %s?" % best) else None
        else:
            return False
        if not phrase:
            return False  # neither: Claude, as before
        if self.settings.get("learn", "ask") != "off" and learned.worth_learning(text, phrase, learned.load()):
            learned.remember(text, phrase, why="you said yes")  # you just confirmed it: no need to ask again
            self.intents.learn(text, phrase)
        self.heard(text, "you meant \"%s\"" % phrase)
        self.handled(text, "confirmed", phrase)
        return self._reword(phrase)

    def _learn(self, said, phrase):
        """Claude turned `said` into a JARVIS phrase that worked: learn the wording, so next time needs no Claude.
        settings learn: ask (default; you said to ask before saving), quiet, or off."""
        mode = self.settings.get("learn", "ask")
        if mode == "off" or not self.intents or not learned.worth_learning(said, phrase, learned.load()):
            return
        if mode == "ask" and not self.ask("Should I remember that \u201c%s\u201d means %s?" % (said, phrase)):
            learned.decline(said)  # never asked about again
            return
        learned.remember(said, phrase, why="Claude did it for you")
        self.intents.learn(said, phrase)
        self.heard(said, "learned: means \"%s\"" % phrase)
        if mode == "ask":
            self.speaker.say(["Got it, I'll remember that.", "Okay, noted for next time."])

    def _split(self, text):
        """["open youtube", "turn the volume down"] for "open youtube and turn the volume down", or None.
        Only when every part is understood without Claude: "add bread and milk to my list" is one request."""
        if self._splitting or self._rewording or self.from_claude:
            return None
        parts = [p.strip(" ,.") for p in SPLIT.split(text) if p.strip(" ,.")]
        if not 2 <= len(parts) <= 4 or not all(self.understands(p) for p in parts):
            return None
        return parts

    def understands(self, text):
        """Would JARVIS handle `text` itself (no Claude)? Checks the same steps as handle(), running nothing."""
        t = text.strip().rstrip("?.!")
        low = t.lower()
        if low in CHAT_PHRASES or low in OPEN_CLAUDE or low in OPEN_CLAUDE_VSCODE or undo.UNDO.match(t):
            return True
        if self.intents and (self.intents.early(t) or self.intents.fill(t)):
            return True
        if any(pattern.match(t) for skill in self.skills.all for pattern, _ in skill.rules):
            return True
        match = self.registry.match_exact(t)
        if match and not self._not_running(match):
            return True
        if self.app_for(apps.OPEN, t)[0] or self.app_for(apps.CLOSE, t)[0]:
            return True
        if self.browser.parse(t):
            return True
        return bool(self.intents and self.intents.match(t)[0])

    def _reword(self, phrase):
        self._rewording = True  # one rewrite only: the new phrase isn't rewritten again
        try:
            return self.handle(phrase)
        finally:
            self._rewording = False

    @staticmethod
    def _not_running(match):
        """"kill the tunes" isn't "close {app}" unless a program by that name is running (else pkill "tunes")."""
        app = match.slots.get("app") if match.name == "close_app" else None
        return bool(app) and subprocess.call(["pgrep", "-x", app[:15]], stdout=subprocess.DEVNULL) != 0

    def app_for(self, pattern, text):
        """(installed app, the name as said) for "open X" / "close X", or (None, None)."""
        m = pattern.match(text)
        if not m or m.group(1).lower() in self.registry.sites:
            return None, None  # "open gmail" is the website (not Thunderbird, which "gmail" sounds like)
        return self.apps.find(m.group(1)), m.group(1)

    def _open_app(self, text):
        app, said = self.app_for(apps.OPEN, text)
        if not app:
            return False
        desktop.go_to_jarvis_workspace()
        before = verify.windows_now()
        if not self.apps.launch(app):
            verify.record("open %s" % app["name"], False, "it didn't start")
            self.speaker.say("Hmm, %s didn't start." % app["name"])
            return True
        verify.launched("open %s" % app["name"], None, before, True, self.executor.notify)
        self.last_app = app["process"] or None
        self.context.done(text, said)
        self.speaker.say(["Opening %s." % app["name"], "Sure, here's %s." % app["name"]])
        return True

    def _close_app(self, text):
        app, said = self.app_for(apps.CLOSE, text)
        if not app:
            return False
        if not app["process"]:
            self.speaker.say("%s runs inside the browser, so I'd close the whole browser. Switch to it and say "
                             "close this window instead." % app["name"])
            return True
        self.executor.run("pkill -x %s" % shlex.quote(app["process"]),
                          reply=["Okay, closed %s." % app["name"], "Done, %s is closed." % app["name"]],
                          question="Close %s? Anything unsaved will be lost." % app["name"], declared="confirm")
        if self.executor.ok:
            self.context.done(text, said)
        return True

    def _undo(self):
        if callable(self.last_undo):  # a skill's own way back (files moved, renamed, put in the Trash)
            reverse, self.last_undo = self.last_undo, None
            self.heard("undo", "reverses the last change")
            reverse()
            return True
        cmd = next((c for c in self.registry.commands if c["name"] == self.last_undo), None)
        if not cmd:
            self.speaker.say(["Sorry, I can't undo that one.", "Hmm, there's nothing I can undo right now."])
            return True
        self.last_undo = None
        self.heard("undo", "runs %s" % cmd["name"])
        return self._run_match(self.registry.match_exact(cmd["say"][0]))

    def _slash(self, raw):
        cmd, _, arg = raw.partition(" ")
        if cmd in ("/quit", "/exit"):
            return False
        if cmd in ("/agent", "/sys"):
            self.mode = cmd[1:]
            self.speaker.say("Agent mode. I'll ask Claude when I don't know something." if self.mode == "agent"
                             else "System mode. I'll stick to my command list.")
        elif cmd == "/list":
            for c in self.registry.commands:
                level, _ = self.safety.classify(c["run"].replace("{", "").replace("}", ""), c.get("risk"))
                print("  %-20s %-8s %s" % (c["name"], level, " | ".join(c.get("say") or [])))
            print("  sites: %s" % ", ".join(self.registry.sites))
            print("  tab groups: %s" % ", ".join(self.registry.tab_groups))
        elif cmd == "/reload":
            self.load()
            self.projects.refresh()
            self.speaker.say(["Okay, I've reloaded everything.", "All refreshed."])
        elif cmd == "/new":
            self.claude.new_session()
            self.speaker.say(["Sure, fresh start with Claude.", "Okay, starting a new conversation with Claude."])
        elif cmd == "/mute":
            self._mute(arg)
        elif cmd == "/hearing":
            on = {"on": True, "off": False}.get(arg.strip().lower())
            self.show_hearing = (not self.show_hearing) if on is None else on
            self.speaker.say("I'll show what I hear in the chat." if self.show_hearing
                             else "Okay, I'll stop showing what I hear.")
            if self.show_hearing and self.ui:
                self.ui.show_chat(True)
        elif cmd in ("/chat", "/orb"):
            on = {"on": True, "off": False}.get(arg.strip().lower())
            if not self.ui:
                print("  The UI isn't running.")
            elif cmd == "/chat":
                self.ui.show_chat(on)
            else:
                self.ui.show_orb(on)
        elif cmd == "/card":
            if not self.ui:
                print("  The UI isn't running.")
            elif arg.strip().lower() == "off":
                self.ui.close_card()
            else:
                self.ui.show_card()  # the last card again
        elif cmd == "/desk":
            desktop.go_to_jarvis_workspace()
        elif cmd == "/status":
            self.skills.status.status()
        elif cmd == "/stop":
            self.speaker.stop()
        elif cmd == "/check":
            level, reason = self.safety.classify(arg)
            print("  %s: %s" % (level, reason))
        else:
            print(HELP)
        return True


def read_keyboard(inbox):
    while True:
        try:
            inbox.put(("text", input()))
        except EOFError:
            inbox.put(("eof", ""))
            return


def start_voice(jarvis, whisper):
    """Load Whisper (on the GPU) and enable push-to-talk; runs in the background at startup."""
    whisper.set_names(jarvis.names)
    whisper.set_vocabulary(jarvis.vocabulary())
    if not whisper.start():
        print("[voice] Whisper isn't available yet; push-to-talk is off. Typing still works.")
        jarvis.rest = "off"
        jarvis.state("off")
        return

    def on_audio(pcm):
        jarvis.state("thinking")
        try:
            text = whisper.transcribe(pcm, command=True)
            jarvis.heard(text, "push-to-talk") if text else jarvis.heard("", "push-to-talk: couldn't make out words")
            jarvis.inbox.put(("voice", text))
        except Exception as e:
            print("[voice] transcription failed: %s" % e)
            jarvis.state(jarvis.rest)

    def on_listen(active):
        if active:
            jarvis.speaker.stop()
        jarvis.state("listening" if active else jarvis.rest)

    ptt_cfg = jarvis.settings.get("push_to_talk") or {}
    ptt = PushToTalk(ptt_cfg, on_audio, on_listen=on_listen)
    ptt.start()
    print("[voice] Push-to-talk ready: hold %s and speak." % ptt_cfg.get("key", "right_ctrl"))

    wake_cfg = jarvis.settings.get("wake_word") or {}
    busy = lambda: jarvis.speaker.busy() or ptt.recording  # noqa: E731
    if wake_cfg.get("enabled", True) and wake_cfg.get("engine", "whisper") == "whisper":
        def on_text(text, follow_up):
            jarvis.state("thinking")
            jarvis.inbox.put(("follow" if follow_up else "voice", text))
        wake = WhisperWake(jarvis.names, whisper, on_text, on_wake=on_listen, is_busy=busy, is_echo=jarvis.speaker.is_echo, on_heard=jarvis.heard,
                           follow_seconds=wake_cfg.get("follow_up_seconds", 8),
                           on_stop=lambda: (jarvis.speaker.stop(), jarvis.state(jarvis.rest)),
                           is_talking=jarvis.speaker.busy, barge_in=wake_cfg.get("barge_in", "any"),
                           is_deaf=lambda: jarvis.deaf)
        wake.start()
        jarvis.wake = wake
        jarvis.follow_up = wake.follow_up
        jarvis.keep_following = wake.keep_following
        print('[voice] Wake word ready: say any of: %s (optionally with "hey").' % ", ".join(jarvis.names))
    elif wake_cfg.get("enabled", True):
        phrases = [g + n for n in jarvis.names for g in ("", "hey ", "hello ", "ok ")]
        WakeWord(phrases, on_audio, on_wake=on_listen, is_busy=busy, is_deaf=lambda: jarvis.deaf,
                 threshold=wake_cfg.get("threshold", 0.10), boost=wake_cfg.get("boost", 2.0)).start()
        print('[voice] Wake word ready (keyword model): say any of: %s.' % ", ".join(jarvis.names))
    jarvis.rest = "idle"
    jarvis.state("idle")


def run_loop(jarvis, has_ui):
    """Handle typed, spoken and UI requests one at a time."""
    while True:
        sys.stdout.write("[%s] you> " % jarvis.mode)
        sys.stdout.flush()
        kind, line = jarvis.inbox.get()
        if kind == "quit" or (kind == "eof" and not has_ui):
            break
        if kind == "eof":
            continue  # No terminal attached; the UI keeps JARVIS running.
        if kind == "say":
            print()
            jarvis.speaker.say(line)
            continue
        if kind == "offer":
            print()
            jarvis.offer(line)
            continue
        if kind == "ctl":  # another Claude, via tools/jarvis
            print()
            jarvis.control(line)
            continue
        if kind == "task":  # the next step of a task (skills/tasks.py): one per turn, so a pause gets in between
            jarvis.state("thinking")
            jarvis.handling_since = time.time()
            try:
                jarvis.skills.tasks.step(line)
            except Exception as e:  # a broken step must never take JARVIS down
                print("[task] step failed: %r" % e)
            finally:
                jarvis.handling_since = None
                jarvis.state(jarvis.rest)
            continue
        if kind == "follow":
            print(line)
        if kind == "voice":
            print(line or "(didn't catch that: the recording was silent or unclear)")
            if not line:
                jarvis.speaker.say(["Sorry, I didn't catch that.", "Sorry, could you say that again?",
                                    "Hmm, I missed that. One more time?"])
                jarvis.state(jarvis.rest)
                jarvis.follow_up()
                continue
        if not line.startswith("/"):
            jarvis.chat("you", line)
        jarvis.state("thinking")
        jarvis.handling_since = time.time()  # for the watchdog
        try:
            jarvis.source = kind
            if not jarvis.handle(line, follow_up=kind == "follow"):
                break
        finally:
            jarvis.handling_since = None
            jarvis.state(jarvis.rest)
        if kind == "voice" or (kind == "follow" and not jarvis.ignored):
            jarvis.follow_up()  # "what's the weather?" next, without the name
        elif kind == "follow":
            jarvis.keep_following()  # it was noise: the rest of the window is still yours


def teach(jarvis):
    """Background: teaches itself other ways of saying things (core/teaching.py), without asking.

    Held in focus mode and quiet hours, and while JARVIS is talking; skipped in system mode (no Claude) and with
    learn: off. The time of the last pass is kept, so restarting JARVIS doesn't mean asking Claude again."""
    cfg = jarvis.settings.get("teaching") or {}
    if not cfg.get("enabled", True):
        return
    time.sleep(max(30, cfg.get("after", 300)))
    while True:
        wait = teaching.due_in(cfg)
        if wait or jarvis.settings.get("learn", "ask") == "off" or jarvis.mode == "sys" \
                or jarvis.speaker.busy() or jarvis.skills.proactive.holding():
            time.sleep(min(wait or 600, 600))
            continue
        try:
            line = teaching.run(jarvis, cfg)
            if line:
                print("[teaching] %s" % line)
        except ClaudeError as e:
            print("[teaching] %s" % e)
        except Exception as e:  # a wording must never take JARVIS down
            print("[teaching] failed: %r" % e)


def watch(jarvis):
    """Background: the notification reader; reminders when they're due; crashed projects; the morning briefing;
    every 2 minutes, heads-ups and habit offers (skills/proactive.py keeps why each was said, and holds them in focus
    mode and quiet hours)."""
    tell = jarvis.skills.proactive
    tick = 0
    jarvis.skills.notifications.watch()
    while True:
        time.sleep(10)
        tick += 1
        try:
            jarvis.skills.notifications.watch()  # restarts the notification reader if it stopped
            for reminder in jarvis.memory.due_reminders():
                line = persona.reminder_line(reminder, jarvis.settings.get("user_name"))
                tell.note("reminder", line, "You asked me to remind you, for %s." % time.strftime(
                    "%H:%M", time.localtime(reminder["due"])))
                jarvis.inbox.put(("say", line))
            tell.watch()
            jarvis.skills.briefing.watch()
            if tick % 12 or tell.holding():  # focus mode, quiet hours: no heads-ups or habit offers
                continue
            for key, line, why in persona.heads_ups(jarvis.settings) + tell.resources():
                if time.time() - jarvis.warned.get(key, 0) > 2 * 3600 and tell.tell(key, line, why):
                    jarvis.warned[key] = time.time()
            if (jarvis.settings.get("habits") or {}).get("enabled", True) and not persona.quiet_hours(jarvis.settings):
                name = jarvis.memory.habit_due()
                if name and not tell.muted("habit:" + name):
                    days = jarvis.memory.habit_days(name)
                    tell.note("habit:" + name, "You usually %s around now." % tell._habit_say(name),
                              "You did it around this hour on %d of the last 14 days (%s), and not yet today. I offer "
                              "each one once a day; say stop offering that to stop." % (
                                  len(days), ", ".join(time.strftime("%a %d", time.strptime(d, "%Y-%m-%d"))
                                                       for d in days[-4:])))
                    jarvis.inbox.put(("offer", name))
        except Exception as e:
            print("[watch] %s" % e)


def single_instance():
    """Hold a lock for JARVIS's lifetime; False if another JARVIS already has it."""
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
    single_instance.lock = open(os.path.join(RUN_DIR, "jarvis.lock"), "w")
    try:
        fcntl.flock(single_instance.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main():
    if not single_instance():
        print("JARVIS is already running (look for the icon in the top bar).")
        return 3  # tools/run_jarvis.sh treats 3 as "don't restart"
    inbox = queue.Queue()
    jarvis = Jarvis(inbox)
    jarvis.speaker.listen_inbox()

    ui_cfg = jarvis.settings.get("ui") or {}
    if ui_cfg.get("enabled", True) and os.environ.get("DISPLAY"):
        from ui.window import UI
        jarvis.ui = UI(ui_cfg, on_text=lambda t: inbox.put(("text", t)),
                       on_command=lambda c: inbox.put(("quit", "") if c == "/quit" else ("text", c)),
                       is_speaking=jarvis.speaker.busy)
        from gi.repository import GLib
        GLib.idle_add(jarvis.skills.clipboard_history.watch_clipboard)  # clipboard history ("paste the thing I copied before")

    ups = [u for u in persona.heads_ups(jarvis.settings) if not jarvis.skills.proactive.muted(u[0])][:2]
    for key, line, why in ups:
        jarvis.warned[key] = time.time()
        jarvis.skills.proactive.note(key, line, why)
    jarvis.speaker.say(persona.greeting(jarvis.settings, jarvis.memory, [line for _, line, _ in ups]))
    jarvis.memory.seen()
    threading.Thread(target=watch, args=(jarvis,), daemon=True).start()
    threading.Thread(target=teach, args=(jarvis,), daemon=True).start()
    print("Type /help for commands.")
    whisper = jarvis.whisper = Whisper(jarvis.settings.get("stt") or {})
    threading.Thread(target=watchdog.run, args=(jarvis, jarvis.settings.get("watchdog") or {}), daemon=True).start()
    threading.Thread(target=start_voice, args=(jarvis, whisper), daemon=True).start()
    threading.Thread(target=read_keyboard, args=(inbox,), daemon=True).start()

    def loop():
        try:
            run_loop(jarvis, jarvis.ui is not None)
        finally:
            if jarvis.ui:
                jarvis.ui.quit()

    try:
        if jarvis.ui:
            import signal
            from gi.repository import GLib
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: inbox.put(("quit", "")) or True)
            threading.Thread(target=loop, daemon=True).start()
            jarvis.ui.run()
        else:
            loop()
    except KeyboardInterrupt:
        print()
    jarvis.memory.seen()
    jarvis.speaker.say("Restarting, back in a moment." if jarvis.exit_code == RESTART_CODE
                       else ["Goodbye!", "See you later.", "Bye for now."])
    jarvis.speaker.close()
    whisper.stop()
    sys.stdout.flush()
    os._exit(jarvis.exit_code)  # Background threads are blocked reading the mic/keyboard; don't wait for them.


if __name__ == "__main__":
    sys.exit(main())
