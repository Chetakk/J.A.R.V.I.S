"""Wake word: two detectors, picked with settings wake_word.engine.

whisper (default): a tiny voice detector (Silero VAD) cuts the mic into bits of speech; each short bit
    goes to Whisper with a hint of the names, and it wakes when the words begin or end with a name.
    "EV, open YouTube" in one breath runs straight away. Tuned on the user's own voice: with the hint
    Whisper heard 19 of 21 accented "EV"/"Jarvis" (the keyword model below managed about 13).
kws: sherpa-onnx keyword spotting (CPU only, no GPU), then records until you stop talking.

The names come from settings.yaml, so renaming the assistant needs no retraining.
"""
import collections
import fcntl
import os
import re
import struct
import subprocess
import termios
import threading
import time

import numpy as np
import sentencepiece
import sherpa_onnx

from voice.level import hear
from voice.listen import RATE, beep
from core.registry import NAME_AS_THING, _names_regex
from skills.status import STOP as INTERRUPT  # "stop", "be quiet", "enough"...
from system.terminal import RUN_DIR

MODELS = os.path.expanduser("~/.local/share/jarvis/models")
KWS_DIR = os.path.join(MODELS, "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
KWS_FILES = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
VAD_WINDOW = 512
# A short yes or no, to a question JARVIS is still saying.
ANSWER = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|go ahead|do it|please|no|nope|nah|don'?t)\b"
                    r"[\w\s',]{0,25}[.!?]*$", re.IGNORECASE)
VAD_TAIL = 0.5  # silence the voice detector waits for before ending a bit of speech
# The voice detector notices speech a moment after it starts, so a soft "s" or "j" ("set a timer",
# "Jarvis") can be missing from the bit Whisper gets. Each bit keeps this much audio from before it.
PRE_ROLL = 0.3


class WakeWord:
    """Calls on_wake(True) when a wake phrase is heard, then on_audio(pcm) with what was said after it
    (or on_wake(False) if nothing was said)."""

    def __init__(self, phrases, on_audio, on_wake=None, is_busy=None, threshold=0.10, boost=2.0, max_seconds=10,
                 is_deaf=None):
        self.is_deaf = is_deaf or (lambda: False)  # "mute the mic": I stop listening, the system mic stays live
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
            hear(raw)
            if self.is_deaf():
                continue
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
                beep("end")
                threading.Thread(target=self.on_audio, args=(b"".join(recent) + pcm,), daemon=True).start()
            recent.clear()
            stream = self.kws.create_stream()

    def _record_command(self, mic):
        return record_until_silence(self.vad, mic, self.max_seconds)


_GREETING = re.compile(r"^\W*(?:(?:hey|hi|hello|ok|okay|yo|oi|good (?:morning|afternoon|evening|night)|what'?s up|what are you doing|are you there)\W*)*$",
                       re.IGNORECASE)


_ABOUT_NAME = re.compile(r"\b(?:close|quit|exit|shut ?down|turn off|switch off|kill|restart|reload|reboot|relaunch|"
                         r"in|into|to|on) $", re.IGNORECASE)


def called_by_name(text, names):
    """A long name ("Jarvis") wakes anywhere; a short one ("EV") only at the start, as everyday talk has it:
    "Hey EV, open YouTube" -> True, "How was that EV?" / "I'm moving by EV." -> False."""
    long = [n for n in names if len(n.replace(" ", "")) > 5]
    if long and split_name(text, long)[0]:
        return True
    m = re.search(r"(?<![a-z])%s" % _names_regex(names), text, re.IGNORECASE)
    return bool(m and _GREETING.match(text[:m.start()]))


def split_name(text, names):
    """("EV, open YouTube." -> (True, "open YouTube")), ("What's up Jarvis?" -> (True, "")),
    ("open YouTube" -> (False, "")). The name can be anywhere; in the middle the rest of the sentence is the request."""
    m = re.search(r"(?<![a-z])%s" % _names_regex(names), text, re.IGNORECASE)
    if not m:
        return False, ""
    before, after = text[:m.start()], text[m.end():].strip(" ,.!?-")
    if re.match(NAME_AS_THING, text[m.end():], re.IGNORECASE):
        # "Jarvis project in VS Code", "open Jarvis project": the name is what the request is about.
        return True, (text[m.start():] if _GREETING.match(before) else text).strip(" ,.!?-")
    if _GREETING.match(before):
        # "EV. EV. Hey EV, open YouTube" -> "open YouTube": drop repeated calls of the name.
        call = re.compile(r"^(?:\W*(?:hey|hi|hello|ok|okay|yo|good (?:morning|afternoon|evening|night)|what'?s up)\b)*\W*%s[\s,.!?-]*" % _names_regex(names),
                          re.IGNORECASE)
        while call.match(after) and call.match(after).end():
            after = after[call.match(after).end():]
        return True, after
    if not after:
        # Name at the end: "open YouTube, Jarvis" -> the command is before it. Straight after one of
        # _ABOUT_NAME it's what the request is about: "close Jarvis", "make these changes in Jarvis".
        if _ABOUT_NAME.search(before):
            return True, (before + m.group(0)).strip(" ,.!?-")
        return True, before.strip(" ,.!?-")
    # Name in the middle: "Please Jarvis, open YouTube" -> the whole sentence is the request. Straight after
    # one of _ABOUT_NAME it's what the request is about, so the name stays.
    if _ABOUT_NAME.search(before):
        return True, (before + m.group(0) + " " + after).strip(" ,.!?-")
    return True, ("%s %s" % (before.strip(" ,.!?-"), after)).strip(" ,.!?-")


