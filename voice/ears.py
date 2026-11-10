"""Ears (wake_word.engine: ears): a tiny model trained on your voice hears "Jarvis"; the words go to whichever
transcriber ears.words picks, Parakeet or Whisper. Only that one is loaded.

Stands in for voice.stt.Whisper. Bits of speech with no name in them never reach the transcriber, so room talk costs a
few milliseconds of CPU and isn't written down. Trained by tools/wake_train.py; only knows "Jarvis", not the other names.
"""
import collections
import glob
import json
import os
import re
import signal
import struct
import subprocess
import threading
import time
import wave

import numpy as np
import onnxruntime as ort

from voice.stt import MIN_PEAK, RATE, Whisper, _clean, _peak

JARVIS = os.path.expanduser("~/.local/share/jarvis")
WAKE = os.path.join(JARVIS, "models", "wake")
DATA = os.path.join(JARVIS, "wake-data")  # for retraining (tools/wake_train.py): never in the repo
AFTER_NAME = 0.4  # seconds: less than this after the name is just its tail, not a command
BEFORE_CUT = 0.1  # seconds taken back from the end of the name, so the next word keeps its start


class Embedder:
    """openWakeWord's speech embedding: 96 numbers for every 80 ms of audio."""

    def __init__(self, threads=0):
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = threads
        self.mel = ort.InferenceSession(os.path.join(WAKE, "melspectrogram.onnx"), options, providers=["CPUExecutionProvider"])
        self.embed = ort.InferenceSession(os.path.join(WAKE, "embedding_model.onnx"), options, providers=["CPUExecutionProvider"])

    def __call__(self, x):
        """x: float32 samples in 16-bit scale."""
        mel = self.mel.run(None, {self.mel.get_inputs()[0].name: x[None].astype(np.float32)})[0].squeeze() / 10 + 2
        windows = np.stack([mel[i:i + 76] for i in range(0, len(mel) - 75, 8)])[..., None].astype(np.float32)
        return self.embed.run(None, {self.embed.get_inputs()[0].name: windows})[0].reshape(len(windows), 96)


