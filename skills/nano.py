"""The Jetson Nano: is it up, restart its keyboard link, hand it the keyboard, run a command on it."""
import os
import shlex
import subprocess
import time

from core.safety import MANUAL
from skills.base import Skill


class Nano(Skill):
    def patterns(self):
        return [
            (r"(?:is )?(?:the )?nano (?:up|online|alive|reachable|there|connected|working)|nano status|"
             r"check (?:on )?(?:the )?nano", lambda m: self.nano_status()),
            (r"(?:restart|reconnect|fix|start) (?:the )?nano(?: link| connection| kvm)?", lambda m: self.nano_restart()),
            (r"(?:switch|move|hand)(?: control| the keyboard| the mouse)? to (?:the )?nano|give (?:the )?nano "
             r"(?:the )?(?:keyboard|mouse|control)", lambda m: self.nano_focus()),
            (r"run (.+) on (?:the )?nano", self.nano_run),
        ]

    def _nano(self):
        return dict({"host": "192.168.50.10", "user": "biztel-nano", "key": "~/.ssh/boisys_nano",
                     "service": "nano-kvm.service"}, **(self.j.settings.get("nano") or {}))

    def nano_status(self):
        n = self._nano()
        up = subprocess.call(["ping", "-c1", "-W1", n["host"]], stdout=subprocess.DEVNULL) == 0
        link = subprocess.call(["systemctl", "--user", "is-active", "--quiet", n["service"]]) == 0
        if up and link:
            self.say("The Nano is up, and the keyboard link is running.")
        elif up:
            self.say("The Nano is up, but the keyboard link isn't running. Say restart the Nano link to fix it.")
        else:
            self.say("I can't reach the Nano. It may be off, or the cable is unplugged.")

    def nano_restart(self):
        n = self._nano()
        if self.j.ask("Restart the Nano keyboard link?"):
            subprocess.call(["systemctl", "--user", "restart", n["service"]])
            time.sleep(3)
            self.nano_status()

    def nano_focus(self):
        script = os.path.expanduser("~/.local/bin/nano-focus.sh")
        if not os.path.exists(script):
            return False
        subprocess.Popen([script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def nano_run(self, m):
        n = self._nano()
        inner = m.group(1).strip()
        ssh = "ssh -i %s -o BatchMode=yes -o ConnectTimeout=5 %s@%s %s" % (
            n["key"], n["user"], n["host"], shlex.quote(inner))
        level, reason = self.j.safety.classify(inner)
        if level == MANUAL:
            self.j.executor.terminal.send(ssh, "on the Nano: " + reason)
            self.say("That one's risky on the Nano too, so it's ready in your terminal for you to run.")
            return
        if not self.j.ask("Run %s on the Nano?" % inner):
            return
        try:
            res = subprocess.run(ssh, shell=True, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            self.say("The Nano didn't answer in time.")
            return
        out = (res.stdout or res.stderr).strip()
        self.j.chat("sys", "nano$ %s\n%s" % (inner, out[-2000:]))
        if len(out) > 150:
            self.j.show({"title": "Nano: %s" % inner, "text": "```\n%s\n```" % out[-3000:]})
        if res.returncode == 255:
            self.say("I couldn't connect to the Nano.")
        elif len(out) <= 150:
            self.say(out or "Done, no output.")
        else:
            self.say("Done. The output's in the chat.")
