"""Text-to-speech. Piper stays running with the voice loaded, so each reply starts instantly."""
import collections
import difflib
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import threading
import time

import numpy as np
import yaml

from system.terminal import RUN_DIR
from voice import kokoro, supertonic_voice

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# "Hmm, ..." is spoken as "Hmm. ...": a sentence break, so Piper leaves a small, natural pause.
_PAUSE = re.compile(r"\b(Hmm|Oh|Okay|Alright|Well|Oops),(?=\s)")
INBOX = os.path.join(RUN_DIR, "speak.fifo")
# Lines that only fill a wait while Claude thinks (jarvis.py _ask_claude): no apology goes in front of these.
FILLER = re.compile(r"^(?:let me think|thinking|one moment|asking claude|still on it|almost there|just a moment"
                    r"|this one's taking|still thinking)\b", re.I)
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "settings.yaml")


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def _like(word, said):
    """`word`, or the word in `said` that Whisper probably misheard it from ("clones" for "closing", "ck" for
    "ckay"). Numbers must match exactly: "set volume to 40" isn't the echo of "Setting the volume to 45"."""
    if word in said or word.isdigit():
        return word
    ratio = lambda other: difflib.SequenceMatcher(None, word, other).ratio()
    near = max((w for w in said if not w.isdigit()), key=ratio, default=None)
    return near if near and ratio(near) >= 0.6 else word


HEARD_RATE = 16000  # the mic's rate (voice/stt.py)


def _spectrum(x):
    """Power in 31 Hz bins every 10 ms of 16 kHz audio: (frames, bins)."""
    if len(x) < 512:
        return np.zeros((0, 257), np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x.astype(np.float32), 512)[::160] * np.hanning(512)
    return np.abs(np.fft.rfft(frames, axis=1)) ** 2


def echo_match(heard, played):
    """How much `heard` (the mic) is `played` (what JARVIS sent to the speaker, a few seconds around it):
    (score, extra). score, 0-1: the best correlation over every delay of the spectrum's shape frame by frame
    (harmonics and formants, loudness taken off: only the same voice saying the same thing matches; your voice
    scored at most 0.32 on your clips, its echo through the Echo Dot 0.57 up). extra: per mille of the mic's loud bits the echo can't
    explain at that delay, even with the room's reverb: someone talking in its pauses or between its harmonics."""
    H, P = _spectrum(heard), _spectrum(played)
    if not len(H) or len(P) < len(H):
        return 0.0, 0
    shape = lambda S: (lambda L: L - L.mean(1, keepdims=True))(np.log(S[:, 3:100] + 1e3))  # 94 Hz-3.1 kHz
    h, p = shape(H), shape(P)
    h -= h.mean(0)
    h /= np.linalg.norm(h) + 1e-9
    windows = np.lib.stride_tricks.sliding_window_view(p, len(h), axis=0)  # (delays, bins, frames)
    windows = windows - windows.mean(2, keepdims=True)
    corr = np.einsum("dbf,fb->d", windows, h) / (np.sqrt(np.einsum("dbf,dbf->d", windows, windows)) + 1e-9)
    lag = int(corr.argmax())
    he = 10 * np.log10(H[:, 3:110] + 1e2)
    pe = 10 * np.log10(P[lag:lag + len(H), 3:110] + 1e2)
    loud = pe > pe.max() - 25
    gain = np.median(he[loud] - pe[loud])  # the speaker and room, from where it was loud
    for i in range(1, len(pe)):
        pe[i] = np.maximum(pe[i], pe[i - 1] - 1.0)  # the room's reverb tail: 100 dB a second
    pe = np.maximum.reduce([pe, np.roll(pe, 1, 1) - 3, np.roll(pe, -1, 1) - 3])  # (pitch drifts, the window leaks)
    cells = he > 55  # clearly over the room's noise
    extra = ((he - (pe + gain) > 10) & cells).sum() * 1000 // max(1, cells.sum())
    return float(max(0.0, corr[lag])), int(extra)


def pick(text):
    """One line at random when `text` is a list of alternatives, so replies don't sound canned."""
    return random.choice(text) if isinstance(text, (list, tuple)) else text


_OKAY = re.compile(r"^(?:okay[,.!]?\s+)*okay[,.!]?(?:\s+|$)", re.I)
# A leading "Okay" sounds flat in Piper's voice: mostly the user's name instead, sometimes "sir".
INSTEAD_OF_OKAY = ["Sure, Chetak.", "Got it, Chetak.", "Alright, Chetak.", "Right, Chetak.", "Yes, sir."]
# How Piper should say the name (the chat still shows it spelled right).
SPOKEN = {"Chetak": "Chay-tuck"}


