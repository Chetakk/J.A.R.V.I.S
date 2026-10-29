"""Speech-to-text with whisper.cpp. The server keeps the model loaded on the GPU between requests."""
import array
import io
import json
import os
import subprocess
import time
import urllib.request
import uuid
import wave

RATE = 16000
MIN_PEAK = 1500  # 16-bit peak below this is background noise, not speech
HALLUCINATIONS = {"you", "thank you", "thanks", "thank you very much", "thanks for watching",
                  "thank you for watching", "bye", "so", "uh", "um", "hmm", ""}


class Whisper:
    def __init__(self, cfg):
        self.server = os.path.expanduser(cfg.get("server_bin", "~/.local/share/jarvis/whisper.cpp/build/bin/whisper-server"))
        self.model = os.path.expanduser(cfg.get("model", "~/.local/share/jarvis/models/ggml-base.en.bin"))
        self.port = int(cfg.get("port", 8178))
        self.threads = int(cfg.get("threads", 4))
        self.audio_ctx = int(cfg.get("audio_ctx", 0))  # 0 = Whisper's full 30 s window
        self.url = "http://127.0.0.1:%d/inference" % self.port
        self._proc = None
        self.ready = False  # started fine once; from then on the watchdog keeps it running
        self.hint = ""  # set_names() fills this
        self.command_hint = ""  # set_vocabulary(): the names plus JARVIS's own words, for commands

    def set_names(self, names):
        """Tell Whisper the assistant's names, e.g. "Jarvis. EV. Hey Jarvis. Hey EV."."""
        main = [n for n in names if " " not in n][:2]  # the tested hint: "Jarvis. EV. Hey Jarvis. Hey EV."
        spoken = [n.upper() if len(n) <= 3 else n.capitalize() for n in main]
        self.hint = " ".join(["%s." % n for n in spoken] + ["Hey %s." % n for n in spoken])

    @property
    def available(self):
        return os.path.isfile(self.server) and os.path.isfile(self.model)

    def start(self):
        """Start whisper-server and wait until the model is loaded."""
        args = [self.server, "-m", self.model, "--host", "127.0.0.1", "--port", str(self.port),
                "-t", str(self.threads), "-nt"] + (["-ac", str(self.audio_ctx)] if self.audio_ctx else [])
        if self._ping():
            # One left running by an earlier JARVIS: reused only if it runs these settings (else a model change
            # in settings.yaml would never take effect).
            if subprocess.call(["pgrep", "-xf", " ".join(args)], stdout=subprocess.DEVNULL) == 0:
                self.ready = True
                return True
            subprocess.call(["pkill", "-f", "%s -m .* --port %d " % (self.server, self.port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
        if not self.available:
            return False
        self._proc = subprocess.Popen(args,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self._ping():
                self._request(b"\0\0" * RATE)  # Warm up the GPU so the first real request is fast.
                self.ready = True
                return True
            if self._proc.poll() is not None:
                return False
            time.sleep(0.2)
        return False

    def stop(self):
        """Stop the server, including one left behind by an earlier JARVIS that this run reused."""
        if self._proc:
            self._proc.terminate()
            self._proc = None
        else:
            subprocess.call(["pkill", "-f", "%s -m %s" % (self.server, self.model)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def set_vocabulary(self, words):
        """Words commands use that Whisper would otherwise mishear ("Rhythmbox", not "rhythm box";
        "avtec-hs", not "ATECHS"). Only for commands: the wake check keeps its tested names-only hint."""
        words = [w for w in dict.fromkeys(words) if w][:60]  # Whisper reads at most ~224 tokens of prompt
        self.command_hint = (self.hint + " " + " ".join("%s." % w for w in words)).strip()

    def transcribe(self, pcm, command=False):
        """16 kHz mono 16-bit PCM in, text out ("" if nothing was said).
        command=True: it's a request (push-to-talk, or after the name), so JARVIS's vocabulary helps."""
        if _peak(pcm) < MIN_PEAK:
            return ""  # Too quiet to contain speech; Whisper would invent words.
        return _clean(self._request(pcm, (self.command_hint or self.hint) if command else self.hint))

    def _request(self, pcm, prompt=""):
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        boundary = uuid.uuid4().hex
        fields = [b'Content-Disposition: form-data; name="response_format"\r\n\r\njson\r\n']
        if prompt:
            # A hint of words to expect: turns accented "Dharvas" / "Eevee" into "Jarvis" / "EV".
            fields.append(b'Content-Disposition: form-data; name="prompt"\r\n\r\n%s\r\n' % prompt.encode())
        body = b"".join([
            b"--%s\r\n" % boundary.encode(),
            b'Content-Disposition: form-data; name="file"; filename="speech.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n", wav.getvalue(), b"\r\n"]
            + [b"--%s\r\n" % boundary.encode() + f for f in fields]
            + [b"--%s--\r\n" % boundary.encode()])
        req = urllib.request.Request(self.url, data=body,
                                     headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()).get("text", "")

    def _ping(self):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=0.5)
            return True
        except Exception:
            return False


def _peak(pcm):
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    return max((abs(s) for s in samples), default=0)


def _clean(text):
    text = " ".join(text.split())
    # Whisper writes non-speech as "[BLANK_AUDIO]", "(music)" etc., and invents a few
    # stock phrases from silence or noise.
    if not text or (text[0] in "[(" and text[-1] in "])"):
        return ""
    if text.lower().strip(" .!?,") in HALLUCINATIONS:
        return ""
    return text
