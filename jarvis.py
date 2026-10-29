#!/usr/bin/env python3
"""JARVIS: a light, fast voice/text assistant for OS and browser tasks.

Modes:
  /agent  command list first, then ask Claude
  /sys    command list only, no AI
"""
import fcntl
import os
import queue
import random
import re
import sys
import threading
import time
from collections import deque

import yaml

try:
    import readline  # noqa: F401  (arrow keys + history in the prompt)
except ImportError:
    pass

from core import desktop, misslog, persona, undo
from core.browser import Browser
from core.claude_bridge import Claude, ClaudeError, open_in_vscode
from core.executor import Executor
from core.skills import Skills
from core.listen import PushToTalk
from core.intent import IntentMatcher
from core.registry import Registry, clean
from core.safety import MANUAL, Safety
from core.speech import Speaker, pick
from core.stt import Whisper
from core.terminal import RUN_DIR
from core.wake import WakeWord, WhisperWake

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config")

HELP = """Commands:
  /agent    command list, then Claude when nothing matches
  /sys      command list only (no AI)
  /list     show all commands
  /reload   reload the YAML files
  /new      start a fresh conversation with Claude
  /mute     toggle speech
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
Built-in skills (core/skills.py): "stop", "status", "what's on my screen", "close this tab", "next tab",
"switch to the youtube tab", "scroll down", "pause", "next song", "what's playing", "what did I copy",
"fix the grammar in what I copied", "remember that ...", "what did I ask you to remember", "find my resume",
"open the latest download", "maximise", "move this to workspace 2", "put chrome on the left", "switch to vs code",
"is the nano up", "run uptime on the nano", "git status of study buddy", "open the study buddy project",
"run the tests in jarvis", "ask claude to <task> in the <name> project"."""

OPEN_CLAUDE = {"open claude", "open claude terminal", "open claude code", "talk to claude"}
OPEN_CLAUDE_VSCODE = {"open claude in vs code", "open claude in vscode", "open claude code in vs code",
                      "open claude code in vscode"}
ASK_CLAUDE_VSCODE = re.compile(r"^(?:ask|tell) claude(?: code)? in (?:vs ?code) (?:to )?(.+)$", re.IGNORECASE)
CHAT_PHRASES = {"show what you hear": "/hearing on", "hide what you hear": "/hearing off",
                "stop showing what you hear": "/hearing off",
                "show chat": "/chat on", "open chat": "/chat on", "show the chat": "/chat on",
                "hide chat": "/chat off", "close chat": "/chat off", "hide the chat": "/chat off",
                "show orb": "/orb on", "hide orb": "/orb off",
                "go to your workspace": "/desk", "take me to your workspace": "/desk", "go to jarvis workspace": "/desk"}


# Said in a follow-up (no name): only these go to Claude; other chatter ("moving on") is ignored.
REQUEST = re.compile(r"^\W*(please |hey |so |and |also |ok |okay )?(can you |could you |would you |will you )?"
                     r"(what|what's|whats|when|where|who|whose|why|how|which|is|are|am|was|were|do|does|did|can|"
                     r"could|would|will|should|shall|tell|give|show|find|search|look|open|close|launch|start|"
                     r"stop|play|pause|resume|turn|set|make|create|write|read|check|remind|add|remove|delete|"
                     r"explain|define|translate|calculate|go|take|switch|run|send|help|i want|i need|let's|lets)\b",
                     re.IGNORECASE)

# Words that alone don't make a request: "is", "put that", "what the", "okay okay" are mishearings, not questions.
FILLER = set("""a an the and but or so is it it's its that this those these what what's whats up um uh hmm
huh oh ok okay yeah yes no yep nope well like just i i'm me you your we put get got go do did be been was
there here then now hey hi hello right sure fine good""".split())
CODE = re.compile(r"[{}=;<>`$\\]|\b(?:import|def|return|sys\.\w+)\b")


