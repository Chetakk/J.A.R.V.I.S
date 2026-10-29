"""Keeps a long-running JARVIS healthy: notices hangs that a crash-restart wouldn't.

Every 30 s: whisper-server still answering (else restart it), the mic still delivering audio (else
reopen it), no request stuck for ages (else exit; tools/run_jarvis.sh starts JARVIS again).
Then core/health.py: anything still broken (mic, speaker, speech recognition) is said out loud.
Every 10 min: logs memory use; restarts whisper, or JARVIS itself when idle, if one grows past its limit.
"""
import os
import time

from core import health

CHECK_SECONDS = 30
MEMORY_EVERY = 20  # checks, i.e. every 10 minutes


def rss_mb(pid):
    try:
        with open("/proc/%d/status" % pid) as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def whisper_pid(whisper):
    if whisper._proc and whisper._proc.poll() is None:
        return whisper._proc.pid
    for pid in os.listdir("/proc"):  # a server reused from an earlier run
        if pid.isdigit():
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    if os.path.basename(whisper.server).encode() in f.read().split(b"\0")[0]:
                        return int(pid)
            except OSError:
                pass
    return None


def run(jarvis, cfg):
    stuck = cfg.get("stuck_minutes", 10) * 60
    jarvis_mb = cfg.get("jarvis_mb", 1500)
    whisper_mb = cfg.get("whisper_mb", 2500)
    monitor = health.Monitor(jarvis)
    tick = 0
    while True:
        time.sleep(CHECK_SECONDS)
        tick += 1
        try:
            whisper = getattr(jarvis, "whisper", None)
            if whisper and whisper.ready and not whisper._ping():
                print("[watchdog] whisper-server isn't answering; restarting it")
                whisper.stop()
                print("[watchdog] whisper-server %s" % ("is back" if whisper.start() else "failed to start"))

            wake = getattr(jarvis, "wake", None)
            if wake and wake.mic and time.time() - wake.audio_at > 60:
                print("[watchdog] no audio from the microphone for 60 s; reopening it")
                wake.audio_at = time.time()
                wake.mic.kill()

            monitor.update()

            since = jarvis.handling_since
            if since and time.time() - since > stuck:
                print("[watchdog] a request has been running for %d min; restarting JARVIS" % (stuck // 60))
                os._exit(1)

            if tick % MEMORY_EVERY == 0:
                mine = rss_mb(os.getpid())
                pid = whisper_pid(whisper) if whisper else None
                theirs = rss_mb(pid) if pid else 0
                print("[watchdog] memory: JARVIS %d MB, whisper-server %d MB" % (mine, theirs))
                if theirs > whisper_mb:
                    print("[watchdog] whisper-server is over %d MB; restarting it" % whisper_mb)
                    whisper.stop()
                    whisper.start()
                if mine > jarvis_mb and not since and not jarvis.speaker.busy():
                    print("[watchdog] JARVIS is over %d MB; restarting it" % jarvis_mb)
                    os._exit(1)
        except Exception as e:  # the watchdog itself must never die
            print("[watchdog] %r" % e)
