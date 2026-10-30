"""Is JARVIS able to hear, understand and talk? Cheap checks (a few pactl calls), run by the watchdog.

problems() -> {key: spoken line}. Monitor.update() says new problems out loud AND as a desktop
notification (the speaker itself may be what's broken), and says when one is fixed.
"""
import subprocess
import time

LOW_MIC_VOLUME = 20  # percent; below this the wake word misses most of what you say


def _pactl(*args):
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _device(kind, name):
    """{"mute": bool, "volume": int} for the pactl source/sink called `name`, or None if it isn't there."""
    for block in _pactl("list", kind + "s").split("\n\n"):
        fields = dict(line.strip().split(": ", 1) for line in block.splitlines() if ": " in line)
        if fields.get("Name") == name:
            volume = fields.get("Volume", "")
            percent = [int(w[:-1]) for w in volume.split() if w.endswith("%") and w[:-1].isdigit()]
            return {"mute": fields.get("Mute") == "yes", "volume": max(percent) if percent else 100}
    return None


def problems(jarvis):
    """What's wrong right now, {key: spoken line}. Also refreshes jarvis.mutes (what's muted, for the tray icon);
    a mute you asked for yourself isn't a problem, so it's shown but never said."""
    found = {}
    mic = out = None
    info = dict(line.split(": ", 1) for line in _pactl("info").splitlines() if ": " in line)

    source = info.get("Default Source", "")
    if not source or source.endswith(".monitor"):
        found["mic"] = "I can't find a microphone."
    else:
        mic = _device("source", source)
        if not mic:
            found["mic"] = "Your microphone isn't connected."
        elif mic["mute"]:
            if "mic" not in jarvis.muted_on_purpose:
                found["mic"] = "Your microphone is muted."
        elif mic["volume"] < LOW_MIC_VOLUME:
            found["mic"] = "Your microphone volume is only %d percent, so I may not hear you." % mic["volume"]
    wake = getattr(jarvis, "wake", None)
    if "mic" not in found and wake and wake.mic and time.time() - wake.audio_at > 90:
        found["mic"] = "I'm not getting any sound from the microphone."

    sink = info.get("Default Sink", "")
    if not sink or sink == "auto_null":
        found["speaker"] = "There's no speaker connected."
    else:
        out = _device("sink", sink)
        if not out:
            found["speaker"] = "The speaker isn't connected."
        elif out["mute"] and "speaker" not in jarvis.muted_on_purpose:
            found["speaker"] = "The speaker is muted."
    # speaker.enabled is whether a voice is installed at all; speaker.muted is quiet mode, which you chose.
    if not jarvis.speaker.enabled:
        found["voice"] = "My voice isn't working: Piper and spd-say are both missing."
    jarvis.show_mutes(_asked=False, voice=jarvis.speaker.muted,
                      mic=bool(mic and mic["mute"]), speaker=bool(out and out["mute"]))

    whisper = getattr(jarvis, "whisper", None)
    if whisper and getattr(jarvis, "rest", "") != "starting" and not whisper._ping():
        found["stt"] = "Speech recognition isn't running, so I can't understand you. Typing still works."
    return found


WHY = ("Every 30 seconds I check that I can hear you (microphone), talk (speaker, voice) and understand you "
       "(speech recognition), and I tell you when that changes, as a notification too in case the speaker is the "
       "problem.")
FIXED = {"mic": "The microphone is working again.", "speaker": "The speaker is back.",
         "voice": "My voice is working again.", "stt": "Speech recognition is running again."}


class Monitor:
    def __init__(self, jarvis):
        self.jarvis = jarvis
        self.known = {}

    def update(self, announce=True):
        """Check now; tell the user about anything that broke or got fixed since the last check."""
        now = problems(self.jarvis)
        if announce:
            for key, line in now.items():
                if self.known.get(key) != line:
                    self._tell(line, urgent=True, kind="health:" + key)
            for key in self.known.keys() - now.keys():
                self._tell(FIXED.get(key, "That's fixed."), kind="health:" + key)
        self.known = now
        return now

    def _tell(self, line, urgent=False, kind="health"):
        print("[health] %s" % line)
        self.jarvis.skills.proactive.note(kind, line, WHY)
        subprocess.Popen(["notify-send", "-u", "critical" if urgent else "normal", "JARVIS", line],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.jarvis.inbox.put(("say", line))