def worth_asking(text, names):
    """False for speech-recognition junk that shouldn't cost a Claude call."""
    if "\n" in text or CODE.search(text):
        return False  # pasted or leaked code, not something the user said
    name_words = {w for n in names for w in n.split()}
    words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in name_words]
    real = [w for w in words if w not in FILLER]
    return bool(real) and (len(words) > 1 or len(real[0]) >= 4)


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
        self.speaker = Speaker(self.settings.get("speech") or {})
        self.speaker.on_say = self._said
        self.speaker.quiet = lambda: persona.quiet_hours(self.settings)
        self.speaker.quiet_volume = (self.settings.get("quiet_hours") or {}).get("volume", 40)
        self.executor = Executor(self.safety, self.speaker, self.settings, self.ask)
        self.whisper_port = int((self.settings.get("stt") or {}).get("port", 8178))
        self.skills = Skills(self)  # stop, status, screen, tabs, clipboard, files, windows, media, notes, nano, git
        self.mode = self.settings.get("mode", "agent")
        self.memory = persona.Memory()
        self.warned = {}  # heads-up key -> when it was last mentioned
        self.last_request = None  # for "do that again"
        self.last_app = None  # process of the last app opened, for "close it"
        self.last_undo = None  # saved command that reverses the last one, for "undo that"
        self.ignored = False  # last follow-up was chatter, not a request
        self.follow_up = lambda: None  # wake word: listen once more without the name (set by start_voice)
        self.recent = deque(maxlen=8)  # what happened without Claude since its last answer, passed on next time
        self.source = "text"  # how the current request came in: text / voice / follow
        self.last_suggest = 0.0  # when Claude's last own suggestion was passed on, so it doesn't nag

    def state(self, name):
        if self.ui:
            self.ui.set_state(name)

    def heard(self, text, outcome):
        """Show what speech recognition heard (and what JARVIS did with it) in the chat panel and the log."""
        print("  [heard] %s -> %s" % (text and repr(text), outcome))
        if self.show_hearing:
            self.chat("heard", ("heard \u201c%s\u201d  \u2192 %s" % (text, outcome)) if text else "\u2026 %s" % outcome)

    def _said(self, text):
        self.chat("jarvis", text)
        self.recent.append("JARVIS said: %s" % text)

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
        self.intents = None  # meaning matcher: other wordings of known phrases, before Claude
        try:
            cfg = self.settings.get("intents") or {}
            self.intents = IntentMatcher(os.path.join(CONFIG, "intents.yaml"), cfg.get("threshold", 0.72),
                                         extra=command_phrases(self.registry))
        except Exception as e:
            print("[intent] meaning matcher off: %r" % e)
        self.browser = Browser(self.registry)
        self.claude = Claude(self.settings.get("claude") or {}, self.name, self.settings.get("user_name") or "the user")
        if hasattr(self, "executor"):
            self.executor.safety = self.safety

    def ask(self, question):
        """Ask a yes/no question; the answer can be typed or spoken."""
        self.speaker.say(question)
        self.follow_up()  # "yes" without saying the name first
        timeout = self.settings.get("confirm_timeout", 8)
        sys.stdout.write("  [yes/no, typed or spoken] (%ds) > " % timeout)
        sys.stdout.flush()
        deferred = []  # reminders and heads-ups that arrive mid-question wait until it's answered
        deadline = self.speaker.done_at() + timeout  # counted from when the question has been said
        try:
            while True:
                kind, answer = self.inbox.get(timeout=max(0.01, deadline - time.time()))
                if kind not in ("say", "offer"):
                    break
                deferred.append((kind, answer))
        except queue.Empty:
            print()
            return False
        finally:
            for item in deferred:
                self.inbox.put(item)
        if kind in ("voice", "follow"):
            print(answer)
        if kind in ("text", "voice", "follow"):
            self.chat("you", answer)
        return kind in ("text", "voice", "follow") and bool(YES.match(answer or ""))

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
        text = clean(raw, self.names)
        if not text:
            self.speaker.say(["Yes?", "I'm listening.", "Yeah, what's up?", "Go ahead."])
            return True

        self.recent.append("User: %s" % text)
        low = text.lower()
        if undo.UNDO.match(text):
            return self._undo()
        if low in CHAT_PHRASES:
            return self._slash(CHAT_PHRASES[low])
        if self._personal(text) or self.skills.handle(text):
            return True
        self.last_request = text
        if low in OPEN_CLAUDE:
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

        # 1. Exact command, 2. browser phrase, 3. close-enough command, 4. Claude.
        match = self.registry.match_exact(text)
        if match:
            return self._run_match(match)
        action = self.browser.parse(text)
        if action:
            if action.browser:
                run = self.registry.browsers[action.browser]
                self.last_app = run.get("process") or os.path.basename(run["run"].split()[0])
            self.executor.run(action.shell(self.registry.browsers), reply=[p % action.description for p in ("Opening %s", "Sure, opening %s", "Okay, pulling up %s")])
            return True
        match = self.registry.match_fuzzy(text)
        if match:
            return self._run_match(match)
        if self.intents and not getattr(self, "_rewording", False):
            phrase, score = self.intents.match(text)
            if phrase:
                self.heard(text, "means \"%s\" (%.2f)" % (phrase, score))
                self._rewording = True
                try:
                    return self.handle(phrase)
                finally:
                    self._rewording = False

        if follow_up and not (REQUEST.match(text) or text.rstrip().endswith("?")):
            self.heard(text, "ignored (follow-up that isn't a question or request)")
            self.ignored = True
            return True
        if not worth_asking(text, self.names):
            self.heard(text, "ignored (too short or unclear to ask Claude)")
            self._miss(text, "unclear")
            self.speaker.say(["Sorry, I didn't catch that.", "Sorry, could you say that again?"])
            return True
        self._miss(text, "claude" if self.mode != "sys" else "unknown")
        if self.mode == "sys":
            self.speaker.say(["Sorry, that one's not on my list, and I can't ask Claude in system mode.",
                              "Hmm, I don't know that one yet. Switch to agent mode and I can ask Claude."])
        else:
            self._ask_claude(text)
        return True

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
            if self.last_app:
                return self.handle("close %s" % self.last_app)
            self.speaker.say("Sorry, I'm not sure what to close. Tell me the app's name.")
        elif persona.LIST_REMINDERS.match(text):
            reminders = self.memory.reminders()
            if not reminders:
                self.speaker.say(["You don't have any reminders.", "No reminders right now."])
            else:
                self.speaker.say("You have %d: %s." % (len(reminders), "; ".join(
                    "%s %s at %s" % (r["kind"], r["what"], time.strftime("%-I:%M %p", time.localtime(r["due"])))
                    for r in reminders)))
        elif persona.CLEAR_REMINDERS.match(text):
            count = self.memory.clear_reminders()
            self.speaker.say("Okay, cleared %d reminder%s." % (count, "" if count == 1 else "s") if count
                             else "There weren't any reminders.")
        else:
            reminder = persona.parse_reminder(text)
            if reminder == "no time":
                self.speaker.say("Sure, when? Say something like, remind me in 10 minutes to stretch.")
            elif reminder:
                self.memory.add_reminder(reminder)
                self.speaker.say(["Okay, I'll remind you %s %s %s." % (reminder["when"], reminder["kind"], reminder["what"]),
                                  "Got it, %s." % reminder["when"]])
            else:
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
            self.speaker.say(line)

        try:
            answer = self.claude.ask(text, recent, on_say=speak_early, image=image, about=self._about())
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
                self.speaker.say(answer["say"])
            self.recent.clear()  # Claude knows its own answer; what happens next (e.g. "Done.") is new to it
        if not answer["steps"] and answer["run_now"] and " && " in answer["command"]:
            # Claude chained commands anyway: run them as separate steps, so each can be checked and stopped.
            answer["steps"] = [{"command": part.strip()} for part in answer["command"].split(" && ")]
            answer["command"] = ""
        if answer["steps"]:
            self._run_steps(answer["steps"])
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
                                        on_output=lambda o: o if 0 < len(o) <= 120 else pick(
                                            ["That's a long one, so I've put it on screen.",
                                             "Here you go, it's on your screen."]))
                if out and len(out) > 120:
                    print("  " + out.replace("\n", "\n  "))
        if answer["save"]:
            self._save(answer["save"])
        if answer["suggest"]:
            self._suggest(answer["suggest"])

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
        return " | ".join(parts)

    def _suggest(self, suggest):
        """Pass on Claude's own recommendation: a tip, or a yes/no offer that runs a command."""
        if not isinstance(suggest, dict) or not str(suggest.get("say") or "").strip():
            return
        gap = (self.settings.get("claude") or {}).get("suggest_every_minutes", 10) * 60
        if time.time() - self.last_suggest < gap:
            return
        self.last_suggest = time.time()
        say, command = str(suggest["say"]).strip(), str(suggest.get("command") or "").strip()
        self.chat("sys", "suggestion: %s%s" % (say, "  (command: %s)" % command if command else ""))
        if not command:
            self.speaker.say(say)
        elif self.ask(say):
            self.executor.run(command, reply=["Done.", "There you go."], output=True)
        else:
            self.speaker.say(["Okay, no worries.", "Sure, never mind."])

    def _run_steps(self, steps):
        """Agent mode: run Claude's multi-step plan in order. Every step goes through the safety rules;
        the plan stops at the first step that fails, is refused, or has to be run by hand."""
        steps = [s for s in steps if isinstance(s, dict) and str(s.get("command") or "").strip()][:10]
        if not steps:
            return
        self.chat("sys", "plan:\n" + "\n".join("%d. %s" % (i + 1, s["command"]) for i, s in enumerate(steps)))
        if len(steps) > 1 and not self.ask("That's %d steps; I've put them in the chat. Shall I go ahead?"
                                           % len(steps)):
            self.speaker.say(["Okay, I won't.", "No problem, cancelled."])
            return
        for i, step in enumerate(steps):
            command = str(step["command"]).strip()
            if step.get("say"):
                self.speaker.say(str(step["say"]))
            self.chat("sys", "step %d: %s" % (i + 1, command))
            out = self.executor.run(command, reply="", output=True, timeout=600,
                                    question=["Step %d: run %s?" % (i + 1, command.split()[0])])
            if out:
                self.chat("sys", out[-600:])
            if not self.executor.ok:
                self.speaker.say("I stopped at step %d of %d%s." % (
                    i + 1, len(steps), ", it didn't work" if out else ""))
                return
        self.speaker.say(["All %d steps done." % len(steps), "Done, that's everything."])

    def _save(self, save):
        """Add Claude's suggested shortcut to commands.yaml and say how to use it (it decides what's worth it)."""
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
        self.registry.add(name, phrases, run, str(save.get("reply") or "Done"), bool(save.get("output")))
        phrase = re.sub(r"\{\w+\}", "something", phrases[0])
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
        return True

    def _undo(self):
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
            self.speaker.say(["Okay, I've reloaded everything.", "All refreshed."])
        elif cmd == "/new":
            self.claude.new_session()
            self.speaker.say(["Sure, fresh start with Claude.", "Okay, starting a new conversation with Claude."])
        elif cmd == "/mute":
            self.speaker.enabled = not self.speaker.enabled
            print("speech %s" % ("on" if self.speaker.enabled else "off"))
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
        elif cmd == "/desk":
            desktop.go_to_jarvis_workspace()
        elif cmd == "/status":
            self.skills.status()
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
    if not whisper.start():
        print("[voice] Whisper isn't available yet; push-to-talk is off. Typing still works.")
        jarvis.rest = "off"
        jarvis.state("off")
        return

    def on_audio(pcm):
        jarvis.state("thinking")
        try:
            text = whisper.transcribe(pcm)
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
        wake = WhisperWake(jarvis.names, whisper, on_text, on_wake=on_listen, is_busy=busy, on_heard=jarvis.heard,
                           follow_seconds=wake_cfg.get("follow_up_seconds", 8),
                           on_stop=lambda: (jarvis.speaker.stop(), jarvis.state(jarvis.rest)))
        wake.start()
        jarvis.follow_up = wake.follow_up
        print('[voice] Wake word ready: say any of: %s (optionally with "hey").' % ", ".join(jarvis.names))
    elif wake_cfg.get("enabled", True):
        phrases = [g + n for n in jarvis.names for g in ("", "hey ", "hello ", "ok ")]
        WakeWord(phrases, on_audio, on_wake=on_listen, is_busy=busy,
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
        try:
            jarvis.source = kind
            if not jarvis.handle(line, follow_up=kind == "follow"):
                break
        finally:
            jarvis.state(jarvis.rest)
        if kind == "voice" or (kind == "follow" and not jarvis.ignored):
            jarvis.follow_up()  # "what's the weather?" next, without the name


def watch(jarvis):
    """Background: reminders when they're due; every 2 minutes, heads-ups and habit offers."""
    tick = 0
    while True:
        time.sleep(10)
        tick += 1
        try:
            for reminder in jarvis.memory.due_reminders():
                jarvis.inbox.put(("say", persona.reminder_line(reminder, jarvis.settings.get("user_name"))))
            if tick % 12:
                continue
            for key, line in persona.heads_ups(jarvis.settings):
                if time.time() - jarvis.warned.get(key, 0) > 2 * 3600:
                    jarvis.warned[key] = time.time()
                    jarvis.inbox.put(("say", line))
            if (jarvis.settings.get("habits") or {}).get("enabled", True) and not persona.quiet_hours(jarvis.settings):
                name = jarvis.memory.habit_due()
                if name:
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
        from core.ui import UI
        jarvis.ui = UI(ui_cfg, on_text=lambda t: inbox.put(("text", t)),
                       on_command=lambda c: inbox.put(("quit", "") if c == "/quit" else ("text", c)),
                       is_speaking=jarvis.speaker.busy)

    ups = persona.heads_ups(jarvis.settings)[:2]
    for key, _ in ups:
        jarvis.warned[key] = time.time()
    jarvis.speaker.say(persona.greeting(jarvis.settings, jarvis.memory, [line for _, line in ups]))
    jarvis.memory.seen()
    threading.Thread(target=watch, args=(jarvis,), daemon=True).start()
    print("Type /help for commands.")
    whisper = Whisper(jarvis.settings.get("stt") or {})
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
    jarvis.speaker.say(["Goodbye!", "See you later.", "Bye for now."])
    jarvis.speaker.close()
    whisper.stop()
    sys.stdout.flush()
    os._exit(0)  # Background threads are blocked reading the mic/keyboard; don't wait for them.


if __name__ == "__main__":
    sys.exit(main())
