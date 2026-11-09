"""Stop talking, and "status": is everything working."""
import os
import re
import urllib.request

from skills.base import Skill
from system import health

# Shared with the wake word detector, which listens for these even while JARVIS is talking.
STOP = re.compile(r"^\W*(?:ok(?:ay)?\W+)?(?:stop(?: talking| it| that)?|be quiet|quiet|shut up|enough|"
                  r"that'?s enough|cancel|never ?mind|silence)\W*$", re.IGNORECASE)

# "Falls wake": how it was typed (and may be heard).
FALSE_WAKE = (r"(?:(?:that|it|this) (?:was|is)(?: a| an)? |that'?s(?: a)? |it'?s(?: a)? |another )?(?:false|falls?|fake|wrong|mistaken)"
              r" (?:wake(?:[ -]?up)?|alarm|trigger)s?|you woke(?: up)? (?:by mistake|for nothing|on your own|"
              r"by yourself)|i (?:didn'?t|did not) call you|(?:nobody|no one|no-one) called you|"
              r"(?:that|it) (?:was ?n'?t|was not) (?:me|for you)")


class Status(Skill):
    group = "itself"
    does = ("Stops JARVIS talking; says whether it can hear, understand and talk; \"false wake\" marks the last "
            "wake-up as a mistake, for retraining the wake model")
    phrases = ("status", "stop (stop talking)", "false wake")
    gives = "speech and a status card"
    permission = "auto"
    checks = "checks speech recognition, mic, speaker, disk and memory each time"

    def patterns(self):
        return [
            (STOP, self.stop),
            (r"(?:jarvis |system )?status|health check|are you (?:ok|okay|working)|how are you running", self.status),
            (FALSE_WAKE, self.false_wake),
        ]

    def false_wake(self, m):
        """The last wake-up wasn't you: its audio is kept as "not the name" for the next training."""
        mark = getattr(self.j.whisper, "mark_false", None)
        marked = mark(m.string) if mark else 0
        if marked is None:
            self.say("I've already marked that one.")
        elif marked:
            self.say("Got it, that was a false wake. %d marked so far, for the next training." % marked)
        else:
            self.say("I haven't got a wake-up saved to mark.")

    def stop(self, m=None):
        self.j.speaker.stop()
        return True

    def status(self, m=None):
        j = self.j
        voice = {"idle": "listening for my name", "off": "voice input is off", "starting": "still starting"}.get(
            j.rest, j.rest)
        try:
            if getattr(j.whisper, "ears", False):  # Ears has no port: it's a process JARVIS started
                if not j.whisper._ping():
                    raise OSError
            else:
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
        total = getattr(st, "f_blocks", 0) * st.f_frsize / 1e9
        disk_bar = "\n\n```stack\nDisk: used %.0f%s, free %.0f | GB\n```" % (total - disk, " bad" if disk < 2 else "",
                                                                          disk) if total > disk else ""
        j.show({"title": "JARVIS status", "text": "\n".join(
            "- %s %s" % ("{bad}" if l in problems or "NOT" in l else "{ok}", l) for l in lines) + disk_bar})
        self.say("All good. I'm %s, and there's %.0f gigabytes of disk free." % (voice, disk) if not problems
                 else "Mostly fine, but: %s" % " ".join(problems))


def _meminfo(key):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key):
                return int(line.split()[1]) * 1024
    return 0