class Ears:
    ears = True  # (voice/wake.py and the status check ask this)

    def __init__(self, cfg, stt=None):
        self.whisper = Whisper(stt or {}) if cfg.get("words") == "whisper" else None
        self.server = os.path.expanduser(cfg.get("parakeet_bin", JARVIS + "/whisper.cpp/build/bin/parakeet-serve"))
        self.model = os.path.expanduser(cfg.get("parakeet_model", JARVIS + "/models/parakeet/ggml-parakeet-tdt-0.6b-v3-q8_0.bin"))
        if self.whisper:  # (the watchdog finds the process by this name)
            self.server, self.model = self.whisper.server, self.whisper.model
        self.wake_model = os.path.expanduser(cfg.get("wake_model", WAKE + "/jarvis_wake.npz"))
        self.threads = int(cfg.get("threads", 4))
        self.cutoff = float(cfg.get("wake_cutoff", 0.7))
        self.strict = float(cfg.get("wake_cutoff_strict", 0.98))  # in a command: false hits would cut its start off
        self.min_confidence = self.whisper.min_confidence if self.whisper else float(cfg.get("min_confidence", -0.4))
        self.recent = collections.deque(maxlen=8)  # (text, confidence) of the last few things heard
        self.idle = float(cfg.get("idle_minutes", 10)) * 60  # 0 = keep Parakeet loaded
        self.ready = False
        self._proc = None
        self._asleep = False  # Parakeet unloaded on purpose: it comes back when it's needed
        self._sleeper = None
        self.used_at = time.time()
        self._lock = threading.RLock()  # one request at a time down the pipe; loading counts as one
        self.layers = self.embedder = None
        self.keep_hits = int(cfg.get("keep_wake_hits", 200))  # audio of each wake-up, to tell real ones from false later
        self.media_minutes = float(cfg.get("collect_media_minutes", 0))  # mic audio while something plays: not the name
        self.recorder = None
        self.is_deaf = lambda: False  # "mute the mic": then nothing is recorded either (jarvis.py sets it)

    @property
    def available(self):
        return all(os.path.isfile(p) for p in (self.server, self.model, self.wake_model))

    def set_names(self, names):  # the wake model knows its name; Parakeet takes no hints, Whisper does
        if self.whisper:
            self.whisper.set_names(names)

    def set_vocabulary(self, words):
        if self.whisper:
            self.whisper.set_vocabulary(words)

    def start(self):
        if not self.available:
            return False
        with np.load(self.wake_model) as saved:
            self.layers = [(saved["w%d" % i], saved["b%d" % i]) for i in range(3)]
        self.embedder = Embedder(threads=1)
        self._load()
        if self.media_minutes and not self.recorder:
            from skills.media import something_playing
            self.recorder = MediaRecorder(os.path.join(DATA, "media"), self.media_minutes,
                                          lambda: not self.is_deaf() and something_playing())
            self.recorder.start()
        if self.idle and not self._sleeper:
            self._sleeper = threading.Thread(target=self._watch_idle, daemon=True)
            self._sleeper.start()
        return self.ready

    def _load(self):
        """Start the transcriber unless it's running, and warm it up: its first request is slow (GPU setup)."""
        with self._lock:
            if self._alive():
                return
            if self.whisper:  # (its start() warms it up too)
                self.ready = self.whisper.start()
                self._asleep = False
                self.used_at = time.time()
                return
            self._proc = subprocess.Popen([self.server, self.model, str(self.threads)], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            self.ready = self._proc.stdout.readline().strip() == b"ready"
            self._asleep = False
            self.used_at = time.time()
            if self.ready:
                self._ask(b"\0\0" * RATE)

    def _alive(self):
        return self.whisper._ping() if self.whisper else bool(self._proc and self._proc.poll() is None)

    def _unload(self):
        if self.whisper:
            self.whisper.stop()
            return
        proc, self._proc = self._proc, None
        if proc:
            proc.terminate()
            proc.wait()

    def _sleep_if_idle(self):
        with self._lock:
            if self.idle and time.time() - self.used_at > self.idle and self._alive():
                self._unload()
                self._asleep = True

    def _watch_idle(self):
        while True:
            time.sleep(30)
            self._sleep_if_idle()

    def stop(self):
        with self._lock:
            self._unload()
            self._asleep = self.ready = False

    def _ping(self):
        return self._asleep or self._alive()

    def _score(self, windows):
        """Each window's chance of being the name just before its end (the classifier wake_train.py trained)."""
        x = windows.reshape(len(windows), -1)
        for w, b in self.layers[:-1]:
            x = np.maximum(x @ w.T + b, 0)
        w, b = self.layers[-1]
        return 1 / (1 + np.exp(-(x @ w.T + b).squeeze(1)))

    def name_end(self, pcm, cutoff):
        """Where the name ends in this speech, in samples; None if it isn't there."""
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        lead = RATE + RATE // 5  # room sound before it, as in training: the first window needs ~2 s behind it
        audio = np.concatenate([np.random.normal(0, 20, lead), x, np.random.normal(0, 20, RATE // 2)]).astype(np.float32)
        e = self.embedder(audio)
        windows = np.stack([e[i:i + 16] for i in range(len(e) - 15)])
        hits = np.where(self._score(windows) >= cutoff)[0]
        if not len(hits):
            return None
        end = (8 * (hits[0] + 15) + 76) * 160 - lead - int(BEFORE_CUT * RATE)  # (mel frames are 10 ms)
        return int(min(max(end, 0), len(x)))

    def _hear(self, pcm, plain=False):
        """(text, confidence) from the transcriber. plain: ordinary speech to type, so Whisper gets no hint."""
        with self._lock:
            self._load()
            self.used_at = time.time()
            if self.whisper:
                if plain:
                    return _clean(self.whisper._request(pcm)), 0.0
                text, _, confidence, _ = self.whisper.listen(pcm, command=True)
                return text, confidence
            line = self._ask(pcm)
        confidence, _, text = line.rstrip("\n").partition("\t")
        return _clean(text, whisper=False), float(confidence)

    def _ask(self, pcm):
        self._proc.stdin.write(struct.pack("<I", len(pcm) // 2) + pcm)
        self._proc.stdin.flush()
        line = self._proc.stdout.readline().decode("utf-8", "replace")
        if not line:
            raise RuntimeError("parakeet stopped")
        return line

    def _words(self, pcm, prefix=""):
        text, confidence = self._hear(pcm)
        self.recent.append((text, confidence))
        return "%s%s" % (prefix, text) if text else prefix.rstrip(", ")

    def transcribe(self, pcm, command=False, plain=False):
        """Same as Whisper.transcribe. command=False: "" unless the name is in it, as the name is all that's wanted."""
        if _peak(pcm) < MIN_PEAK:
            return ""
        if plain:
            return self._hear(pcm, plain=True)[0]
        end = self.name_end(pcm, self.strict if command else self.cutoff)
        if end is None:
            return self._words(pcm) if command else ""
        rest = pcm[end * 2:]
        if len(rest) >= AFTER_NAME * RATE * 2:
            text = self._words(rest, "Jarvis, ")
        else:
            threading.Thread(target=self._load, daemon=True).start()  # so it's awake by the time the command comes
            text = "Jarvis."
        if not command and self.keep_hits:
            self.keep_hit(pcm, text)
        return text

    def keep_hit(self, pcm, text):
        """Save what woke it (and what was heard), so false wake-ups can be trained away; the newest keep_hits stay."""
        folder = os.path.join(DATA, "hits")
        try:
            os.makedirs(folder, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
            write_wav(os.path.join(folder, name + ".wav"), pcm)
            playing = self.recorder.playing() if self.recorder else None
            with open(os.path.join(folder, "hits.jsonl"), "a") as f:
                f.write(json.dumps({"clip": name, "time": time.time(), "heard": text, "media_playing": playing}) + "\n")
            for old in sorted(glob.glob(os.path.join(folder, "*.wav")))[:-self.keep_hits]:
                os.remove(old)
        except Exception as e:  # never let keeping data get in the way of hearing you
            print("[ears] couldn't keep the wake-up audio: %r" % e)

    def mark_false(self, said=""):
        """"False wake": the latest wake-up wasn't you. Its clip moves to wake-data/false (training uses it as "not the
        name", and the newest-200 cleanup can't delete it). `said`: this request, whose own wake-up is skipped.
        Returns how many are marked in all, 0 if there was nothing to mark, or None if the latest was already marked."""
        hits, false = os.path.join(DATA, "hits"), os.path.join(DATA, "false")
        try:
            with open(os.path.join(hits, "hits.jsonl")) as f:
                woke = [json.loads(l) for l in f if l.strip()]
        except (OSError, ValueError):
            return 0
        words = lambda s: " ".join(re.findall(r"[a-z0-9']+", s.lower()))  # noqa: E731
        if woke and said and words(said) and words(said) in words(woke[-1]["heard"]):
            woke.pop()  # "Jarvis, false wake" woke it too: that one was you
        if not woke:
            return 0
        last = woke[-1]
        clip = last["clip"] + ".wav"
        if os.path.exists(os.path.join(false, clip)):
            return None
        if not os.path.exists(os.path.join(hits, clip)):
            return 0
        os.makedirs(false, exist_ok=True)
        os.rename(os.path.join(hits, clip), os.path.join(false, clip))
        with open(os.path.join(false, "false.jsonl"), "a") as f:
            f.write(json.dumps(last) + "\n")
        return len(glob.glob(os.path.join(false, "*.wav")))

    def unsure(self, said):
        """As Whisper.unsure: True if the latest thing heard containing `said` was doubtful."""
        for text, confidence in reversed(list(self.recent)):
            if said and said in text:
                return confidence < self.min_confidence
        return False


def write_wav(path, pcm):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


class MediaRecorder(threading.Thread):
    """While a show or song plays, records the mic in files of up to 5 minutes, until `minutes` are kept in all
    (across restarts). That's what the room sounds like when nobody calls JARVIS: training data for "not the name".
    Wake-ups during it are in hits.jsonl, so their seconds can be left out."""

    def __init__(self, folder, minutes, playing, poll=3.0, chunk=300):
        super().__init__(daemon=True)
        self.folder, self.limit, self.playing, self.poll, self.chunk = folder, minutes * 60, playing, poll, chunk
        self.proc = None

    def recorded(self):
        return sum(os.path.getsize(f) - 44 for f in glob.glob(os.path.join(self.folder, "*.wav"))) / (2 * RATE)

    def run(self):
        os.makedirs(self.folder, exist_ok=True)
        while self.recorded() < self.limit:
            try:
                if not self.playing():
                    time.sleep(self.poll)
                    continue
                left = int(min(self.chunk, self.limit - self.recorded())) + 1
                path = os.path.join(self.folder, "%s-%03d.wav" % (time.strftime("%Y%m%d-%H%M%S"), time.time() * 1000 % 1000))
                self.proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-d", str(left),
                                              path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while self.proc.poll() is None:
                    time.sleep(self.poll)
                    if not self.playing():
                        self.proc.send_signal(signal.SIGINT)  # (arecord finishes the file's header on SIGINT)
                        self.proc.wait()
            except Exception as e:  # keeps trying; recording is never worth breaking JARVIS for
                print("[ears] media recording: %r" % e)
                time.sleep(30)
        print("[ears] recorded %.0f minutes of the room while something played: enough for retraining" % (self.limit / 60))
