"""Supertonic 3 on the CPU (~0.7 GB, vs Kokoro's ~3.8 GB on the GPU), kept loaded in its own process, like
voice/kokoro.py and with the same socket protocol. Runs from its own venv (it needs Python 3.9+):
supertonic_voice.py <dir>, with the models in <dir>/models.

Over the socket: one JSON settings line, then one sentence per line in; raw 16-bit 44.1 kHz mono audio out."""
import json
import os
import socket
import socketserver
import sys
import threading
import time

RATE = 44100
RUN_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "jarvis-%d" % os.getuid())
SOCKET = os.path.join(RUN_DIR, "supertonic.sock")
IDLE_EXIT = 600  # seconds without saying anything before giving its memory back (JARVIS restarts it when needed)
STEPS = 5  # quality steps (default 8): 3 was quicker but jumped to a squeaky pitch and clipped; 5 is clean
# JARVIS's speed setting (length_scale 0.8 -> 1.25) was tuned for Kokoro; Supertonic sounds right at its own 1.05.
SPEED = 1.05 / 1.25


def is_up():
    with socket.socket(socket.AF_UNIX) as s:
        try:
            s.connect(SOCKET)
            return True
        except OSError:
            return False


def main(base):
    if is_up():
        return
    os.environ["SUPERTONIC_CACHE_DIR"] = base + "/models"
    import numpy as np
    from supertonic import TTS

    tts = TTS(auto_download=False)
    styles = {}
    lock = threading.Lock()
    state = {"used": time.time()}

    def synth(text, voice, speed):
        if not any(c.isalnum() for c in text):
            return b""
        with lock:
            if voice not in styles:
                styles[voice] = tts.get_voice_style(voice)
            wav, _ = tts.synthesize(text, voice_style=styles[voice], lang="en", total_steps=STEPS,
                                    speed=speed * SPEED, silence_duration=0)
            state["used"] = time.time()
        return (np.clip(np.asarray(wav).squeeze(), -1, 1) * 32767).astype("int16").tobytes()

    def leave(code):
        try:
            os.unlink(SOCKET)
        except OSError:
            pass
        os._exit(code)

    class Voice(socketserver.StreamRequestHandler):
        def handle(self):
            head = self.rfile.readline()
            if not head:
                return  # just is_up() checking
            cfg = json.loads(head)
            pause = bytes(2 * int(RATE * float(cfg.get("silence", 0.25))))
            try:
                for line in self.rfile:
                    try:
                        audio = synth(line.decode().strip(), cfg["voice"], float(cfg.get("speed", 1.0)))
                    except Exception as e:  # gone, so JARVIS goes back to Piper
                        print("[supertonic] %r on %r" % (e, line), file=sys.stderr, flush=True)
                        leave(1)
                    if audio:
                        self.wfile.write(audio + pause)
            except (BrokenPipeError, ConnectionResetError):
                pass  # JARVIS cut it off: the rest of what was queued is dropped with the connection

    def watch_idle():
        while True:
            time.sleep(30)
            with lock:  # not in the middle of a sentence
                if time.time() - state["used"] > IDLE_EXIT:
                    leave(0)

    synth("Hello.", "M5", 1.0)  # the first run is slow: not on the first real reply
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    socketserver.ThreadingUnixStreamServer.daemon_threads = True
    server = socketserver.ThreadingUnixStreamServer(SOCKET, Voice)
    threading.Thread(target=watch_idle, daemon=True).start()
    print("[supertonic] ready", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1])
