""""What's slowing my computer": top CPU and memory users; offers to close the worst one, or deals with a frozen
window first (skills/frozen.py). Also "system usage" (rings for CPU, memory and GPU, with a CPU trend line kept
fresh while the card is up) and "show the temperatures" (the Jetson's sensors and your wireless devices' batteries).
Power draw is read only where the sensors allow it (the INA3221 rails are root-only on this Jetson)."""
import glob
import os
import signal
import subprocess
import threading
import time

from skills.base import Skill
from system import desktop

# Never offered for closing: the desktop, sound, JARVIS itself and its helpers.
PROTECTED = {"gnome-shell", "xorg", "x", "systemd", "pulseaudio", "pipewire", "gdm", "gnome-session-b",
             "python3", "whisper-server", "piper", "nvargus-daemon", "dbus-daemon", "ibus-daemon"}


class SlowComputer(Skill):
    group = "computer"
    does = "What's using the CPU and memory; offers to close the worst; system usage; temperatures and batteries"
    phrases = ("what's slowing my computer (top CPU/memory users, offers to close one)",
               "show the system usage (live rings for CPU, memory and GPU)",
               "show the temperatures (the Jetson's sensors and the mouse and keyboard batteries)",
               "what's taking up space (a donut of your home folder, and the disk used and free)")
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
            (r"(?:show |check |what are )?(?:me )?(?:the |my )?(?:jetson'?s? |computer'?s? |system |device )?"
             r"(?:temperatures|temps|sensors|thermals)|how hot is (?:the |my )?(?:jetson|machine|board|soc)|"
             r"is (?:the |my )?(?:jetson|computer|system|machine) (?:over ?heating|too hot|running hot)|"
             r"(?:jetson|board|soc|system) temperature",  # "how hot is the computer", "mouse battery": commands
             lambda m: self.sensors()),
            (r"what'?s (?:taking|eating|using) (?:up )?(?:all )?(?:my |the )?(?:disk ?space|disk|storage|space)|"
             r"(?:show |check )(?:me )?(?:my |the )?(?:disk|storage) usage|where did (?:all )?(?:my |the )?(?:disk )?"
             r"space go|(?:disk|storage) (?:space )?breakdown|what'?s (?:filling|using) (?:up )?(?:my |the )?"
             r"(?:disk|storage|sd card)", lambda m: self.disk()),
        ]

    def usage(self):
        """CPU, memory and GPU load together, read straight from /proc and sysfs (no shell, nothing chained); the
        card stays live for a minute, with a CPU trend line, while it's on screen."""
        history = []
        text, (cpu, used, gpu) = self._usage(history)
        self.j.show({"id": "usage", "title": "System usage", "text": text})
        self.say("The CPU is at %d percent, memory is %d percent used%s." % (
            cpu, used, ", and the GPU is at %d percent" % gpu if gpu is not None else ""))
        if self.j.ui:
            threading.Thread(target=self._keep_fresh, args=(history,), daemon=True).start()

    def _usage(self, history):
        cpu = _cpu_percent()
        total, free = _meminfo("MemTotal"), _meminfo("MemAvailable")
        used = 100 * (total - free) // max(total, 1)
        gpu = _gpu_percent()
        history.append(round(cpu))
        del history[:-30]
        rings = ["CPU: %d%%%s" % (cpu, _level(cpu)),
                 "Memory: %.1f/%.1f GB%s" % ((total - free) / 2 ** 20, total / 2 ** 20, _level(used))]
        if gpu is not None:
            rings.append("GPU: %d%%%s" % (gpu, _level(gpu)))
        text = "```ring\n%s\n```" % "\n".join(rings)
        if len(history) > 1:
            text += "\n\n```spark\nCPU: %s | %%\n```" % " ".join(str(v) for v in history)
        return text, (cpu, used, gpu)

    def _keep_fresh(self, history, every=2, until=60):
        end = time.time() + until
        while time.time() < end:
            time.sleep(every)
            ui = self.j.ui
            if not (ui and ui.card_open("usage")):
                return
            ui.show_card({"id": "usage", "title": "System usage", "text": self._usage(history)[0]})

    def disk(self):
        """What takes the room: your home folder's biggest folders as a donut, and the whole disk used and free."""
        self.say("One moment, measuring your home folder.")
        home = os.path.expanduser("~")
        try:
            out = subprocess.run(["du", "-x", "-d1", "--block-size=1M", home], capture_output=True, text=True,
                                 timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        sizes = {}
        for line in out.splitlines():
            size, _, path = line.partition("\t")
            if size.isdigit():
                sizes[path] = int(size) / 1024.0  # GB
        total = sizes.pop(home, None)
        if not total or not sizes:
            self.say("I couldn't measure your home folder.")
            return
        top = sorted(sizes.items(), key=lambda kv: -kv[1])
        shown, rest = top[:6], sum(v for _p, v in top[6:])
        shown_total = sum(v for _p, v in shown)
        rest += max(0.0, total - shown_total - rest)  # files straight in the home folder
        st = os.statvfs("/")
        free = st.f_bavail * st.f_frsize / 2 ** 30
        disk = st.f_blocks * st.f_frsize / 2 ** 30
        lines = ["```donut\n%s%s\nunit: GB\n```" % ("\n".join("%s: %.1f" % (os.path.basename(p), v) for p, v in shown),
                                                    "\nother: %.1f" % rest if rest >= 0.1 else "")]
        if disk:
            lines.append("```stack\nDisk: home %.0f, system %.0f, free %.0f%s | GB\n```" % (
                total, max(0.0, disk - free - total), free, " bad" if free < 2 else ""))
        self.j.show({"id": "disk", "title": "Disk space", "text": "\n\n".join(lines),
                     "choices": [{"label": "Open %s" % os.path.basename(p), "do": "open file %s" % p.replace(home, "~")}
                                 for p, _v in shown if not os.path.basename(p).startswith(".")][:3]})
        self.say("Your home folder takes %.0f gigabytes; the biggest is %s at %.1f. %.0f gigabytes are free." % (
            total, os.path.basename(shown[0][0]), shown[0][1], free))

    def sensors(self):
        temps = _temps()
        cells = _batteries()
        if not temps and not cells:
            self.say("I can't read any temperature sensors here.")
            return
        lines = []
        if temps:
            lines.append("```ring\n%s\n```" % "\n".join("%s: %d/100 °C%s" % (name, c, _heat(c))
                                                          for name, c in temps[:4]))
        gpu = _gpu_percent()
        if gpu is not None:
            lines.append("GPU load: **%d%%**" % gpu)
        lines.append("Power draw: *not readable without root on this Jetson*")
        if cells:
            lines += ["", "**Batteries**"] + ["- %s %s · %s" % (
                "{bad}" if level in ("low", "critical") or level.rstrip("%").isdigit() and int(level.rstrip("%")) < 20
                else "{ok}", model, level) for model, level in cells]
        self.j.show({"id": "sensors", "title": "Temperatures", "text": "\n".join(lines)})
        hottest = max(temps, key=lambda t: t[1]) if temps else None
        said = ("The hottest part is the %s at %d degrees%s." % (hottest[0], hottest[1], ", which is hot" if
                                                                hottest[1] >= 80 else "") if hottest else "")
        if cells:
            said += " " + " ".join("Your %s battery is %s." % (m.lower().replace("wireless ", ""), l.lower())
                                   for m, l in cells)
        self.say(said.strip())

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
        total = _meminfo("MemTotal") / 2 ** 20
        shown = cpu + [h for h in heavy if h not in cpu]
        text = "**CPU**\n```bars\n%s\n```\n\n**Memory**\n%s%s" % (
            "\n".join("%s: %d%%" % (p[0][:16], p[1]) for p in shown),
            "\n".join("- %s **%s** · %s" % ("{bad}" if p[2] >= 1500 else "{warn}" if p[2] >= 600 else "{ok}", p[0],
                                             "%.1f GB" % (p[2] / 1024) if p[2] >= 1024 else "%d MB" % p[2])
                      for p in sorted(shown, key=lambda p: -p[2])),
            "\n\n```stack\n: used %.1f%s, free %.1f | GB\n```" % (total - free, " bad" if free < 1.5 else "", free)
            if total > free else "")
        self.j.show({"title": "What's using the computer", "text": text})
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


def _level(percent):
    """A ring's colour: amber from 75%, red from 90%."""
    return " bad" if percent >= 90 else " warn" if percent >= 75 else ""


def _heat(celsius):
    return " bad" if celsius >= 85 else " warn" if celsius >= 70 else " ok"


def _gpu_percent():
    """How busy the whole GPU is. The load file counts against the current clock, which the power mode caps (25W:
    408 of 918 MHz), so scale it by current clock / the chip's top clock: 85% at 408 MHz is 38% of the GPU."""
    try:
        with open("/sys/devices/gpu.0/load") as f:
            load = int(f.read()) / 10  # per mille
    except (OSError, ValueError):
        return None
    try:
        freq = glob.glob("/sys/devices/gpu.0/devfreq/*/")[0]
        with open(freq + "cur_freq") as f, open(freq + "available_frequencies") as top:
            return round(load * int(f.read()) / max(map(int, top.read().split())))
    except (OSError, ValueError, IndexError):
        return round(load)


def _temps():
    """[(name, °C)] from the thermal zones, hottest of a kind once: CPU, GPU, SoC, CV, junction."""
    names = {"cpu": "CPU", "gpu": "GPU", "soc": "SoC", "cv": "Vision", "tj": "Junction"}
    found = {}
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            with open(os.path.join(zone, "type")) as f:
                kind = f.read().strip().lower().split("-")[0].rstrip("0123456789")
            with open(os.path.join(zone, "temp")) as f:
                c = int(f.read()) / 1000.0
        except (OSError, ValueError):
            continue
        if kind in names and -20 < c < 150:
            found[names[kind]] = max(found.get(names[kind], c), c)
    order = ["CPU", "GPU", "SoC", "Vision", "Junction"]
    return [(n, round(found[n])) for n in order if n in found]


def _batteries():
    """[(model, level)] of wireless devices (Logitech mice, keyboards): a percentage where they give one."""
    out = []
    for p in sorted(glob.glob("/sys/class/power_supply/*")):
        try:
            with open(os.path.join(p, "type")) as f:
                if f.read().strip() != "Battery":
                    continue
            model = open(os.path.join(p, "model_name")).read().strip() or os.path.basename(p)
        except OSError:
            continue
        level = ""
        for name, fmt in (("capacity", "%s%%"), ("capacity_level", "%s")):
            try:
                with open(os.path.join(p, name)) as f:
                    value = f.read().strip()
                if value:
                    level = fmt % value.lower()
                    break
            except OSError:
                pass
        out.append((model, level or "unknown"))
    return out


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
