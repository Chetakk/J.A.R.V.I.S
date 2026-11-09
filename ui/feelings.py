"""What the idle eyes react to besides talk: the music playing (danced to on its beat, or swayed with when it's slow
or soft) and the computer itself (busy, hot, short of memory), and the hour (sleepy late at night).

Music is heard from the speakers' monitor through parec, only while a player says it's playing and the eyes are up.
"""
import subprocess
import threading
import time
from collections import deque

import numpy as np

RATE, HOP = 8000, 160  # 8 kHz mono, 20 ms frames: the beat envelope runs at 50 a second
LISTEN = 8  # seconds of music the beat is read from
LAG = 0.15  # shortcut: the speaker plays a moment after the monitor hears it; tune if the bob runs early (BT lags more)
DANCE_BPM = 95  # a steady beat this fast or faster is danced to; slower is swayed with
STEADY = 0.25  # how alike the beat is from one bar to the next (0..1) to count as a beat at all
# How far the bass swings between hits (log level, 10th to 90th percentile). A slow-looking beat that pumps this hard
# is a dance track in half time (kick on every other beat): danced to at double tempo. Measured: two dance songs 1.5
# and 1.8, soft piano 1.0, a soft slow beat 1.1. shortcut: set from two songs; tune with a real sad song if one dances.
PUMP = 1.4


def _onsets(audio):
    """The bass's onset envelope: how sharply the low end (kick drums) gets louder in each 20 ms frame."""
    bass = np.convolve(audio, np.ones(32) / 32, "same")  # ~250 Hz low-pass
    frames = bass[: len(bass) // HOP * HOP].reshape(-1, HOP)
    level = np.log(np.sqrt((frames ** 2).mean(1)) + 1)  # log: the system volume doesn't change the shape
    return np.maximum(0.0, np.diff(level)), level


def music_feel(audio):
    """(kind, bpm, last beat as seconds before the end) for a few seconds of 8 kHz audio. kind is "dance" for a steady
    lively beat, "sway" for a slow beat or beat-less music that never pauses, None for talk or silence."""
    audio = np.asarray(audio, dtype=np.float32)
    if len(audio) < RATE * 3:
        return None, 0.0, 0.0
    on, level = _onsets(audio)
    env = np.convolve(on, [0.25, 0.5, 0.25], "same")
    env = env - env.mean()
    ac = np.correlate(env, env, "full")[len(env) - 1:]
    lo, hi = 3000 // 180, 3000 // 55  # lags for 180..55 bpm
    if ac[0] <= 0:
        return None, 0.0, 0.0
    lag = lo + int(np.argmax(ac[lo:hi + 1]))
    while lag // 2 >= lo and ac[lag // 2] >= 0.5 * ac[lag]:  # the bass often lands every other beat: count them all
        lag //= 2
    bpm, steady = 3000.0 / lag, ac[lag] / ac[0]
    smooth = np.convolve(level, np.ones(3) / 3, "valid")
    if bpm < DANCE_BPM and bpm * 2 <= 180 and np.percentile(smooth, 90) - np.percentile(smooth, 10) >= PUMP:
        bpm, lag = bpm * 2, lag // 2
    recent = on[-lag:]  # the strongest onset within the last beat is where the beat is
    last = (len(recent) - int(np.argmax(recent))) * HOP / RATE
    if steady >= STEADY:
        return ("dance" if bpm >= DANCE_BPM else "sway"), bpm, last
    loud = level > np.median(level) - 1.5  # talk keeps stopping between words; music doesn't
    return ("sway" if loud.mean() > 0.92 else None), 0.0, 0.0


class Music:
    """Listens on a thread while `want` is set and a player is playing; kind, bpm and beat_at are read by the bar."""

    def __init__(self):
        self.want = False
        self.kind, self.bpm, self.beat_at = None, 0.0, 0.0
        self.track = ""  # "Song · Artist" while a player says what it plays
        threading.Thread(target=self._run, name="music-feel", daemon=True).start()

    def _run(self):
        from skills.media import playing_player, track
        proc, buf, checked, playing, felt = None, deque(maxlen=RATE * LISTEN // 800), 0.0, False, 0.0
        while True:
            now = time.time()
            if self.want and now - checked > 3:
                checked, player = now, playing_player()
                playing, self.track = bool(player), track(player) if player else ""
            if not (self.want and playing):
                if proc:
                    proc.kill()
                    proc.wait()
                    proc = None
                self.kind = None
                buf.clear()
                time.sleep(0.5)
                continue
            if not proc:
                try:
                    proc = subprocess.Popen(["parec", "--device=@DEFAULT_MONITOR@", "--format=s16le", "--channels=1",
                                             "--rate=%d" % RATE, "--raw", "--latency-msec=50"],
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                except OSError:
                    playing, checked = False, now + 60  # no parec: don't try again for a minute
                    continue
            chunk = proc.stdout.read(1600)  # 0.1 s
            if not chunk:
                proc = None
                continue
            buf.append(np.frombuffer(chunk, dtype=np.int16))
            if now - felt >= 1 and len(buf) * 800 >= RATE * 3:
                felt = now
                self.kind, self.bpm, last = music_feel(np.concatenate(buf))
                self.beat_at = now - last + LAG


def pc_mood(cpu, gpu, hottest, free, hour, busy_for):
    """The mood the computer's state calls for, the most pressing first: hot, straining (busy over a minute), busy
    (over 10 s), squeezed (under 10% memory free), sleepy (midnight to 6 am), else None."""
    if hottest >= 75:
        return "hot"
    if busy_for >= 60:
        return "straining"
    if busy_for >= 10:
        return "busy"
    if free < 0.10:
        return "squeezed"
    if 0 <= hour < 6:
        return "sleepy"
    return None


class Body:
    """The computer's state, read every few seconds on the GTK thread (a few small /proc and /sys files)."""

    def __init__(self):
        self.mood = None
        self.cpu = self.gpu = self.hottest = 0.0  # % busy, % busy, °C: the last readings, for the compact bar's words
        self.free = 1.0  # share of memory free
        self._cpu = None  # (total, idle) ticks at the last check
        self._busy_since = None

    def check(self):
        from skills.slow_computer import _gpu_percent, _meminfo, _temps
        with open("/proc/stat") as f:
            ticks = [int(x) for x in f.readline().split()[1:]]
        total, idle = sum(ticks), ticks[3] + ticks[4]
        cpu = 100 * (1 - (idle - self._cpu[1]) / max(total - self._cpu[0], 1)) if self._cpu else 0
        self._cpu = total, idle
        gpu = _gpu_percent() or 0
        now = time.time()
        self._busy_since = (self._busy_since or now) if max(cpu, gpu) >= 85 else None
        free = _meminfo("MemAvailable") / max(_meminfo("MemTotal"), 1)
        hottest = max((c for _n, c in _temps()), default=0)
        self.cpu, self.gpu, self.hottest, self.free = cpu, gpu, hottest, free
        self.mood = pc_mood(cpu, gpu, hottest, free, time.localtime().tm_hour, now - (self._busy_since or now))
        return True  # keep the GLib timer going

