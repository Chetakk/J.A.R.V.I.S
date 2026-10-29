"""Push-to-talk: hold a key, speak, release. Audio is recorded with arecord (16 kHz mono)."""
import math
import struct
import subprocess
import threading
import time

from pynput import keyboard

RATE = 16000
MIN_SECONDS = 0.4


def active_window_class():
    """WM_CLASS of the focused window, e.g. ('code', 'Code'); empty tuple if unknown."""
    try:
        from Xlib import X, display
        d = display.Display()
        root = d.screen().root
        win_id = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType).value[0]
        cls = d.create_resource_object("window", win_id).get_wm_class() or ()
        d.close()
        return tuple(cls)
    except Exception:
        return ()


def active_window_title():
    """Title of the focused window (browser tab, terminal title); empty string if unknown."""
    try:
        from Xlib import X, display
        d = display.Display()
        root = d.screen().root
        win_id = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType).value[0]
        prop = d.create_resource_object("window", win_id).get_full_property(d.intern_atom("_NET_WM_NAME"), 0)
        d.close()
        return prop.value.decode("utf-8", "ignore") if prop else ""
    except Exception:
        return ""


def beep(freq=880, seconds=0.08):
    """A short tone so you know JARVIS is listening. Never blocks and never raises."""
    def play():
        n = int(22050 * seconds)
        pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / 22050))) for i in range(n))
        try:
            subprocess.run(["aplay", "-q", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"], input=pcm,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
    threading.Thread(target=play, daemon=True).start()


class Recorder:
    def __init__(self):
        self._proc = None
        self._chunks = []
        self._thread = None

    def start(self):
        self._chunks = []
        self._proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        for chunk in iter(lambda: self._proc.stdout.read(3200), b""):
            self._chunks.append(chunk)

    def stop(self):
        """Stop recording; returns raw 16-bit PCM."""
        if not self._proc:
            return b""
        self._proc.terminate()
        self._thread.join(timeout=1)
        self._proc = None
        return b"".join(self._chunks)


class PushToTalk:
    """Calls on_audio(pcm) after the key is held and released, unless used as a shortcut modifier.

    on_listen(True) when recording starts, on_listen(False) if it is cancelled or too short.
    """

    def __init__(self, cfg, on_audio, on_listen=None):
        key_name = cfg.get("key", "right_ctrl").replace("right_", "").replace("left_", "")
        side = "_r" if cfg.get("key", "right_ctrl").startswith("right_") else "_l"
        self.key = getattr(keyboard.Key, key_name + side, None) or getattr(keyboard.Key, key_name)
        self.pass_through = [w.lower() for w in cfg.get("pass_through_windows") or []]
        self.pass_through_titles = [w.lower() for w in cfg.get("pass_through_titles") or []]
        self.on_audio = on_audio
        self.on_listen = on_listen or (lambda active: None)
        self.recorder = Recorder()
        self._recording_since = None

    @property
    def recording(self):
        # Safety net: never stay "recording" for more than 30 s (e.g. a missed key release).
        if self._recording_since and time.time() - self._recording_since > 30:
            self._recording_since = None
            self.recorder.stop()
            self.on_listen(False)
        return self._recording_since is not None

    @recording.setter
    def recording(self, on):
        self._recording_since = time.time() if on else None

    def start(self):
        # pynput stops the listener for good if a callback raises, so errors are caught here.
        listener = keyboard.Listener(on_press=self._safe(self._press), on_release=self._safe(self._release),
                                     daemon=True)
        listener.start()
        listener.wait()  # Only report "ready" once key events are really arriving.

    def _safe(self, fn):
        def wrapper(key):
            try:
                fn(key)
            except Exception as e:
                print("[ptt] error: %r (push-to-talk keeps working)" % e)
                if self.recording:
                    self.recording = False
                    self.recorder.stop()
                    self.on_listen(False)
        return wrapper

    def _press(self, key):
        if key == self.key:
            if self.recording:
                return
            if self._focused_app_wants_key():
                print("[ptt] key left to %s (it's the focused window)" % "/".join(active_window_class()[-1:]))
                return
            self.recording = True
            self.on_listen(True)
            beep()
            self.recorder.start()
        elif self.recording:
            # Another key while holding it: it's a shortcut like Ctrl+C, not push-to-talk.
            self.recording = False
            self.recorder.stop()
            self.on_listen(False)

    def _focused_app_wants_key(self):
        classes = [c.lower() for c in active_window_class()]
        if any(p in c for p in self.pass_through for c in classes):
            return True
        title = active_window_title().lower() if self.pass_through_titles else ""
        return any(t in title for t in self.pass_through_titles)

    def _release(self, key):
        if key == self.key and self.recording:
            self.recording = False
            pcm = self.recorder.stop()
            if len(pcm) >= RATE * 2 * MIN_SECONDS:
                beep(660)
                threading.Thread(target=self.on_audio, args=(pcm,), daemon=True).start()
            else:
                print("[ptt] too short (%.1f s); hold the key while you speak" % (len(pcm) / (RATE * 2)))
                self.on_listen(False)
