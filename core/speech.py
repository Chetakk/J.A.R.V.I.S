"""Text-to-speech. Piper stays running with the voice loaded, so each reply starts instantly."""
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import threading
import time

import yaml

from core.terminal import RUN_DIR

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# "Hmm, ..." is spoken as "Hmm. ...": a sentence break, so Piper leaves a small, natural pause.
_PAUSE = re.compile(r"\b(Hmm|Oh|Okay|Alright|Well|Oops),(?=\s)")
INBOX = os.path.join(RUN_DIR, "speak.fifo")
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "settings.yaml")


def pick(text):
    """One line at random when `text` is a list of alternatives, so replies don't sound canned."""
    return random.choice(text) if isinstance(text, (list, tuple)) else text


def speak_via_jarvis(text):
    """Speak `text` through the running JARVIS, or directly with the same voice if it isn't running."""
    line = " ".join(text.split()) + "\n"
    try:
        fd = os.open(INBOX, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, line.encode())
        os.close(fd)
        return
    except OSError:
        pass
    with open(SETTINGS) as f:
        speaker = Speaker((yaml.safe_load(f) or {}).get("speech") or {})
    speaker.say(text)
    speaker.close()


class Speaker:
    def __init__(self, cfg):
        self.enabled = cfg.get("enabled", True)
        self.engine = cfg.get("engine", "piper")
        self.piper = os.path.expanduser(cfg.get("piper_bin", ""))
        self.voice = os.path.expanduser(cfg.get("voice", ""))
        self.tuning = ["--%s %s" % (k, float(cfg[k])) for k in
                       ("length_scale", "noise_scale", "noise_w", "sentence_silence") if k in cfg]
        self._proc = None
        self._busy_until = 0.0
        self.on_say = None  # callback(text), e.g. the chat panel
        self.quiet = lambda: False  # set by JARVIS: True during quiet hours (softer and shorter)
        self.quiet_volume = 40  # percent, during quiet hours
        self.last = ""  # the last thing said, for "what did you say?"
        self._interrupted = 0.0
        self._proc_quiet = False
        if self.engine == "piper" and not (os.path.isfile(self.piper) and os.path.isfile(self.voice)):
            print("[speech] Piper or voice not found, falling back to spd-say")
            self.engine = "spd-say"
        if self.engine == "spd-say" and not shutil.which("spd-say"):
            self.enabled = False

    def say(self, text):
        """Speak without blocking; `text` may be a list of alternatives. Text is always printed (and shown in the chat panel) too."""
        if isinstance(text, (list, tuple)) and text and self.quiet():
            text = min(text, key=len)
        text = pick(text)
        if text and time.time() - self._interrupted < 30:
            text = "Sorry about that. " + text  # You talked over JARVIS just now.
        self._interrupted = 0.0
        if text:
            self.last = text
        print("JARVIS: %s" % text)
        if self.on_say and text:
            self.on_say(text)
        if not self.enabled or not text:
            return
        line = " ".join(text.split())
        if self.engine == "piper" and self._proc and self._proc_quiet != self.quiet() and not self.busy():
            self.stop()  # Quiet hours started or ended: restart the player at the new volume.
        # Rough estimate of when playback ends (~14 characters per second), so the wake word
        # detector can ignore JARVIS's own voice.
        self._busy_until = max(time.time(), self._busy_until) + len(line) / 14.0 + 0.8
        if self.engine == "piper":
            # One sentence per line: Piper starts speaking the first while making the rest.
            sentences = _SENTENCE_END.split(_PAUSE.sub(r"\1.", line))
            try:
                self._pipeline().stdin.write(("\n".join(sentences) + "\n").encode())
                self._pipeline().stdin.flush()
                return
            except (BrokenPipeError, OSError):
                self._proc = None
                self.engine = "spd-say"
        subprocess.Popen(["spd-say", "--", line], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def busy(self):
        return time.time() < self._busy_until

    def done_at(self):
        """When JARVIS (roughly) stops talking."""
        return max(time.time(), self._busy_until)

    def listen_inbox(self):
        """Speak lines other programs (e.g. the Claude terminal's hook) write to the inbox pipe."""
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        if not os.path.exists(INBOX):
            os.mkfifo(INBOX, 0o600)

        def loop():
            # Opened read+write so the pipe never reports end-of-file between writers.
            with open(os.open(INBOX, os.O_RDWR), "r") as pipe:
                for line in pipe:
                    if line.strip():
                        self.say(line.strip())

        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        """Cut off whatever is being said."""
        if self.busy():
            self._interrupted = time.time()
        if self._proc:
            # Kill the whole piper | aplay group; it restarts on the next say().
            try:
                os.killpg(self._proc.pid, 9)
            except OSError:
                pass
            self._proc = None
        self._busy_until = 0.0
        if self.engine == "spd-say":
            subprocess.Popen(["spd-say", "-S"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self):
        """Let queued speech finish (up to a few seconds), then shut Piper down."""
        if self._proc:
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.stop()

    def _pipeline(self):
        if self._proc and self._proc.poll() is None:
            return self._proc
        with open(self.voice + ".json") as f:
            rate = json.load(f)["audio"]["sample_rate"]
        self._proc_quiet = self.quiet()
        if self._proc_quiet and shutil.which("pacat"):
            player = "pacat --raw --rate=%d --format=s16le --channels=1 --volume=%d" % (
                rate, 65536 * self.quiet_volume // 100)
        else:
            player = "aplay -q -r %d -f S16_LE -t raw -" % rate
        cmd = "%s --model %s %s --output-raw 2>/dev/null | %s 2>/dev/null" % (
            shlex.quote(self.piper), shlex.quote(self.voice), " ".join(self.tuning), player)
        self._proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, start_new_session=True)
        return self._proc
