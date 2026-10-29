"""Wake word: two detectors, picked with settings wake_word.engine.

whisper (default): a tiny voice detector (Silero VAD) cuts the mic into bits of speech; each short bit
    goes to Whisper with a hint of the names, and it wakes when the words begin or end with a name.
    "EV, open YouTube" in one breath runs straight away. Tuned on the user's own voice: with the hint
    Whisper heard 19 of 21 accented "EV"/"Jarvis" (the keyword model below managed about 13).
kws: sherpa-onnx keyword spotting (CPU only, no GPU), then records until you stop talking.

The names come from settings.yaml, so renaming the assistant needs no retraining.
"""
import collections
import os
import re
import subprocess
import threading
import time

import numpy as np
import sentencepiece
import sherpa_onnx

from voice.listen import RATE, beep
from core.registry import _names_regex
from skills.status import STOP as INTERRUPT  # "stop", "be quiet", "enough"...
from system.terminal import RUN_DIR

MODELS = os.path.expanduser("~/.local/share/jarvis/models")
KWS_DIR = os.path.join(MODELS, "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
KWS_FILES = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
VAD_WINDOW = 512
VAD_TAIL = 0.5  # silence the voice detector waits for before ending a bit of speech


class WakeWord:
    """Calls on_wake(True) when a wake phrase is heard, then on_audio(pcm) with what was said after it
    (or on_wake(False) if nothing was said)."""

    def __init__(self, phrases, on_audio, on_wake=None, is_busy=None, threshold=0.10, boost=2.0, max_seconds=10):
        self.on_audio = on_audio
        self.on_wake = on_wake
        self.is_busy = is_busy or (lambda: False)  # True while JARVIS is talking or push-to-talk is active
        self.max_seconds = max_seconds
        self.kws = sherpa_onnx.KeywordSpotter(
            tokens=os.path.join(KWS_DIR, "tokens.txt"),
            encoder=os.path.join(KWS_DIR, "encoder-" + KWS_FILES),
            decoder=os.path.join(KWS_DIR, "decoder-" + KWS_FILES),
            joiner=os.path.join(KWS_DIR, "joiner-" + KWS_FILES),
            keywords_file=self._write_keywords(phrases, threshold, boost), keywords_threshold=threshold,
            num_threads=1, provider="cpu")
        vad_cfg = sherpa_onnx.VadModelConfig()
        vad_cfg.silero_vad.model = os.path.join(MODELS, "silero_vad.onnx")
        vad_cfg.silero_vad.min_silence_duration = 0.7
        vad_cfg.sample_rate = RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=30)

    @staticmethod
    def _write_keywords(phrases, threshold, boost):
        """Turn plain phrases into the model's sub-word tokens (one keyword per line)."""
        sp = sentencepiece.SentencePieceProcessor(model_file=os.path.join(KWS_DIR, "bpe.model"))
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        path = os.path.join(RUN_DIR, "wake_keywords.txt")
        with open(path, "w") as f:
            for p in phrases:
                f.write("%s :%s #%s @%s\n" % (" ".join(sp.encode(p.upper(), out_type=str)), boost, threshold,
                                              p.replace(" ", "_")))
        return path

    def start(self):
        threading.Thread(target=self._run_logged, daemon=True).start()

    def _run_logged(self):
        """Keep listening forever; if the microphone stream drops, reopen it."""
        while True:
            try:
                self._run()
                print("[wake] microphone stream stopped; reopening it")
            except Exception as e:
                print("[wake] error: %r; restarting the wake word" % e)
            time.sleep(1)

    def _run(self):
        mic = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            self._listen(mic)
        finally:
            mic.kill()

    def _listen(self, mic):
        stream = self.kws.create_stream()
        chunk_bytes = 1600 * 2  # 0.1 s
        recent = collections.deque(maxlen=8)  # last ~0.8 s, so words said right after the name aren't lost
        last_sound, warned = time.time(), False
        while True:
            raw = mic.stdout.read(chunk_bytes)
            if not raw:
                return
            recent.append(raw)
            if np.abs(np.frombuffer(raw, dtype=np.int16)).max() > 200:
                last_sound, warned = time.time(), False
            elif not warned and time.time() - last_sound > 30:
                print("[mic] no sound from the microphone for 30 s; is it muted or unplugged?")
                warned = True
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
            stream.accept_waveform(RATE, samples)
            while self.kws.is_ready(stream):
                self.kws.decode_stream(stream)
            heard = self.kws.get_result(stream)
            if not heard:
                continue
            self.kws.reset_stream(stream)
            if self.is_busy():
                print("[wake] heard '%s' but ignored it (JARVIS was talking or push-to-talk was active)" % heard)
                continue
            print("[wake] heard '%s', listening..." % heard)
            if self.on_wake:
                self.on_wake(True)
            beep()
            pcm = self._record_command(mic)
            if not pcm:
                print("[wake] no speech after the wake word")
                if self.on_wake:
                    self.on_wake(False)
            else:
                beep(660)
                threading.Thread(target=self.on_audio, args=(b"".join(recent) + pcm,), daemon=True).start()
            recent.clear()
            stream = self.kws.create_stream()

    def _record_command(self, mic):
        return record_until_silence(self.vad, mic, self.max_seconds)


