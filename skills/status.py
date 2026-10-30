"""Stop talking, and "status": is everything working."""
import os
import re
import urllib.request

from skills.base import Skill
from system import health

# Shared with the wake word detector, which listens for these even while JARVIS is talking.
STOP = re.compile(r"^\W*(?:ok(?:ay)?\W+)?(?:stop(?: talking| it| that)?|be quiet|quiet|shut up|enough|"
                  r"that'?s enough|cancel|never ?mind|silence)\W*$", re.IGNORECASE)


class Status(Skill):
    group = "itself"
    does = "Stops JARVIS talking; says whether it can hear, understand and talk"
    phrases = ("status", "stop (stop talking)")
    gives = "speech and a status card"
    permission = "auto"
    checks = "checks speech recognition, mic, speaker, disk and memory each time"

    def patterns(self):
        return [
            (STOP, self.stop),
            (r"(?:jarvis |system )?status|health check|are you (?:ok|okay|working)|how are you running", self.status),
        ]

    def stop(self, m=None):
        self.j.speaker.stop()
        return True

    def status(self, m=None):
        j = self.j
        voice = {"idle": "listening for my name", "off": "voice input is off", "starting": "still starting"}.get(
            j.rest, j.rest)
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/" % j.whisper_port, timeout=0.5)
            stt = "speech recognition is up"
        except Exception:
            stt = "speech recognition is NOT running"
        st = os.statvfs("/")
        disk = st.f_bavail * st.f_frsize / 1e9
        mem = _meminfo("MemAvailable") / 1e9
        claude = "Claude is available" if j.claude.bin else "Claude Code is not installed"
        lines = ["Voice: %s." % voice, "Speech: %s." % stt, "%s (%s mode)." % (claude, j.mode),
                 "Disk: %.1f GB free." % disk, "Memory: %.1f GB free." % mem]
        broken = list(health.problems(j).values())
        lines += broken
        problems = [l for l in lines if "NOT" in l or "off" in l] + (["Disk is nearly full."] if disk < 2 else [])
        problems += [l for l in broken if l not in problems]
        j.chat("sys", "status\n" + "\n".join(lines))
        j.show({"title": "JARVIS status", "text": "\n".join("- " + l for l in lines)})
        self.say("All good. I'm %s, and there's %.0f gigabytes of disk free." % (voice, disk) if not problems
                 else "Mostly fine, but: %s" % " ".join(problems))


def _meminfo(key):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key):
                return int(line.split()[1]) * 1024
    return 0
