"""Feeds the JARVIS manual terminal (tools/manual_terminal.rc): each "reason<TAB>base64 command" line from the fifo
is typed at the shell's prompt, never run. Typing waits while a program has the terminal, so it can't land in one.
It also reads the terminal's log (`script`) and appends each command you ran, its exit code and output to RESULTS,
from the marks the rc's ble.sh hooks print, so JARVIS and Claude know what came of it.

    python3 manual_feed.py FIFO PIDFILE SHELL_PID LOG RESULTS
"""
import base64
import fcntl
import json
import os
import re
import select
import signal
import sys
import termios
import time

MARK = re.compile(rb"\x1b\]7777;(?:start;([A-Za-z0-9+/=]*);([A-Za-z0-9+/=]*)|end;(\d+))\x07")
ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>78DEHMc]")
OUTPUT_KEEP = 3000  # characters of a command's output kept (its end, where errors are)
LOG_MAX = 1 << 20  # the log is emptied past this while no command runs


def keys(command, reason):
    """The bytes to type: end of line + clear it (Ctrl+E, Ctrl+U), then the command with the reason as a comment,
    as a bracketed paste: ble.sh inserts it as text, so a newline is a real line break (not Enter, not a literal ^J)
    and a tab never completion. ESC is dropped so the command can't end the paste early and press keys itself."""
    text = "# ⚠ %s\n%s" % (reason, command) if "\n" in command else "%s  # ⚠ %s" % (command, reason)
    return ("\x05\x15\x1b[200~" + text.replace("\x1b", "") + "\x1b[201~").encode()


def at_prompt(shell):
    """True while the shell itself has the terminal (no program running in it)."""
    with open("/proc/%d/stat" % shell) as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return fields[5] == fields[2]  # foreground process group (tpgid) == the shell's own


def clean(raw):
    """Terminal output as plain text: no colours or cursor moves, a progress bar's last state, its end only."""
    text = ESCAPES.sub("", raw.decode("utf-8", "replace")).replace("\r\n", "\n")
    text = "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n")).strip()
    return text[-OUTPUT_KEEP:]


def results(buf):
    """(finished commands, the rest to keep) from the log: {"command", "cwd", "exit", "output"} for each start..end."""
    done, start = [], None
    for m in MARK.finditer(buf):
        if m.group(1) is not None:
            start = m
        elif start:
            done.append({"command": base64.b64decode(start.group(1)).decode("utf-8", "replace"),
                         "cwd": base64.b64decode(start.group(2)).decode("utf-8", "replace"),
                         "exit": int(m.group(3)), "output": clean(buf[start.end():m.start()])})
            start = None
    if not start:
        return done, b""
    rest = buf[start.start():]
    if len(rest) > LOG_MAX:  # a command printing without end: its mark and the latest output
        rest = buf[start.start():start.end()] + rest[-OUTPUT_KEEP * 4:]
    return done, rest


def main(fifo, pidfile, shell, log, results_file):
    shell = int(shell)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)  # the window closing: notice the shell's gone, remove the pidfile
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))
    tty = os.open("/dev/tty", os.O_RDWR)  # a background job's stdin is /dev/null
    fd = os.open(fifo, os.O_RDWR)  # read+write: never blocks, never sees EOF
    buf, pending, seen, logged = b"", [], 0, b""
    try:
        while os.path.exists("/proc/%d" % shell):
            if select.select([fd], [], [], 0.5)[0]:
                buf += os.read(fd, 65536)
                *lines, buf = buf.split(b"\n")
                pending += [line for line in lines if b"\t" in line]
            while pending and at_prompt(shell):
                reason, b64 = pending.pop(0).decode("utf-8", "replace").split("\t", 1)
                for b in keys(base64.b64decode(b64).decode("utf-8", "replace"), reason):
                    fcntl.ioctl(tty, termios.TIOCSTI, bytes([b]))
            try:
                with open(log, "rb") as f:
                    f.seek(seen)
                    new = f.read()
            except OSError:
                new = b""
            seen += len(new)
            done, logged = results(logged + new)
            if done:
                with open(results_file, "a") as f:
                    for r in done:
                        f.write(json.dumps(dict(r, at=time.time())) + "\n")
            if seen > LOG_MAX and not logged and at_prompt(shell):
                # shortcut: output written between the read above and this truncate is lost; it's only the prompt
                # unless a command starts within those microseconds. Upgrade: rotate by renaming if that matters.
                os.truncate(log, 0)
                seen = 0
    except (OSError, ValueError):
        pass
    finally:
        try:
            os.remove(pidfile)
        except OSError:
            pass


if __name__ == "__main__":
    main(*sys.argv[1:6])
