"""Kokoro on the GPU, kept loaded in its own process (it takes ~15 s to load, and voice/speech.py restarts its
speech process whenever you talk over it). Runs from its own venv: kokoro.py <kokoro_dir>.

Over the socket: one JSON settings line, then one sentence per line in; raw 16-bit 24 kHz mono audio out."""
import ctypes
import json
import os
import socket
import socketserver
import sys
import threading
import time

RATE = 24000
RUN_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "jarvis-%d" % os.getuid())
SOCKET = os.path.join(RUN_DIR, "kokoro.sock")
IDLE_EXIT = 600  # seconds with JARVIS gone before giving the GPU memory back
LONG = 480  # the model takes 510 phonemes at most: a longer sentence is split into pieces
# espeak's IPA -> Kokoro's phonemes, longest first (Kokoro writes diphthongs as one letter).
IPA = (("eɪ", "A"), ("aɪ", "I"), ("ɔɪ", "Y"), ("aʊ", "W"), ("əʊ", "Q"), ("oʊ", "Q"), ("eə", "ɛː"),
       ("dʒ", "ʤ"), ("tʃ", "ʧ"), ("r", "ɹ"), ("ɐ", "ə"), ("ɚ", "əɹ"), ("ɾ", "T"), ("e", "ɛ"))


def from_ipa(text):
    for ipa, kokoro in IPA:
        text = text.replace(ipa, kokoro)
    return text


def phonemes(text, lexicon, espeak):
    """Kokoro's phonemes for a line, in pieces it can take. Words not in the lexicon (names, numbers) go to espeak."""
    pieces, piece = [], ""
    for raw in text.replace("’", "'").split():
        word = raw.strip("\"'()[]{}*_`“”‘")
        bare = word.rstrip(".,!?;:")
        end = word[len(bare):][:1]
        if bare:
            piece += lexicon.get(bare.lower()) or from_ipa(espeak(bare))
        piece += end + " "
        if len(piece) > LONG or (end and len(piece) > 300):
            pieces.append(piece)
            piece = ""
    return [p.strip()[:LONG + 20] for p in pieces + [piece] if p.strip()]


def is_up():
    with socket.socket(socket.AF_UNIX) as s:
        try:
            s.connect(SOCKET)
            return True
        except OSError:
            return False


def _espeak():
    lib = ctypes.CDLL("libespeak-ng.so.1")
    lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    lib.espeak_TextToPhonemes.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_int]
    lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
    lib.espeak_Initialize(1, 0, None, 0)  # 1: retrieval only, it doesn't play anything
    if lib.espeak_SetVoiceByName(b"English (Great Britain)") != 0:  # without a voice, espeak crashes on the first word
        raise RuntimeError("espeak has no British English voice")

    def say(word):
        text = ctypes.create_string_buffer(word.encode())
        at = ctypes.c_void_p(ctypes.addressof(text))
        out = []
        while at.value:
            out.append(lib.espeak_TextToPhonemes(ctypes.byref(at), 1, 2).decode())  # 1: UTF-8, 2: IPA
        return "".join(out)
    return say


def main(base):
    if is_up():
        return
    import numpy as np
    import torch
    from loguru import logger
    sys.path[0] = os.path.join(base, "kokoro", "pkg")  # in place of voice/, where this file is also "kokoro"
    from kokoro.model import KModel
    logger.disable("kokoro")

    model = KModel(config=base + "/kokoro/config.json", model=base + "/kokoro/kokoro-v1_0.pth").to("cuda").eval()
    espeak = _espeak()
    lexicon = {}
    with open(base + "/lexicon-gb-en.txt", encoding="utf8") as f:
        for row in f:
            word, _, sounds = row.strip().partition(" ")
            lexicon.setdefault(word, sounds.replace(" ", ""))
    packs = {}
    gpu = threading.Lock()
    state = {"clients": 0, "left": time.time()}

    def synth(text, voice, speed):
        out = []
        with gpu:
            if voice not in packs:
                packs[voice] = torch.load("%s/kokoro/voices/%s.pt" % (base, voice), map_location="cuda",
                                          weights_only=True)
            with torch.cuda.amp.autocast(dtype=torch.float16):  # less GPU memory (0.72 -> 0.58 GB), a bit faster
                for piece in phonemes(text, lexicon, espeak):
                    out.append(model(piece, packs[voice][len(piece) - 1], speed=speed).float().numpy())
        return (np.clip(np.concatenate(out), -1, 1) * 32767).astype("int16").tobytes() if out else b""

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
            state["clients"] += 1
            try:
                for line in self.rfile:
                    try:
                        audio = synth(line.decode().strip(), cfg["voice"], float(cfg.get("speed", 1.0)))
                    except Exception as e:  # e.g. out of GPU memory: gone, so JARVIS goes back to Piper
                        print("[kokoro] %r on %r" % (e, line), file=sys.stderr, flush=True)
                        leave(1)
                    if audio:
                        self.wfile.write(audio + pause)
            except (BrokenPipeError, ConnectionResetError):
                pass  # JARVIS cut it off: the rest of what was queued is dropped with the connection
            finally:
                state["clients"] -= 1
                state["left"] = time.time()

    def watch_idle():
        while True:
            time.sleep(30)
            if not state["clients"] and time.time() - state["left"] > IDLE_EXIT:
                leave(0)

    synth("Hello.", "bm_george", 1.0)  # the GPU's first run is slow: not on the first real reply
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    socketserver.ThreadingUnixStreamServer.daemon_threads = True
    server = socketserver.ThreadingUnixStreamServer(SOCKET, Voice)
    threading.Thread(target=watch_idle, daemon=True).start()
    print("[kokoro] ready", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1])
