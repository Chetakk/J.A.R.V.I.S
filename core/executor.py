"""Runs commands according to their safety level."""
import os
import shlex
import shutil
import subprocess

from system import desktop
from core.persona import with_name
from core.safety import CONFIRM, MANUAL
from voice.speech import pick
from system.terminal import DEFAULT_TERMINAL, ManualTerminal


class Executor:
    def __init__(self, safety, speaker, settings, ask):
        self.safety = safety
        self.speaker = speaker
        self.terminal = ManualTerminal(settings.get("terminal") or DEFAULT_TERMINAL)
        self.ask = ask  # ask(question) -> bool, by voice or keyboard
        self.user_name = settings.get("user_name")
        self.ok = False  # whether the last run() actually ran

    def run(self, command, reply="Done", output=False, declared=None, on_output=None, question=None,
            window=False, timeout=15):
        """Run `command` under the safety rules. Returns the captured output, if any.

        window=True: the command opens a new app window, so go to JARVIS's workspace first.
        Afterwards self.ok tells whether it actually ran (and, with output=True, exited successfully).
        """
        self.ok = False
        reply = pick(reply)
        level, reason = self.safety.classify(command, declared)
        if level == MANUAL:
            self.terminal.send(command, reason)
            self.speaker.say("I'd rather not run that one myself, since %s. I've put it in your terminal for you." % reason)
            return None
        if level == CONFIRM and not self.ask(question or "%s?" % reply.rstrip(".")):
            self.speaker.say(["Okay, I won't.", "No problem, cancelled.", "Alright, never mind then."])
            return None

        try:
            prog = shlex.split(command)[0]
        except (ValueError, IndexError):
            prog = ""
        if prog and not shutil.which(os.path.expanduser(prog)):
            self.speaker.say("Hmm, I can't find %s on this computer." % prog)
            return None
        if output:
            try:
                res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                                     cwd=os.path.expanduser("~"))
            except subprocess.TimeoutExpired:
                self.speaker.say("That was taking too long, so I stopped it.")
                return None
            self.ok = res.returncode == 0
            out = (res.stdout or res.stderr).strip()
            if on_output:
                self.speaker.say(on_output(out))
            return out
        if window:
            desktop.go_to_jarvis_workspace()
        subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        self.ok = True
        if reply:
            self.speaker.say(with_name(reply, self.user_name))
        return None
