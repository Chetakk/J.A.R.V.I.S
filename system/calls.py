"""Whether you're on a call, so JARVIS stays asleep through it (like "mute the mic"): a phone call GSConnect says is
answered (its "Ongoing call" notification, withdrawn when the call ends: system/notifications.py tells this), or a
call app on this PC using the mic (Meet in Chrome, Discord, WhatsApp...), from PulseAudio's recording streams."""
import re
import subprocess
import threading
import time

APPS = ("chrome", "chromium", "firefox", "discord", "vesktop", "whatsapp", "zapzap", "zoom", "teams", "skype", "slack",
        "telegram", "signal")
EVERY = 2.0  # seconds between looks at who's using the mic (is_deaf is asked every few milliseconds)
PHONE_MAX = 3 * 3600  # shortcut: a missed call end (phone off Wi-Fi mid-call) keeps it asleep this long; upgrade: adb call state
_phone = {}  # caller -> when the call was answered
_lock = threading.Lock()
_pc = [0.0, ""]  # when the mic was last looked at, and the call app using it then
_was = [""]


def phone_talking(caller):
    _phone[caller] = time.time()


def phone_ended(caller):
    _phone.pop(caller, None)


def mic_app(pactl):
    """The first call app among `pactl list source-outputs`' recording streams, or ""."""
    for stream in pactl.split("Source Output #")[1:]:
        names = re.findall(r'application\.(?:name|process\.binary) = "([^"]*)"', stream)
        if any(app in n.lower() for n in names for app in APPS):
            return names[0]
    return ""


def on_call():
    """Why JARVIS shouldn't listen now ("a phone call with Mom", "Google Chrome is using the mic"), or ""."""
    now = time.time()
    for caller, since in list(_phone.items()):
        if now - since > PHONE_MAX:
            phone_ended(caller)
    why = ("a phone call with %s" % next(iter(_phone))) if _phone else ""
    with _lock:
        if not why and now - _pc[0] >= EVERY:
            _pc[0] = now
            try:
                out = subprocess.run(["pactl", "list", "source-outputs"], capture_output=True, text=True, timeout=1).stdout
                _pc[1] = mic_app(out)
            except (OSError, subprocess.SubprocessError):
                _pc[1] = ""
        why = why or ("%s is using the mic" % _pc[1] if _pc[1] else "")
        if why != _was[0]:
            print("[calls] %s" % ("on a call (%s): not listening" % why if why else "call over: listening again"))
            _was[0] = why
    return why
