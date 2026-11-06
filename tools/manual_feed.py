"""Feeds the JARVIS manual terminal (tools/manual_terminal.rc): each "reason<TAB>base64 command" line from the fifo
is typed at the shell's prompt, never run. Typing waits while a program has the terminal, so it can't land in one.

    python3 manual_feed.py FIFO PIDFILE SHELL_PID
"""
import base64
import fcntl
import os
import select
import sys
import termios


def keys(command, reason):
    """The bytes to type: end of line + clear it (Ctrl+E, Ctrl+U), then the command with the reason as a comment.
    Ctrl+V before each control character inserts it as text, so a newline is never Enter and a tab never completion."""
    text = "# ⚠ %s\n%s" % (reason, command) if "\n" in command else "%s  # ⚠ %s" % (command, reason)
    return ("\x05\x15" + "".join("\x16" + c if c < " " or c == "\x7f" else c for c in text)).encode()


def at_prompt(shell):
    """True while the shell itself has the terminal (no program running in it)."""
    with open("/proc/%d/stat" % shell) as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return fields[5] == fields[2]  # foreground process group (tpgid) == the shell's own


def main(fifo, pidfile, shell):
    shell = int(shell)
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))
    tty = os.open("/dev/tty", os.O_RDWR)  # a background job's stdin is /dev/null
    fd = os.open(fifo, os.O_RDWR)  # read+write: never blocks, never sees EOF
    buf, pending = b"", []
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
    except (OSError, ValueError):
        pass
    finally:
        try:
            os.remove(pidfile)
        except OSError:
            pass


if __name__ == "__main__":
    main(*sys.argv[1:4])
