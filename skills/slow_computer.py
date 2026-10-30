""""What's slowing my computer": top CPU and memory users; offers to close the worst one, or deals with a frozen
window first (skills/frozen.py)."""
import os
import signal
import threading
import time

from skills.base import Skill
from system import desktop

# Never offered for closing: the desktop, sound, JARVIS itself and its helpers.
PROTECTED = {"gnome-shell", "xorg", "x", "systemd", "pulseaudio", "pipewire", "gdm", "gnome-session-b",
             "python3", "whisper-server", "piper", "nvargus-daemon", "dbus-daemon", "ibus-daemon"}


class SlowComputer(Skill):
    group = "computer"
    does = "What's using the CPU and memory; offers to close the worst"
    phrases = ("what's slowing my computer (top CPU/memory users, offers to close one)",)
    gives = "speech and a card"
    permission = "confirm"
    asks = "before closing a program"
    checks = "never offers the desktop or JARVIS's own helpers; frozen windows are dealt with first"

    def patterns(self):
        return [
            (r"what'?s (?:slowing (?:down )?|hogging |using (?:up )?)(?:my |the )?(?:computer|system|jetson|cpu|"
             r"memory|ram)(?: down)?|why is (?:my |the )?(?:computer|system|jetson|it|everything) (?:so )?slow|"
             r"what'?s using (?:all )?(?:my |the )?(?:cpu|memory|ram)|(?:show )?(?:the )?top processes",
             lambda m: self.slow()),
            (r"(?:check |show |what'?s )?(?:the )?system usage|(?:check|show) (?:the )?cpu,? memory,? (?:and )?gpu"
             r"(?: usage| load)?|(?:check |show )?cpu,? (?:ram|memory),? (?:and )?gpu(?: usage| load)?",
             lambda m: self.usage()),
        ]

    def usage(self):
        """CPU, memory and GPU load together, read straight from /proc and sysfs (no shell, nothing chained)."""
        cpu = _cpu_percent()
        total, free = _meminfo("MemTotal"), _meminfo("MemAvailable")
        used = 100 * (total - free) // max(total, 1)
        try:
            with open("/sys/devices/gpu.0/load") as f:
                gpu = "%d percent" % (int(f.read()) // 10)  # per mille
        except (OSError, ValueError):
            gpu = None
        mem = "%d percent used, %.1f GB free" % (used, free / 2 ** 20)
        self.j.show({"title": "System usage", "text": "- CPU: %d percent\n- Memory: %s\n- GPU: %s"
                     % (cpu, mem, gpu or "not available")})
        self.say("The CPU is at %d percent, memory is %d percent used%s." % (
            cpu, used, ", and the GPU is at " + gpu if gpu else ""))

    def slow(self):
        stuck = []  # windows are pinged while the CPU is measured
        ping = threading.Thread(target=lambda: stuck.extend(desktop.unresponsive(
            [w for w in desktop.windows() if "jarvis" not in w.wm_class], 1.5)))
        ping.start()
        procs = top_processes()
        ping.join()
        cpu = [p for p in procs if p[1] >= 5][:3]
        heavy = sorted(procs, key=lambda p: -p[2])[:3]
        free = _meminfo("MemAvailable") / 2 ** 20  # GB
        lines = ["%s: %d%% CPU, %d MB" % (p[0], p[1], p[2]) for p in (cpu + [h for h in heavy if h not in cpu])]
        self.j.show({"title": "What's using the computer", "text": "\n".join("- " + l for l in lines)
                     + "\n\nMemory free: %.1f GB" % free})
        if cpu:
            spoken = "%s is using the most CPU, %d percent" % (cpu[0][0], cpu[0][1])
        else:
            spoken = "Nothing's working the CPU hard right now"
        spoken += ". The biggest on memory is %s at %s." % (heavy[0][0], _mb(heavy[0][2]))
        if free < 1.5:
            spoken += " Memory is low, only %.1f gigabytes free." % free
        if stuck:  # one question at a time: the frozen app is the likelier culprit
            self.say(spoken)
            self.j.skills.frozen.check(stuck)
            return
        target = next((p for p in cpu + heavy if p[0].lower() not in PROTECTED), None)
        if not target or (target[1] < 30 and free >= 1.5):
            self.say(spoken)
            return
        if self.j.ask(spoken + " Want me to close %s?" % target[0]):
            for pid in target[3]:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            self.say("Closed %s." % target[0])
        else:
            self.say("Okay, leaving it.")


def _cpu_ticks():
    """{pid: (name, cpu ticks, rss kB)} for the user's processes."""
    out, uid = {}, os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat("/proc/" + pid).st_uid != uid:
                continue
            with open("/proc/%s/stat" % pid) as f:
                stat = f.read()
            name = stat[stat.index("(") + 1:stat.rindex(")")]
            fields = stat[stat.rindex(")") + 2:].split()
            out[int(pid)] = (name, int(fields[11]) + int(fields[12]), int(fields[21]) * 4)
        except (OSError, ValueError, IndexError):
            pass
    return out


def top_processes(seconds=0.5):
    """[(name, cpu %, memory MB, [pids])] grouped by program, busiest first (measured over `seconds`)."""
    before = _cpu_ticks()
    time.sleep(seconds)
    after = _cpu_ticks()
    hz, groups = os.sysconf("SC_CLK_TCK"), {}
    for pid, (name, ticks, rss) in after.items():
        used = ticks - before.get(pid, (name, ticks))[1]
        g = groups.setdefault(name, [name, 0.0, 0.0, []])
        g[1] += 100.0 * used / hz / seconds
        g[2] += rss / 1024
        g[3].append(pid)
    return sorted(groups.values(), key=lambda g: (-g[1], -g[2]))


def _cpu_percent(seconds=0.3):
    def sample():
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return sum(v), v[3] + v[4]  # total, idle + iowait
    t1, i1 = sample()
    time.sleep(seconds)
    t2, i2 = sample()
    return 100 * (1 - (i2 - i1) / max(t2 - t1, 1))


def _meminfo(key):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1])
    return 0


def _mb(mb):
    return "%.1f gigabytes" % (mb / 1024) if mb >= 1024 else "%d megabytes" % mb
