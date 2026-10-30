"""Runs commands according to their safety level."""
import os
import shlex
import shutil
import subprocess
import tempfile

from core import verify
from system import desktop
from core.persona import address, with_name
from core.safety import CONFIRM, MANUAL
from voice.speech import pick
from system.terminal import DEFAULT_TERMINAL, ManualTerminal


# Shell builtins have no program file to find, so `cd x && ls` must not be called "missing".
BUILTINS = {"cd", "pwd", "echo", "true", "false", "test", "[", "export", "type", "pushd", "popd"}


class Executor:
    def __init__(self, safety, speaker, settings, ask):
        self.safety = safety
        self.speaker = speaker
        self.terminal = ManualTerminal(settings.get("terminal") or DEFAULT_TERMINAL)
        self.ask = ask  # ask(question) -> bool, by voice or keyboard
        self.user_name = address(settings)  # "sir" or "Ckay" (settings call_me)
        self.ok = False  # whether the last run() actually ran
        self.outcome = ""  # why not: "manual" (left in your terminal), "declined" (you said no), "missing", "timeout"
        self.notify = speaker.say  # a sure failure noticed later (from a thread); JARVIS sets its inbox

    def run(self, command, reply="Done", output=False, declared=None, on_output=None, question=None,
            window=False, timeout=15, approved=False):
        """Run `command` under the safety rules. Returns the captured output, if any.

        window=True: the command opens a new app window, so go to JARVIS's workspace first.
        approved=True: you already said yes to it (a task's steps, shown on its card): not asked again.
        Afterwards self.ok tells whether it actually ran (and, with output=True, exited successfully);
        verify.last tells, a few seconds later, what came of it.
        """
        self.ok, self.outcome = False, "ran"
        reply = pick(reply)
        level, reason = self.safety.classify(command, declared)
        if level == MANUAL:
            self.outcome = "manual"
            self.terminal.send(command, reason)
            self.speaker.say("I'd rather not run that one myself, since %s. I've put it in your terminal for you." % reason)
            return None
        if level == CONFIRM and not approved and not self.ask(question or "%s?" % reply.rstrip(".")):
            self.outcome = "declined"
            self.speaker.say(["Okay, I won't.", "No problem, cancelled.", "Alright, never mind then."])
            return None

        try:
            prog = shlex.split(command)[0]
        except (ValueError, IndexError):
            prog = ""
        if prog and prog not in BUILTINS and not shutil.which(os.path.expanduser(prog)):
            self.outcome = "missing"
            self.speaker.say("Hmm, I can't find %s on this computer." % prog)
            return None
        if output:
            try:
                res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                                     cwd=os.path.expanduser("~"))
            except subprocess.TimeoutExpired:
                self.outcome = "timeout"
                self.speaker.say("That was taking too long, so I stopped it.")
                return None
            self.ok = res.returncode == 0
            out = (res.stdout or res.stderr).strip()
            verify.record(command, self.ok, "it ran without errors" if self.ok else
                          "it failed (exit %d)" % res.returncode)
            if on_output:
                self.speaker.say(on_output(out))
            return out
        if window:
            desktop.go_to_jarvis_workspace()
        # A window app's stderr isn't kept: it can write to it for days. A quick command's is, for the error.
        err = None if window else tempfile.TemporaryFile()
        before = verify.windows_now() if window else {}
        proc = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=err or subprocess.DEVNULL, start_new_session=True)
        verify.launched(command, proc, before, window, self.notify, errors=err and (lambda: _read(err)))
        self.ok = True
        if reply:
            self.speaker.say(with_name(reply, self.user_name))
        return None


def _read(f):
    try:
        f.seek(0)
        return f.read()[-2000:].decode("utf-8", "replace")
    finally:
        f.close()
