"""Commands running in your terminals, for "tell me when this finishes".

A terminal is busy when its shell has handed the terminal to a command (the shell's tty foreground process
group isn't its own). That works for every terminal: GNOME Terminal, VS Code's, tmux. Shells started with
`bash -c` (biztel run, Claude task terminals) have no job control, so they never count as busy here.
"""
import os
import time

SHELLS = {"bash", "zsh", "fish", "sh", "dash"}
# Programs you sit in rather than wait for; a shell, python or node only counts as that with no script given.
INTERACTIVE = {"vim", "vi", "nvim", "nano", "emacs", "less", "more", "man", "top", "htop", "btop", "claude",
               "ssh", "tmux", "screen", "watch", "tail", "journalctl"}
REPLS = {"python", "python3", "node", "ipython", "ipython3", "irb", "psql", "sqlite3", "mysql"}


class Job:
    def __init__(self, shell, pgid, argv, started, cwd):
        self.shell, self.pgid, self.argv, self.started, self.cwd = shell, pgid, argv, started, cwd
        self.command = spoken(argv)


def _stat(pid):
    """(name, fields after the name) from /proc/<pid>/stat, or None."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            stat = f.read()
    except OSError:
        return None
    return stat[stat.index("(") + 1:stat.rindex(")")], stat[stat.rindex(")") + 2:].split()


def _argv(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


def _boot_time():
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("btime "):
                return int(line.split()[1])
    return 0


def spoken(argv):
    """"npm run build", "python3 train.py", "make": the program and its first words, without paths."""
    words = [os.path.basename(argv[0])] + [os.path.basename(a) if "/" in a else a for a in argv[1:4]]
    text = " ".join(w for w in words if w)
    return text if len(text) <= 40 else text[:40].rsplit(" ", 1)[0]


def running():
    """[Job] for every terminal of yours busy with a command, newest first. JARVIS's own terminal isn't one."""
    own, uid, hz, boot, jobs = os.getsid(0), os.getuid(), os.sysconf("SC_CLK_TCK"), _boot_time(), []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat("/proc/" + pid).st_uid != uid:
                continue
        except OSError:
            continue
        st = _stat(int(pid))
        if not st or st[0] not in SHELLS:
            continue
        try:
            pgrp, sid, tty, tpgid = (int(x) for x in st[1][2:6])
        except (ValueError, IndexError):
            continue
        if not tty or tpgid <= 0 or tpgid == pgrp or sid == own:
            continue
        argv, leader = _argv(tpgid), _stat(tpgid)
        name = os.path.basename(argv[0]) if argv else ""
        if not leader or not name or name in INTERACTIVE or (name in REPLS | SHELLS and len(argv) == 1):
            continue
        try:
            cwd = os.readlink("/proc/%d/cwd" % tpgid)
        except OSError:
            cwd = ""
        jobs.append(Job(int(pid), tpgid, argv, boot + int(leader[1][19]) / hz, cwd))
    return sorted(jobs, key=lambda j: -j.started)


def still_running(job):
    """True while the job's terminal is still busy with it."""
    st = _stat(job.shell)
    try:
        return st is not None and int(st[1][5]) == job.pgid
    except (ValueError, IndexError):
        return False


def took(seconds):
    """"40 seconds", "6 minutes", "1 hour and 5 minutes"."""
    seconds = int(seconds)
    if seconds < 90:
        return "%d seconds" % seconds
    minutes = round(seconds / 60)
    if minutes < 60:
        return "%d minutes" % minutes
    hours, minutes = divmod(minutes, 60)
    text = "1 hour" if hours == 1 else "%d hours" % hours
    return text + (" and %d minute%s" % (minutes, "" if minutes == 1 else "s") if minutes else "")


def wait(job, poll=2, limit=12 * 3600):
    """Block until the job is done (or `limit` seconds pass); True if it finished."""
    deadline = time.time() + limit
    while time.time() < deadline:
        if not still_running(job):
            return True
        time.sleep(poll)
    return False
