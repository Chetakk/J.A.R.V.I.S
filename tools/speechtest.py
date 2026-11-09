#!/usr/bin/env python3
"""Your own voice saying JARVIS's commands, and how well each speech setting hears them.

  tools/speechtest.py record     guided: shows a line, you read it (Enter = next, r = redo, q = stop and resume later)
  tools/speechtest.py score      word errors, right actions, wake hits and confidence for each setting
  tools/speechtest.py score-ears the same for Ears (voice/ears.py: the wake model and Parakeet)
  tools/speechtest.py record-wake  guided: "Jarvis" from around the room, for retraining the wake model

Recordings go through the wake loop's own voice detector, so a clip is what Whisper gets in real use. They stay
in ~/.local/share/jarvis/speech-test, out of this public repo. The list to read is tests/speech_commands.yaml.
"""
import difflib
import glob
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import wave

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from voice.stt import MIN_PEAK, RATE  # noqa: E402

DATA = os.path.expanduser("~/.local/share/jarvis/speech-test")
LINES = os.path.join(HERE, "tests", "speech_commands.yaml")
PARTS = (("commands", "Read each line the way you'd say it to JARVIS."),
         ("wake", "Now just the name, however each line says."),
         ("noise", "Last part: things JARVIS should ignore. Do what each line says."))
# Sent per request, so the running server needn't restart. hint: False = commands get only the names as a hint.
SETTINGS = {"now (greedy)": {}, "names-only hint": {"hint": False}, "beam 5": {"beam_size": 5}}
# record-wake: the places in the room (tests/wake_session.yaml), each with time to move there.
WAKE_PARTS = (("close", "Sit or stand about 30 cm from the mic, facing it."),
              ("arm", "Arm's length from the mic (about 1 m), facing it."),
              ("side", "Arm's length, but turned sideways: the mic is to your left or right."),
              ("across", "Across the room (2 to 3 m), facing the mic."),
              ("away", "Across the room, with your back to the mic."),
              ("far", "As far from the mic as the room allows: another corner, or the doorway."),
              ("seat", "Your usual seat, looking at your screen, not the mic. Say it the way each line says."),
              ("breath", "Anywhere you like: the name and the command in one go, no pause."),
              ("near", "Words that sound a bit like the name, said normally: JARVIS must stay asleep for these."),
              ("talk", "Last one: just talk normally, not to JARVIS."))
MOVE = False  # record-wake: wait for Enter at each new place
TIMEOUT = 15  # seconds to wait for someone to speak
NUMBERS = {"2": "two", "4th": "fourth", "5": "five", "10": "ten", "40": "forty", "40%": "forty", "to-do": "todo"}


def items():
    with open(LINES, encoding="utf-8") as f:
        lines = yaml.safe_load(f)
    return [(part, "%s-%02d" % (part, i), line) for part, _ in PARTS for i, line in enumerate(lines[part], 1)]


def path(clip):
    return os.path.join(DATA, clip + ".wav")


def grab(ears):
    """The next bit of speech from the mic as float32 samples, or None if nobody spoke in time."""
    mic = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(TIMEOUT, mic.kill)
    timer.start()
    try:
        ears.vad.reset()
        return next(ears._segments(mic), None)
    finally:
        timer.cancel()
        mic.kill()
        mic.wait()


