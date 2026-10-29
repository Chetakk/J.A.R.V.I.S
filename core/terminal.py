"""The JARVIS manual terminal: commands JARVIS won't run appear there pre-typed for the user."""
import base64
import os
import shlex
import shutil
import subprocess
import time

from core import desktop

_HERE = os.path.dirname(os.path.abspath(__file__))
MANUAL_SCRIPT = os.path.join(_HERE, "..", "tools", "manual_terminal.sh")
RUN_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "jarvis-%d" % os.getuid())
DEFAULT_TERMINAL = 'gnome-terminal --title "JARVIS: manual commands" -- {script}'


class ManualTerminal:
    def __init__(self, terminal=DEFAULT_TERMINAL):
        self.terminal = terminal
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        self.fifo = os.path.join(RUN_DIR, "manual.fifo")
        self.pidfile = os.path.join(RUN_DIR, "manual.pid")

    def send(self, command, reason):
        """Show `command` pre-typed in the terminal. Returns False if it could only be printed."""
        copy_to_clipboard(command)
        if self._alive():
            desktop.bring_up("JARVIS: manual commands")
        else:
            desktop.go_to_jarvis_workspace()
            self._open()
        msg = "%s\t%s\n" % (reason.replace("\t", " ").replace("\n", " "),
                            base64.b64encode(command.encode()).decode())
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, msg.encode())
            os.close(fd)
            return True
        except OSError:
            print("  Run this yourself (copied to clipboard):\n    %s" % command)
            return False

    def _alive(self):
        try:
            with open(self.pidfile) as f:
                os.kill(int(f.read().strip()), 0)
            return True
        except (OSError, ValueError):
            return False

    def _open(self):
        if not os.path.exists(self.fifo):
            os.mkfifo(self.fifo, 0o600)
        script = "bash %s %s %s" % tuple(shlex.quote(p) for p in (MANUAL_SCRIPT, self.fifo, self.pidfile))
        subprocess.Popen(self.terminal.replace("{script}", script), shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.time() + 5
        while time.time() < deadline and not self._alive():
            time.sleep(0.1)


def copy_to_clipboard(text):
    if shutil.which("xclip"):
        try:
            p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            p.communicate(text.encode(), timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