_GREETING = re.compile(r"^\W*(?:(?:hey|hi|hello|ok|okay|yo|oi|what'?s up|what are you doing|are you there)\W*)*$",
                       re.IGNORECASE)


def split_name(text, names):
    """("EV, open YouTube." -> (True, "open YouTube")), ("What's up Jarvis?" -> (True, "")),
    ("open YouTube" -> (False, "")). The name must start or end what was said (after a greeting)."""
    m = re.search(r"(?<![a-z])%s" % _names_regex(names), text, re.IGNORECASE)
    if not m:
        return False, ""
    before, after = text[:m.start()], text[m.end():].strip(" ,.!?-")
    if _GREETING.match(before):
        # "EV. EV. Hey EV, open YouTube" -> "open YouTube": drop repeated calls of the name.
        call = re.compile(r"^(?:\W*(?:hey|hi|hello|ok|okay|yo|what'?s up)\b)*\W*%s[\s,.!?-]*" % _names_regex(names),
                          re.IGNORECASE)
        while call.match(after) and call.match(after).end():
            after = after[call.match(after).end():]
        return True, after
    if not after:
        # Name at the end: "open YouTube, Jarvis" -> the command is before it.
        return True, before.strip(" ,.!?-")
    return False, ""  # Name in the middle of a sentence: someone talking *about* Jarvis.


# Ends a follow-up early: "thanks", "that's all", "stop", "never mind".
STOP = re.compile(r"^\W*(thanks?|thank you|that'?s all|that'?s it|stop|never ?mind|nothing|no thanks?)\W*$",
                  re.IGNORECASE)


