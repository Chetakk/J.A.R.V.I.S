"""Text-to-speech. Piper stays running with the voice loaded, so each reply starts instantly."""
import collections
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

from system.terminal import RUN_DIR

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# "Hmm, ..." is spoken as "Hmm. ...": a sentence break, so Piper leaves a small, natural pause.
_PAUSE = re.compile(r"\b(Hmm|Oh|Okay|Alright|Well|Oops),(?=\s)")
INBOX = os.path.join(RUN_DIR, "speak.fifo")
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "settings.yaml")


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def pick(text):
    """One line at random when `text` is a list of alternatives, so replies don't sound canned."""
    return random.choice(text) if isinstance(text, (list, tuple)) else text


def speak_via_jarvis(text, why=None):
    """Speak `text` through the running JARVIS, or directly with the same voice if it isn't running.
    why: kept for "why did you say that"."""
    line = (json.dumps({"say": text, "why": why}) if why else " ".join(text.split())) + "\n"
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
        self.tuning = {k: float(cfg[k]) for k in ("length_scale", "noise_scale", "noise_w", "sentence_silence")
                       if k in cfg}
        self.default_speed = self.tuning.get("length_scale", 1.0)
        self.sink = None  # private mode: JARVIS's voice only goes to this output (headphones)
        self.muted = False  # quiet mode: nothing spoken, the text still goes to the chat panel
        self.brief = False  # brief answers: the shortest of the alternative lines
        self.on_sink_lost = None  # callback() when the private output disappears (headphones off)
        self._proc = None
        self._player = None
        self._busy_until = 0.0
        self._audio_until = 0.0  # when the audio already sent to the speaker finishes playing
        # Extra time the speaker needs to actually play it (Bluetooth adds a delay, often 0.3-1 s).
        self.output_delay = float(cfg.get("output_delay", 0.8))
        self.history = collections.deque(maxlen=12)  # (when, text) of what JARVIS said, to spot its own echo
        self.on_say = None  # callback(text), e.g. the chat panel
        self.on_message = None  # callback(dict) for {"show"/"do"} lines from tools/jarvis
        self.quiet = lambda: False  # set by JARVIS: True during quiet hours (softer and shorter)
        self.quiet_volume = 40  # percent, during quiet hours
        self.last = ""  # the last thing said, for "what did you say?"
        self._interrupted = 0.0
        self._proc_key = None  # what the running Piper and player were started with
        if self.engine == "piper" and not (os.path.isfile(self.piper) and os.path.isfile(self.voice)):
            print("[speech] Piper or voice not found, falling back to spd-say")
            self.engine = "spd-say"
        if self.engine == "spd-say" and not shutil.which("spd-say"):
            self.enabled = False

    def say(self, text):
        """Speak without blocking; `text` may be a list of alternatives. Text is always printed (and shown in the chat panel) too."""
        if isinstance(text, (list, tuple)) and text and (self.quiet() or self.brief):
            text = min(text, key=len)
        text = pick(text)
        if text and time.time() - self._interrupted < 30:
            text = "Sorry about that. " + text  # You talked over JARVIS just now.
        self._interrupted = 0.0
        if text:
            self.last = text
            self.history.append((time.time(), text))
        print("JARVIS: %s" % text)
        if self.on_say and text:
            self.on_say(text)
        if not self.enabled or not text or self.muted:
            return
        if self.sink and not _sink_exists(self.sink):
            self.sink = None
            if self.on_sink_lost:
                self.on_sink_lost()  # private stays private: not out loud on the speaker
            return
        line = " ".join(text.split())
        key = self._key()
        if self.engine == "piper" and self._proc and self._proc_key != key and (
                not self.busy() or self._proc_key[1] != key[1]):
            # Quiet hours or the speed changed: restart Piper and the player once it's done talking.
            # The output changed (private mode): at once, so nothing more goes out loud.
            self.stop()
        # A first guess until the real audio arrives (see _pump): Piper needs a moment to start.
        self._busy_until = max(time.time(), self._busy_until) + min(len(line) / 18.0, 1.5) + 0.5
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

    def _ends_at(self):
        """When the speech JARVIS sent ends: the real end of the audio, plus the speaker's delay."""
        played = self._audio_until + self.output_delay if self._audio_until else 0.0
        return max(self._busy_until, played)

    def busy(self):
        return time.time() < self._ends_at()

    def done_at(self):
        """When JARVIS stops talking (now, if it already has)."""
        return max(time.time(), self._ends_at())

    def is_echo(self, heard, started_at):
        """True if `heard` is JARVIS's own voice coming back through the mic: it began while JARVIS was
        (still) playing, and most of its words are in something JARVIS just said."""
        if started_at > self.done_at() + 1.0:
            return False  # began well after JARVIS finished: that's you
        words = _words(heard)
        if not words:
            return False
        spoken = set()
        for when, text in self.history:
            if when > time.time() - 30:
                spoken.update(_words(text))
        return bool(spoken) and sum(w in spoken for w in words) / len(words) >= 0.6

    def listen_inbox(self):
        """Speak lines other programs (e.g. the Claude terminal's hook) write to the inbox pipe."""
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        if not os.path.exists(INBOX):
            os.mkfifo(INBOX, 0o600)

        def loop():
            # Opened read+write so the pipe never reports end-of-file between writers.
            with open(os.open(INBOX, os.O_RDWR), "r") as pipe:
                for line in pipe:
                    line = line.strip()
                    if line.startswith("{") and self.on_message:  # tools/jarvis: {"show": ...} / {"do": ...}
                        try:
                            self.on_message(json.loads(line))
                        except ValueError:
                            pass
                    elif line:
                        self.say(line)

        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        """Cut off whatever is being said."""
        if self.busy():
            self._interrupted = time.time()
        for proc in (self._proc, self._player):
            # Kill Piper and the player; they restart on the next say().
            if proc:
                try:
                    os.killpg(proc.pid, 9)
                except OSError:
                    pass
        self._proc = self._player = None
        self._busy_until = self._audio_until = 0.0
        if self.engine == "spd-say":
            subprocess.Popen(["spd-say", "-S"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self):
        """Let queued speech finish (up to a few seconds), then shut Piper down."""
        if self._proc:
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=5)
                if self._player:
                    self._player.wait(timeout=max(1.0, self.done_at() - time.time()))
            except subprocess.TimeoutExpired:
                self.stop()

    def set_speed(self, length_scale):
        """Piper's length_scale: above 1 slower, below 1 faster. Takes effect from the next line said."""
        self.tuning["length_scale"] = round(max(0.6, min(1.6, length_scale)), 2)
        return self.tuning["length_scale"]

    def speed(self):
        return self.tuning.get("length_scale", 1.0)

    def _key(self):
        return self.quiet(), self.sink, tuple(sorted(self.tuning.items()))

    def _pipeline(self):
        if self._proc and self._proc.poll() is None:
            return self._proc
        with open(self.voice + ".json") as f:
            rate = json.load(f)["audio"]["sample_rate"]
        self._proc_key = self._key()
        quiet = self._proc_key[0]
        if (quiet or self.sink) and shutil.which("pacat"):
            player = "pacat --raw --rate=%d --format=s16le --channels=1%s%s" % (
                rate, " --volume=%d" % (65536 * self.quiet_volume // 100) if quiet else "",
                " --device=%s" % shlex.quote(self.sink) if self.sink else "")
        else:
            player = "aplay -q -r %d -f S16_LE -t raw -" % rate
        piper = "%s --model %s %s --output-raw 2>/dev/null" % (
            shlex.quote(self.piper), shlex.quote(self.voice),
            " ".join("--%s %s" % kv for kv in sorted(self.tuning.items())))
        # Piper's audio passes through JARVIS on its way to the player, so it knows exactly how long
        # it will play (to ignore its own voice in the mic for that long, and no longer).
        self._proc = subprocess.Popen(piper, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      start_new_session=True)
        self._player = subprocess.Popen(player + " 2>/dev/null", shell=True, stdin=subprocess.PIPE,
                                        start_new_session=True)
        threading.Thread(target=self._pump, args=(self._proc, self._player, rate * 2), daemon=True).start()
        return self._proc

    def _pump(self, piper, player, bytes_per_second):
        for chunk in iter(lambda: piper.stdout.read1(4096), b""):
            self._audio_until = max(time.time(), self._audio_until) + len(chunk) / bytes_per_second
            try:
                player.stdin.write(chunk)
                player.stdin.flush()
            except (BrokenPipeError, OSError):
                return
        try:
            player.stdin.close()
        except OSError:
            pass


def sinks():
    """[(name, description, form factor, active port)] of the audio outputs (pactl), or []."""
    try:
        out = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for block in out.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, _, value = line.strip().partition(": ")
            fields.setdefault(key, value)
            if line.strip().startswith("device.form_factor = "):
                fields["form"] = line.split("=", 1)[1].strip().strip('"')
        if fields.get("Name"):
            found.append((fields["Name"], fields.get("Description", ""), fields.get("form", ""),
                          fields.get("Active Port", "")))
    return found


def headphones(fallback=None):
    """The output that's headphones: one that says so (Bluetooth form factor, a name like "buds", a headphone
    port), else `fallback` (settings speech.private_sink, e.g. the jack) when it's there."""
    outputs = sinks()
    for name, desc, form, port in outputs:
        if form in ("headphone", "headset", "hands-free") or "headphone" in port or re.search(
                r"head ?(?:phone|set)|ear ?(?:bud|phone)s?|buds|airpods|\bwh-|\bwf-", desc, re.IGNORECASE):
            return name
    return next((n for n, _, _, _ in outputs if n == fallback), None) if fallback else None


def _sink_exists(name):
    try:
        out = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True  # can't tell: don't drop private mode over it
    return any(line.split("\t")[1:2] == [name] for line in out.splitlines())