# Ends a follow-up early: "thanks", "okay, thank you", "alright, thanks. Thank you.", "that's all", "stop", "never mind".
# ("Tanks." is how Whisper hears both "thanks" and "thank you"; "I will thank you." is its "alright, thank you".)
_THANKS = r"(?:th?anks?(?: you)?(?: (?:so|very) much)?|thank you)"
STOP = re.compile(r"^\W*(?:(?:ok(?:ay)?|alright|all right|right|great|cool|perfect|i will)\W+)?"
                  r"(%s(?:\W+%s)*|that'?s all|that'?s it|stop|never ?mind|nothing|no thanks?)\W*$" % (_THANKS, _THANKS),
                  re.IGNORECASE)


class WhisperWake:
    """Wakes on any of `names` heard by Whisper. on_text(command, follow_up) gets what to do (follow_up:
    said without the name, after an answer); on_wake(True/False) reports listening state, like WakeWord."""

    # Seconds of mic audio still queued in the pipe when the newest bit was read: while JARVIS was busy
    # (transcribing, opening a folder) its own reply piled up there and looked as if said after it stopped.
    lag = 0.0
    on_cut = None  # see __init__

    def is_asking(self):
        """JARVIS is asking you a yes/no question (JARVIS passes its own check in)."""
        return False

    def said_lately(self):
        """What JARVIS said in the last half minute (JARVIS passes its own in)."""
        return []

    def is_echo_sound(self, seg, started):
        """The mic bit is JARVIS's own voice by its sound (JARVIS passes its own check in)."""
        return False

    def _called(self, text, started):
        """The name in `text` is yours, unless JARVIS just said its name and the rest is its line too ("the newest
        is JARVIS wake 2" came back). "Jarvis, take me to Chrome" over that line is still you."""
        named, rest = split_name(text, self.names)
        if not named:
            return False
        name = re.compile(r"(?<![a-z])%s" % _names_regex(self.names), re.IGNORECASE)
        return not (rest and any(name.search(line) for line in self.said_lately()) and self.is_echo(rest, started))

    def __init__(self, names, whisper, on_text, on_wake=None, is_busy=None, max_seconds=8, wait_seconds=8,
                 is_echo=None,
                 on_heard=None, follow_seconds=8, on_stop=None, is_talking=None, barge_in="any", is_deaf=None,
                 is_dictating=None, on_wait=None, is_asking=None, said_lately=None, on_cut=None,
                 is_echo_sound=None):
        self.names = names
        self.on_wait = on_wait or (lambda seconds: None)  # listening without the name for this many seconds
        self.is_deaf = is_deaf or (lambda: False)  # "mute the mic": I stop listening, the system mic stays live
        self.whisper = whisper
        # While dictating every phrase is text to type: no name needed, and "that's all" is for dictation to take.
        self.is_dictating = is_dictating or (lambda: False)
        self.on_text = on_text
        self.on_stop = on_stop  # "stop" said while JARVIS is talking
        # Talking over JARVIS with something new: it stops talking, but a question still with Claude goes on
        # (cutting that off lost your first request without a word). on_stop is for "stop" itself.
        self.on_cut = on_cut
        self.on_wake = on_wake or (lambda active: None)
        # on_heard(text, outcome): everything speech recognition heard, for checking it in the chat panel.
        self.on_heard = on_heard or (lambda text, outcome: None)
        self.is_busy = is_busy or (lambda: False)
        self.is_echo = is_echo or (lambda text, started: False)  # (heard text, start time) -> own voice?
        if is_asking:
            self.is_asking = is_asking
        if said_lately:
            self.said_lately = said_lately
        if is_echo_sound:
            self.is_echo_sound = is_echo_sound
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
        self.follow_until = 0.0  # when the current follow-up window ends (noise doesn't cut it short)
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
        # The windows fed to the voice detector (as long as its 30 s buffer), and how many samples so far:
        # a bit's .start counts from the last reset, so the audio before it can be found here.
        history, fed = collections.deque(maxlen=31 * RATE // VAD_WINDOW), 0
        last_sound, warned = time.time(), False
        while True:
            raw = mic.stdout.read(VAD_WINDOW * 2)
            if not raw:
                return
            hear(raw)
            self.audio_at = time.time()
            self.lag = _queued(mic.stdout) / (2.0 * RATE)
            if self.is_deaf():
                pending = np.zeros(0, dtype=np.float32)
                self.vad.reset()
                history.clear()
                fed = 0
                continue
            ints = np.frombuffer(raw, dtype=np.int16)
            if np.abs(ints).max() > 200:
                last_sound, warned = time.time(), False
            elif not warned and time.time() - last_sound > 30:
                print("[mic] no sound from the microphone for 30 s; is it muted or unplugged?")
                warned = True
            pending = np.concatenate([pending, ints.astype(np.float32) / 32768])
            while len(pending) >= VAD_WINDOW:
                self.vad.accept_waveform(pending[:VAD_WINDOW])
                history.append(pending[:VAD_WINDOW])
                fed += VAD_WINDOW
                pending = pending[VAD_WINDOW:]
            while not self.vad.empty():
                seg = np.array(self.vad.front.samples, dtype=np.float32)
                before = _audio_before(history, fed, self.vad.front.start, int(PRE_ROLL * RATE))
                self.vad.pop()
                yield np.concatenate([before, seg])

    def _listen(self, mic):
        self.vad.reset()
        for seg in self._segments(mic):
            seconds = len(seg) / RATE
            started = time.time() - self.lag - seconds - VAD_TAIL  # roughly when this bit of speech began
            if self.is_busy():
                if (self.is_talking() and self.on_stop and seconds <= self.max_seconds
                        and self._barge_in(seg, seconds, started)):
                    continue
                self.on_heard("", "skipped %.1f s of speech (JARVIS was talking or push-to-talk was on)" % seconds)
                continue
            if self.is_echo_sound(seg, started):  # the tail of its voice (Bluetooth lags): Whisper needn't run
                self.on_heard("", "ignored (my own voice, by its sound; %.1f s)" % seconds)
                continue
            # Counted from when you STARTED talking, so a long command after "EV" isn't cut off.
            dictating = self.is_dictating()
            waiting = started <= self.waiting_until or dictating
            if seconds > self.max_seconds and not waiting:
                self.on_heard("", "skipped %.0f s of long talk (not a call)" % seconds)
                continue
            pcm = (np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes()
            if dictating and getattr(self.whisper, "ears", False):  # Ears keeps only what has the name, unless told otherwise
                text = self.whisper.transcribe(pcm, plain=True)
            else:
                text = self.whisper.transcribe(pcm, command=waiting and not dictating)  # waiting: it's the command itself
            if self.is_deaf():  # muted while this was being transcribed: it was said before, but I'm not listening
                self.on_heard(text, "ignored (not listening: the mic is muted)")
                continue
            if not text:
                # The peak shows whether it was too quiet (under stt.MIN_PEAK) or Whisper found no words.
                if getattr(self.whisper, "ears", False) and not waiting:  # (Ears writes nothing without the name)
                    self.on_heard("", "no name in it (%.1f s)" % seconds)
                    continue
                self.on_heard("", "couldn't make out words (%.1f s, peak %d)" % (seconds, np.abs(seg).max() * 32767))
                continue
            # JARVIS's own voice coming back from the speaker (e.g. the tail of a Bluetooth delay).
            # Saying the name always gets through, in case you repeat its words on purpose (unless JARVIS said
            # the name itself). Right after the name alone JARVIS only beeped, so it's you ("Do a speed test"
            # was taken for its earlier answer).
            if (not waiting or self.following or dictating) and self.is_echo(text, started) and not self._called(text, started):
                self.on_heard(text, "ignored (my own voice)")
                continue
            if waiting:
                named, command = split_name(text, self.names)
                command = command if named else text
                if not command:
                    self.on_heard(text, "the name again; still waiting for the command")
                    if not dictating:  # you called it: a full new wait, and what comes next is no follow-up
                        self.following = False
                        self._wait(self.wait_seconds)
                    continue
                self.waiting_until = 0.0
                if STOP.match(command) and not dictating:
                    self.on_heard(text, "done; back to waiting for the name")
                    if INTERRUPT.match(command) and self.on_stop:  # "stop" also stops Claude if it's still thinking
                        self.on_stop()
                    self.on_wake(False)
                    continue
                beep("end")
                print("[wake] command: %r" % command)
                self.on_heard(text, "follow-up" if self.following and not named else "command")
                self.last_request = time.time()
                self.on_text(command, self.following and not named)
                continue
            named, command = split_name(text, self.names)
            if not named:
                self.on_heard(text, "ignored (no name at the start or end)")
                continue
            if not called_by_name(text, self.names):
                self.on_heard(text, "ignored (a short name only wakes me at the start)")
                continue
            if STOP.match(command) and not INTERRUPT.match(command):  # "Jarvis, thanks": nothing to do ("Jarvis, stop" still stops it)
                self.on_heard(text, "done; back to waiting for the name")
                continue
            if command:
                command = self._command_again(pcm, command)
            print("[wake] heard %r" % text)
            self.on_wake(True)
            if command:
                self.on_heard(text, "woke, command: %s" % command)
                beep("end")
                self.last_request = time.time()
                self.on_text(command, False)
            else:
                self.on_heard(text, "woke; say the command")
                beep()  # "EV?" on its own: listen for the command next
                self.following = False
                self._wait(self.wait_seconds)

    def _barge_in(self, seg, seconds, started):
        """Speech while JARVIS is talking: True if it was for JARVIS (it stops talking and handles it)."""
        if seconds > 2.5 and self.barge_in == "off":
            return False
        # (Ears writes down only what has the name unless it's a command, and "stop" has no name)
        text = self.whisper.transcribe((np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes(),
                                       command=getattr(self.whisper, "ears", False))
        if not text:
            return False
        named, rest = split_name(text, self.names)
        if INTERRUPT.match(rest if named else text):
            self.on_heard(text, "stopped talking")
            self.on_stop()
            return True
        if self.barge_in == "off":
            return False
        echo = self.is_echo(text, started)  # (started: from before transcribing, which takes a while)
        if named and not echo:  # (an echo of JARVIS saying its own name mustn't interrupt it)
            (self.on_cut or self.on_stop)()
            self.on_wake(True)
            if rest:
                rest = self._command_again((np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes(), rest)
                self._pass_on(text, rest, False, "interrupted, command: %s" % rest)
            else:
                self.on_heard(text, "interrupted; say the command")
                beep()
                self.following = False
                self._wait(self.wait_seconds)
            return True
        if not echo and self.is_asking() and ANSWER.match(text):
            # "Yeah" while the question is still playing (the speaker lags behind): the answer, not chatter
            (self.on_cut or self.on_stop)()
            self._pass_on(text, text, True, "answered the question")
            return True
        if (self.barge_in == "any" and time.time() - self.last_request < 60
                and not echo and not self.is_echo_sound(seg, started)):  # (its voice garbled past its words)
            (self.on_cut or self.on_stop)()
            self._pass_on(text, text, True, "interrupted, follow-up")
            return True
        self.on_heard(text, "ignored while talking (%s)" % ("my own voice" if echo
                                                            else "no name, not in a conversation"))
        return True

    def _command_again(self, pcm, command):
        """"EV, run avtec hs" in one breath was heard with the names-only hint (tuned for waking): hear it
        again with JARVIS's vocabulary for the command part (~0.3 s). Keeps the first try if that fails."""
        if getattr(self.whisper, "ears", False):  # no vocabulary to use, and it has already cut the name off
            return command
        try:
            named, again = split_name(self.whisper.transcribe(pcm, command=True), self.names)
        except Exception:
            return command
        return again if named and again else command

    def _pass_on(self, heard, command, follow_up, outcome):
        self.waiting_until = 0.0
        beep("end")
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
        if self.is_deaf():  # "I'm not listening" mustn't light the orb as if I were
            return
        self.on_wake(True)
        self.following = True
        self.follow_until = time.time() + self.follow_seconds
        self._wait(self.follow_seconds)

    def keep_following(self):
        """The follow-up was just noise ("Vee", a cough): keep listening without the name for the rest
        of the window instead of closing it."""
        left = self.follow_until - time.time()
        if self.following and left > 1.0:
            self.on_wake(True)
            self._wait(left)

    def _wait(self, seconds):
        self.on_wait(seconds)
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


def _queued(pipe):
    """Bytes waiting unread in `pipe` (0 if that can't be told)."""
    try:
        return struct.unpack("i", fcntl.ioctl(pipe.fileno(), termios.FIONREAD, b"\0\0\0\0"))[0]
    except (OSError, ValueError, AttributeError):
        return 0


def _audio_before(history, fed, start, count):
    """The `count` samples just before sample `start`, from the last windows fed (fewer if they're gone)."""
    first = fed - len(history) * VAD_WINDOW  # the sample history[0] begins at
    lo = max(start - count, first)
    if lo >= start:
        return np.zeros(0, dtype=np.float32)
    skip = (lo - first) // VAD_WINDOW
    windows = list(history)[skip:(start - first + VAD_WINDOW - 1) // VAD_WINDOW]
    offset = first + skip * VAD_WINDOW
    return np.concatenate(windows)[lo - offset:start - offset]


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
        hear(raw)
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
