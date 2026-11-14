"""Whether you're on a call, so JARVIS stays asleep through it (like "mute the mic"): a phone call GSConnect says is
answered (its "Ongoing call" notification, withdrawn when the call ends: system/notifications.py tells this), or a
call app on this PC using the mic (Meet in Chrome, Discord, WhatsApp...), from PulseAudio's recording streams.

meeting_calls(): during meeting mode, a phone call doesn't silence the meeting: GSConnect's call settings are switched
(ringing leaves the sound alone, answering lowers it instead of muting, the PC mic is still muted) and put back after."""
import json
import os
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
GSCONNECT = "/org/gnome/shell/extensions/gsconnect/device/"
SCHEMAS = os.path.expanduser("~/.local/share/gnome-shell/extensions/gsconnect@andyholmes.github.io/schemas")
TELEPHONY = "org.gnome.Shell.Extensions.GSConnect.Plugin.Telephony"
IN_MEETING = {"ringing-volume": "'nothing'", "ringing-pause": "false", "talking-volume": "'lower'",
              "talking-microphone": "true", "talking-pause": "false"}  # 'nothing': GSConnect leaves the volume alone
SAVED = os.path.expanduser("~/.local/share/jarvis/phone_call_settings.json")  # yours, while a meeting has them


def phone_talking(caller):
    _phone[caller] = time.time()


def phone_ended(caller):
    _phone.pop(caller, None)


def _gsettings(*args):
    return subprocess.run(["gsettings", "--schemadir", SCHEMAS] + list(args), capture_output=True, text=True,
                          timeout=5).stdout.strip()


def meeting_calls(on):
    """Switch every paired phone's call settings for a meeting (on) or put yours back (off). Never raises."""
    try:
        if on and not os.path.exists(SAVED):
            phones = subprocess.run(["dconf", "list", GSCONNECT], capture_output=True, text=True,
                                    timeout=5).stdout.split()
            saved = {}
            for dev in (d for d in phones if d.endswith("/")):
                where = "%s:%s%splugin/telephony/" % (TELEPHONY, GSCONNECT, dev)
                saved[where] = {k: _gsettings("get", where, k) for k in IN_MEETING}
                for k, v in IN_MEETING.items():
                    _gsettings("set", where, k, v)
            with open(SAVED, "w") as f:
                json.dump(saved, f)
        elif not on and os.path.exists(SAVED):
            with open(SAVED) as f:
                saved = json.load(f)
            for where, keys in saved.items():
                for k, v in keys.items():
                    if v:
                        _gsettings("set", where, k, v)
            os.remove(SAVED)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print("[calls] couldn't %s the phone's call settings: %r" % ("switch" if on else "restore", e))


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