def _no_okay(text):
    if not isinstance(text, str) or not _OKAY.match(text):
        return text
    rest = _OKAY.sub("", text, count=1)
    return random.choice(INSTEAD_OF_OKAY) + (" " + rest[:1].upper() + rest[1:] if rest else "")


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
        self.piper_speaker = cfg.get("speaker")  # for Piper voices with several speakers (e.g. semaine: 1 = spike)
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
        self.played = collections.deque()  # (when it plays, 16 kHz samples) of the last 40 s sent to the speaker
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
        # Supertonic (CPU) or Kokoro (GPU) speaks instead of Piper's voice once it has loaded; Supertonic if both are set.
        self.server = supertonic_voice if cfg.get("supertonic_voice") else kokoro
        self.kokoro_voice = cfg.get("supertonic_voice") or cfg.get("kokoro_voice")
        self.kokoro_dir = os.path.expanduser(cfg.get("supertonic_dir" if cfg.get("supertonic_voice") else "kokoro_dir", ""))
        self._kokoro_next_try = 0.0
        self._kokoro_wait = 60
        if self.kokoro_voice and not (self.engine == "piper" and shutil.which("socat")
                                      and os.path.isfile(self.kokoro_dir + "/venv/bin/python")):
            print("[speech] %s or socat not found, staying with Piper" % self.server.__name__)
            self.kokoro_voice = None
        self._use_kokoro()  # starts loading now, so it's ready for the first replies

    def _use_kokoro(self):
        """True when Kokoro is loaded and can speak. If it isn't running, starts it (less often each time it fails)."""
        if not self.kokoro_voice:
            return False
        if self.server.is_up():
            self._kokoro_wait = 60  # it unloads itself when idle: restarting it then isn't a failure
            return True
        if time.time() >= self._kokoro_next_try:
            self._kokoro_next_try = time.time() + self._kokoro_wait
            self._kokoro_wait *= 2
            with open(os.path.expanduser("~/.local/share/jarvis/%s.log" % self.server.__name__.split(".")[-1]), "ab") as log:
                # Its own session: it stays loaded while JARVIS restarts.
                subprocess.Popen([self.kokoro_dir + "/venv/bin/python", "-W", "ignore", self.server.__file__, self.kokoro_dir],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        return False

    def say(self, text):
        """Speak without blocking; `text` may be a list of alternatives. Text is always printed (and shown in the chat panel) too."""
        if isinstance(text, (list, tuple)) and text and (self.quiet() or self.brief):
            text = min(text, key=len)
        text = _no_okay(pick(text))
        if text and not FILLER.match(text):  # "Sorry about that. Thinking." sounded odd: the apology waits for the answer
            if time.time() - self._interrupted < 30:
                text = "Sorry about that. " + text  # You talked over JARVIS just now.
            self._interrupted = 0.0
        if text:
            self.last = text
            self.history.append((time.time(), text))
        print("%s JARVIS: %s" % (time.strftime("%H:%M:%S"), text))  # times: to see how late an echo came
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
        for name, said in SPOKEN.items():
            line = re.sub(r"\b%s\b" % name, said, line)
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
            for _ in range(2):  # the second go: if Kokoro has just died, Piper takes this line
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
        (still) playing, and 90% of its words match, in order, one thing JARVIS just said (or a piece of it).
        Not all its words pooled together: common words like "just", "it", "that" made your own sentences match.
        While its audio is still playing, a misheard word counts too ("Clones Text Editor" for "Closing Text Editor"),
        and 80% is enough: Whisper adds a word ("Opening the project folder and")."""
        if started_at > self._ends_at() + 1.0:
            return False  # began well after JARVIS finished: that's you
        words = _words(heard)
        if not words:
            return False
        playing = started_at <= self._ends_at() + 1.0  # (+1 s: the speaker's lag varies; its tail came back as a command)
        for text in self.said_lately():
            said = _words(text)
            heard_words = [_like(w, said) for w in words] if playing else words
            blocks = difflib.SequenceMatcher(None, heard_words, said, autojunk=False).get_matching_blocks()
            if sum(b.size for b in blocks) / len(words) >= (0.8 if playing else 0.9):
                return True
        return False

    def _keep_played(self, when, pcm, rate):
        x = np.frombuffer(pcm, np.int16).astype(np.float32)
        if rate != HEARD_RATE and len(x):
            x = np.interp(np.arange(0, len(x), rate / HEARD_RATE), np.arange(len(x)), x).astype(np.float32)
        self.played.append((when, x))
        while self.played and self.played[0][0] < when - 40:
            self.played.popleft()

    def played_audio(self, since, until):
        """What JARVIS sent to the speaker between these times, at the mic's rate (silence where it sent nothing)."""
        out = np.zeros(max(0, int((until - since) * HEARD_RATE)), np.float32)
        for when, x in list(self.played):
            at = int(round((when - since) * HEARD_RATE))
            lo, hi = max(at, 0), min(at + len(x), len(out))
            if lo < hi:
                out[lo:hi] = x[lo - at:hi - at]
        return out

    # Seconds the speaker (Bluetooth: ~0.5 s measured) and the mic's timing can be behind what was sent, and ahead.
    SOUND_LATE, SOUND_EARLY = 3.0, 1.0
    # Its own voice: echo_match from 0.5 up, with under 15% unexplained. On two recordings through the Echo Dot this
    # caught 82-91% of its echo and none of your clips, alone or starting in its tail; talking over its whole line
    # at your usual level (14 dB under its echo) 36-54% passed for its echo.
    SOUND_ECHO, SOUND_EXTRA = 0.5, 150

    def sound_match(self, heard, started):
        """echo_match of the mic bit `heard` (16 kHz floats) that began at `started` against what JARVIS played
        around then; None if it played nothing then (nothing to compare: most of the time, and free)."""
        since, until = started - self.SOUND_LATE, started + len(heard) / HEARD_RATE + self.SOUND_EARLY
        if not any(when < until and when + len(x) / HEARD_RATE > since for when, x in list(self.played)):
            return None
        return echo_match(heard, self.played_audio(since, until))

    def is_echo_sound(self, heard, started):
        """True if the mic bit (floats -1 to 1, as the wake has it) is JARVIS's own voice by its sound, whatever
        words Whisper would make of it."""
        match = self.sound_match(np.asarray(heard, np.float32) * 32767, started)
        if match is None:
            return False
        print("[echo] sound %.2f, %d/1000 not its voice, %.1f s" % (match + (len(heard) / HEARD_RATE,)))  # (to tune)
        return match[0] >= self.SOUND_ECHO and match[1] < self.SOUND_EXTRA

    def said_lately(self):
        """What JARVIS said in the last 30 s: what its echo can still come back as. The latest line counts until
        30 s after it finished playing: a long answer's tail comes back more than 30 s after it was said."""
        now = time.time()
        lines = [text for when, text in self.history if when > now - 30]
        if self.history and not lines and self._ends_at() > now - 30:
            lines.append(self.history[-1][1])
        return lines

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
        self.played = collections.deque((w, x) for w, x in self.played if w < time.time())  # the rest never plays
        if self.engine == "spd-say":
            subprocess.Popen(["spd-say", "-S"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self):
        """Let queued speech finish (up to a few seconds), then shut Piper down."""
        if self._proc:
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=15)  # Kokoro is still making the later sentences while the first plays
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
        return self.quiet(), self.sink, tuple(sorted(self.tuning.items())), self._use_kokoro()

    def _pipeline(self):
        if self._proc and self._proc.poll() is None:
            return self._proc
        self._proc_key = self._key()
        quiet, use_kokoro = self._proc_key[0], self._proc_key[3]
        if use_kokoro:
            rate = self.server.RATE
        else:
            with open(self.voice + ".json") as f:
                rate = json.load(f)["audio"]["sample_rate"]
        if (quiet or self.sink) and shutil.which("pacat"):
            player = "pacat --raw --rate=%d --format=s16le --channels=1%s%s" % (
                rate, " --volume=%d" % (65536 * self.quiet_volume // 100) if quiet else "",
                " --device=%s" % shlex.quote(self.sink) if self.sink else "")
        else:
            player = "aplay -q -r %d -f S16_LE -t raw -" % rate
        piper = "%s --model %s %s --output-raw 2>/dev/null" % (
            shlex.quote(self.piper), shlex.quote(self.voice),
            " ".join("--%s %s" % kv for kv in sorted(self.tuning.items()))
            + (" --speaker %d" % int(self.piper_speaker) if self.piper_speaker is not None else ""))
        if use_kokoro:
            piper = "socat -t 60 - UNIX-CONNECT:%s 2>/dev/null" % shlex.quote(self.server.SOCKET)
        # Piper's audio passes through JARVIS on its way to the player, so it knows exactly how long
        # it will play (to ignore its own voice in the mic for that long, and no longer).
        self._proc = subprocess.Popen(piper, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      start_new_session=True)
        self._player = subprocess.Popen(player + " 2>/dev/null", shell=True, stdin=subprocess.PIPE,
                                        start_new_session=True)
        if use_kokoro:
            self._proc.stdin.write((json.dumps({
                "voice": self.kokoro_voice, "speed": 1.0 / self.tuning.get("length_scale", 1.0),
                "silence": self.tuning.get("sentence_silence", 0.25)}) + "\n").encode())
        threading.Thread(target=self._pump, args=(self._proc, self._player, rate * 2), daemon=True).start()
        return self._proc

    def _pump(self, piper, player, bytes_per_second):
        odd = b""  # (a chunk can end in half a sample)
        for chunk in iter(lambda: piper.stdout.read1(4096), b""):
            start = max(time.time(), self._audio_until)
            self._audio_until = start + len(chunk) / bytes_per_second
            whole = odd + chunk
            odd = whole[len(whole) // 2 * 2:]
            carried = len(whole) - len(chunk)  # (the half sample from before plays just before `start`)
            self._keep_played(start - carried / bytes_per_second, whole[:len(whole) // 2 * 2], bytes_per_second // 2)
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