def save(clip, seg):
    pcm = (np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes()
    with wave.open(path(clip), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def problem(part, line, seg):
    """Why this take should be redone, if it should. Never judged on what Whisper made of it: that's the test."""
    if part != "commands":
        return ""
    if np.abs(seg).max() * 32767 < MIN_PEAK:
        return "That was very quiet."
    if len(seg) / RATE < 0.15 * len(line.split()):
        return "That sounds cut off: a pause of half a second ends the clip, so say it in one go."
    return ""


def record():
    from voice.wake import WhisperWake  # (loads the voice detector; only recording needs it)
    os.makedirs(DATA, exist_ok=True)
    ears = WhisperWake([], None, lambda *a: None)
    todo = [it for it in items() if not os.path.exists(path(it[1]))]
    print(__doc__.split("\n")[0])
    print("\nBefore starting: in the JARVIS tray menu, tick \"Stop listening\", or it will act on what you read.")
    print("Speak normally. Only redo a line if you stumbled or something interrupted you, however it sounded to you.")
    print("\n%d clips to record. Enter = start, q = stop (it picks up where you left off)." % len(todo))
    if input("> ").strip().lower() == "q":
        return 0
    intro, i = None, 0
    while i < len(todo):
        part, clip, line = todo[i]
        if part != intro:
            intro = part
            print("\n=== %s" % dict(PARTS)[part])
            if MOVE and input("  Move there, then press Enter (q = stop): ").strip().lower() == "q":
                return 0
        print("\n[%d/%d]  %s" % (i + 1, len(todo), line.upper() if part != "noise" else line))
        seg = grab(ears)
        if seg is None:
            print("  Nothing heard in %d s." % TIMEOUT)
            answer = input("  Enter = try again, s = skip it, q = stop: ").strip().lower()
            if answer == "q":
                return 0
            i += answer == "s"
            continue
        save(clip, seg)
        print("  saved, %.1f s  %s" % (len(seg) / RATE, problem(part, line, seg)))
        answer = input("  Enter = next, r = redo, q = stop: ").strip().lower()
        if answer == "q":
            return 0
        if answer == "r":
            os.remove(path(clip))
            continue
        i += 1
    print("\nAll done. %d recordings in %s" % (len(glob.glob(path("*"))), DATA))
    return 0


def words(text):
    text = re.sub(r"[^\w\s'%-]", " ", text.lower().replace("jarvis,", "jarvis"))
    return [NUMBERS.get(w, w) for w in text.split()]


def errors(truth, heard):
    """(word errors, words said): insertions, deletions and swaps, as word error rate counts them."""
    truth, heard = words(truth), words(heard)
    matches = sum(b.size for b in difflib.SequenceMatcher(None, truth, heard, autojunk=False).get_matching_blocks())
    return max(len(truth), len(heard)) - matches, len(truth)


def load(clip):
    with wave.open(path(clip), "rb") as w:
        return w.readframes(w.getnframes())


def hear(whisper, pcm, command, form):
    """(text, seconds, confidence, no-speech probability) for one request, as Whisper reports them.
    A request that times out counts as heard nothing; the server finishes it first, so wait for that."""
    try:
        return whisper.listen(pcm, command=command, **form)
    except socket.timeout:
        print("    timed out")
        while True:
            try:
                whisper._request(b"\0\0" * RATE)
                return "", 30.0, -9.0, 1.0
            except socket.timeout:
                pass


def score():
    from tools.dryrun import DryJarvis
    from voice.stt import Whisper
    from voice.wake import split_name

    dry = DryJarvis()
    names = dry.j.names
    whisper = Whisper(dry.j.settings.get("stt") or {})
    whisper.set_names(names)
    whisper.set_vocabulary(dry.j.vocabulary())
    clips = [it for it in items() if os.path.exists(path(it[1]))]
    print("%d recordings\n" % len(clips))
    routes = {}

    def route(text):
        if text not in routes:
            routes[text] = dry.route(text)[0]
        return routes[text]

    results = {}
    vocabulary = whisper.command_hint
    for setting, form in SETTINGS.items():
        form = dict(form)
        whisper.command_hint = vocabulary if form.pop("hint", True) else whisper.hint
        rows, spent = [], []
        for part, clip, line in clips:
            pcm = load(clip)
            said = re.sub(r"\s*\(.*\)$", "", line)
            named_line = part == "wake" or bool(split_name(said, names)[0])
            # The wake loop's order: the name-only hint first when the name is in it, then JARVIS's vocabulary.
            first = hear(whisper, pcm, not named_line and part != "noise", form)
            text, took, sure, blank = first
            spent.append(took)
            row = {"part": part, "clip": clip, "said": said, "heard": text, "sure": sure, "blank": blank}
            if part == "wake":
                row["ok"] = bool(split_name(text, names)[0])
            elif part == "commands":
                want = split_name(said, names)[1] if named_line else said
                if named_line:
                    woke, _ = split_name(text, names)
                    again = hear(whisper, pcm, True, form)
                    spent[-1] += again[1]
                    text, sure, blank = (split_name(again[0], names)[1] or again[0]), again[2], again[3]
                    row.update(heard=text, sure=sure, blank=blank, woke=woke)
                row["want"] = want
                row["errors"], row["words"] = errors(want, text)
                row["same"] = route(text) == route(want) if text else False
            rows.append(row)
        results[setting] = rows
        report(setting, rows, spent)
    with open(os.path.join(DATA, "results.json"), "w") as f:
        json.dump(results, f, indent=1)
    print("Every clip's result: %s" % os.path.join(DATA, "results.json"))
    return 0


def score_ears():
    """Ears (voice/ears.py) on the recordings, through the same calls the wake loop makes."""
    from tools.dryrun import DryJarvis
    from voice.ears import Ears
    from voice.wake import split_name

    dry = DryJarvis()
    names = dry.j.names
    cfg = dict(dry.j.settings.get("ears") or {}, **({"words": sys.argv[2]} if len(sys.argv) > 2 else {}))  # score-ears whisper
    ears = Ears(cfg, dry.j.settings.get("stt") or {})
    ears.set_names(names)
    ears.set_vocabulary(dry.j.vocabulary())
    if not ears.start():
        print("Ears couldn't start: is everything in ~/.local/share/jarvis/models/wake and parakeet-serve built?")
        return 1
    routes, rows, spent, woke_wrongly = {}, [], [], []

    def route(text):
        routes.setdefault(text, dry.route(text)[0])
        return routes[text]

    for part, clip, line in [it for it in items() if os.path.exists(path(it[1]))]:
        pcm = load(clip)
        said = re.sub(r"\s*\(.*\)$", "", line)
        named_line = part == "wake" or bool(split_name(said, names)[0])
        began = time.time()
        text = ears.transcribe(pcm, command=part == "commands" and not named_line)
        spent.append(time.time() - began)
        row = {"part": part, "clip": clip, "said": said, "heard": text, "sure": ears.recent[-1][1] if ears.recent else 0.0}
        if part == "wake":
            row["ok"] = bool(split_name(text, names)[0])
        elif part == "commands":
            want = split_name(said, names)[1] if named_line else said
            if named_line:
                row["woke"], text = split_name(text, names)
                row["heard"] = text
            elif ears.transcribe(pcm):
                woke_wrongly.append(clip)  # said with no name: would this have woken it?
            row["want"] = want
            row["errors"], row["words"] = errors(want, text)
            row["same"] = bool(text) and route(text) == route(want)
        elif text:
            woke_wrongly.append(clip)
        rows.append(row)
    ears.stop()
    report("ears (%s)" % (cfg.get("words") or "parakeet"), rows, spent, (-1.0, -0.6, -0.4, -0.3, -0.2, -0.15, -0.1, -0.05))
    print("  name-in-one-breath woke  %d/%d" % (sum(r.get("woke", False) for r in rows), sum("woke" in r for r in rows)))
    print("  woke with no name        %s" % (woke_wrongly or "none"))
    return 0


def report(setting, rows, spent, cuts=(-1.0, -0.8, -0.7, -0.6, -0.5, -0.4, -0.3)):
    cmds = [r for r in rows if r["part"] == "commands"]
    wake = [r for r in rows if r["part"] == "wake"]
    noise = [r for r in rows if r["part"] == "noise"]
    print("=== %s (%.0f ms a clip)" % (setting, 1000 * sum(spent) / max(len(spent), 1)))
    if cmds:
        print("  word errors   %.1f%%" % (100.0 * sum(r["errors"] for r in cmds) / max(sum(r["words"] for r in cmds), 1)))
        print("  right action  %d/%d" % (sum(r["same"] for r in cmds), len(cmds)))
        for r in cmds:
            if not r["same"]:
                print("    said %-44r heard %-44r  %.2f" % (r["want"], r["heard"], r["sure"]))
    if wake:
        print("  name heard    %d/%d" % (sum(r["ok"] for r in wake), len(wake)))
    if noise:
        print("  noise         %s" % ", ".join("%r %.2f" % (r["heard"][:30], r["sure"]) for r in noise))
    good = [r["sure"] for r in cmds if r["same"]]
    bad = [r["sure"] for r in cmds if not r["same"] and r["heard"]] + [r["sure"] for r in noise if r["heard"]]
    if good and bad:
        print("  if clips under a confidence were refused:")
        for cut in cuts:
            print("    %.1f  junk refused %d/%d, good commands lost %d/%d" %
                  (cut, sum(s < cut for s in bad), len(bad), sum(s < cut for s in good), len(good)))
    print()


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "record":
        sys.exit(record())
    if what == "record-wake":
        DATA = os.path.join(os.path.expanduser("~/.local/share/jarvis/wake-data"), "mine")
        LINES, PARTS, MOVE = os.path.join(HERE, "tests", "wake_session.yaml"), WAKE_PARTS, True
        sys.exit(record())
    if what == "score":
        sys.exit(score())
    if what == "score-ears":
        sys.exit(score_ears())
    print(__doc__)
    sys.exit(2)