class WhisperWake:
    """Wakes on any of `names` heard by Whisper. on_text(command, follow_up) gets what to do (follow_up:
    said without the name, after an answer); on_wake(True/False) reports listening state, like WakeWord."""

    def __init__(self, names, whisper, on_text, on_wake=None, is_busy=None, max_seconds=8, wait_seconds=8,
                 is_echo=None,
                 on_heard=None, follow_seconds=8, on_stop=None, is_talking=None, barge_in="any"):
        self.names = names
        self.whisper = whisper
        self.on_text = on_text
        self.on_stop = on_stop  # "stop" said while JARVIS is talking
        self.on_wake = on_wake or (lambda active: None)
        # on_heard(text, outcome): everything speech recognition heard, for checking it in the chat panel.
        self.on_heard = on_heard or (lambda text, outcome: None)
        self.is_busy = is_busy or (lambda: False)
        self.is_echo = is_echo or (lambda text, started: False)  # (heard text, start time) -> own voice?
        # Talking over JARVIS: "any" = a new request stops it (no name needed in a conversation),
        # "named" = only with the name, "off" = only "stop". is_talking: JARVIS speaking (not push-to-talk).
        self.is_talking = is_talking or self.is_busy
        self.barge_in = barge_in
        self.last_request = 0.0  # when a command was last passed on: within a minute, it's a conversation
        self.mic, self.audio_at = None, time.time()  # for the watchdog: a mic that stops delivering is reopened
        self.max_seconds = max_seconds    # longer speech is conversation, not a wake phrase
        self.wait_seconds = wait_seconds  # after "EV?" alone, how long to wait for the command
        self.follow_seconds = follow_seconds  # after an answer, how long to listen without the name
        self.waiting_until = 0.0
        self.following = False  # the current wait is a follow-up (after an answer), not after "EV?"
        vad_cfg = sherpa_onnx.VadModelConfig()
        vad_cfg.silero_vad.model = os.path.join(MODELS, "silero_vad.onnx")
        vad_cfg.silero_vad.min_silence_duration = 0.5
        vad_cfg.silero_vad.min_speech_duration = 0.15
        vad_cfg.sample_rate = RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=30)

    def start(self):
        threading.Thread(target=self._run_logged, daemon=True).start()

    def _run_logged(self):
        while True:
            try:
                mic = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self.mic = mic
                try:
                    self._listen(mic)
                finally:
                    mic.kill()
                print("[wake] microphone stream stopped; reopening it")
            except Exception as e:
                print("[wake] error: %r; restarting the wake word" % e)
            time.sleep(1)

    def _segments(self, mic):
        """Yield each finished bit of speech (float32 samples) from the mic."""
        pending = np.zeros(0, dtype=np.float32)
        last_sound, warned = time.time(), False
        while True:
            raw = mic.stdout.read(VAD_WINDOW * 2)
            if not raw:
                return
            self.audio_at = time.time()
            ints = np.frombuffer(raw, dtype=np.int16)
            if np.abs(ints).max() > 200:
                last_sound, warned = time.time(), False
            elif not warned and time.time() - last_sound > 30:
                print("[mic] no sound from the microphone for 30 s; is it muted or unplugged?")
                warned = True
            pending = np.concatenate([pending, ints.astype(np.float32) / 32768])
            while len(pending) >= VAD_WINDOW:
                self.vad.accept_waveform(pending[:VAD_WINDOW])
                pending = pending[VAD_WINDOW:]
            while not self.vad.empty():
                seg = np.array(self.vad.front.samples, dtype=np.float32)
                self.vad.pop()
                yield seg

    def _listen(self, mic):
        self.vad.reset()
        for seg in self._segments(mic):
            seconds = len(seg) / RATE
            if self.is_busy():
                if self.is_talking() and self.on_stop and seconds <= self.max_seconds and self._barge_in(seg, seconds):
                    continue
                self.on_heard("", "skipped %.1f s of speech (JARVIS was talking or push-to-talk was on)" % seconds)
                continue
            started = time.time() - seconds - VAD_TAIL  # roughly when this bit of speech began
            # Counted from when you STARTED talking, so a long command after "EV" isn't cut off.
            waiting = started <= self.waiting_until
            if seconds > self.max_seconds and not waiting:
                self.on_heard("", "skipped %.0f s of long talk (not a call)" % seconds)
                continue
            text = self.whisper.transcribe((np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes())
            if not text:
                self.on_heard("", "couldn't make out words (%.1f s)" % seconds)
                continue
            # JARVIS's own voice coming back from the speaker (e.g. the tail of a Bluetooth delay).
            # Saying the name always gets through, in case you repeat its words on purpose.
            if self.is_echo(text, started) and not split_name(text, self.names)[0]:
                self.on_heard(text, "ignored (my own voice)")
                continue
            if waiting:
                named, command = split_name(text, self.names)
                command = command if named else text
                if not command:
                    self.on_heard(text, "the name again; still waiting for the command")
                    continue  # just the name again; keep waiting
                self.waiting_until = 0.0
                if STOP.match(command):
                    self.on_heard(text, "done; back to waiting for the name")
                    self.on_wake(False)
                    continue
                beep(660)
                print("[wake] command: %r" % command)
                self.on_heard(text, "follow-up" if self.following and not named else "command")
                self.last_request = time.time()
                self.on_text(command, self.following and not named)
                continue
            named, command = split_name(text, self.names)
            if not named:
                self.on_heard(text, "ignored (no name at the start or end)")
                continue
            print("[wake] heard %r" % text)
            self.on_wake(True)
            if command:
                self.on_heard(text, "woke, command: %s" % command)
                beep(660)
                self.last_request = time.time()
                self.on_text(command, False)
            else:
                self.on_heard(text, "woke; say the command")
                beep()  # "EV?" on its own: listen for the command next
                self.following = False
                self._wait(self.wait_seconds)

    def _barge_in(self, seg, seconds):
        """Speech while JARVIS is talking: True if it was for JARVIS (it stops talking and handles it)."""
        if seconds > 2.5 and self.barge_in == "off":
            return False
        text = self.whisper.transcribe((np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes())
        if not text:
            return False
        named, rest = split_name(text, self.names)
        if INTERRUPT.match(rest if named else text):
            self.on_heard(text, "stopped talking")
            self.on_stop()
            return True
        if self.barge_in == "off":
            return False
        started = time.time() - seconds - VAD_TAIL
        echo = self.is_echo(text, started)
        if named and not echo:  # (an echo of JARVIS saying its own name mustn't interrupt it)
            self.on_stop()
            self.on_wake(True)
            if rest:
                self._pass_on(text, rest, False, "interrupted, command: %s" % rest)
            else:
                self.on_heard(text, "interrupted; say the command")
                beep()
                self.following = False
                self._wait(self.wait_seconds)
            return True
        if (self.barge_in == "any" and time.time() - self.last_request < 60
                and not echo):
            self.on_stop()
            self._pass_on(text, text, True, "interrupted, follow-up")
            return True
        self.on_heard(text, "ignored while talking (%s)" % ("my own voice" if echo
                                                            else "no name, not in a conversation"))
        return True

    def _pass_on(self, heard, command, follow_up, outcome):
        self.waiting_until = 0.0
        beep(660)
        print("[wake] command: %r" % command)
        self.on_heard(heard, outcome)
        self.last_request = time.time()
        self.on_text(command, follow_up)

    def follow_up(self):
        """After an answer (or a yes/no question): once JARVIS stops talking, listen for the next request
        without the name for follow_seconds, with the orb lit."""
        if self.follow_seconds > 0:
            threading.Thread(target=self._follow_up, daemon=True).start()

    def _follow_up(self):
        time.sleep(0.3)  # let the reply start speaking
        while self.is_busy():
            time.sleep(0.1)
        self.on_wake(True)
        self.following = True
        self._wait(self.follow_seconds)

    def _wait(self, seconds):
        self.waiting_until = until = time.time() + seconds
        threading.Timer(seconds + 0.3, self._expire, args=(until,)).start()

    def _expire(self, until, was_talking=False):
        if self.waiting_until != until:
            return  # the command (or a new wait) already came
        if self.vad.is_speech_detected():
            # You're still talking: keep the window open until you finish.
            threading.Timer(0.5, self._expire, args=(until, True)).start()
            return
        if was_talking:
            # You just stopped: the voice detector hands the sentence over after a short silence, so
            # give it a moment to arrive before closing the window.
            threading.Timer(VAD_TAIL + 0.7, self._expire, args=(until, False)).start()
            return
        # Still waiting, and nothing was said after the name.
        self.waiting_until = 0.0
        print("[wake] nothing said; back to waiting for the name")
        self.on_wake(False)


def record_until_silence(vad, mic, max_seconds):
    """Read from the mic until speech ends (or nothing is said within 4 s)."""
    vad.reset()
    audio, pending = [], np.zeros(0, dtype=np.float32)
    started = False
    start = time.time()
    while time.time() - start < max_seconds:
        raw = mic.stdout.read(VAD_WINDOW * 2)
        if not raw:
            break
        audio.append(raw)
        pending = np.concatenate([pending, np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768])
        while len(pending) >= VAD_WINDOW:
            vad.accept_waveform(pending[:VAD_WINDOW])
            pending = pending[VAD_WINDOW:]
        if vad.is_speech_detected():
            started = True
        elif started and not vad.empty():
            break  # A full speech segment ended with silence.
        elif not started and time.time() - start > 4:
            return b""
    return b"".join(audio) if started else b""
