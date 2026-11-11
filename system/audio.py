"""Speakers and microphones through pactl: what's there, which is in use, switching (what's already playing or
recording moves along) and the volume. Monitor sources (a speaker's own echo) aren't microphones and are left out."""
import re
import subprocess
import threading
from collections import namedtuple

Device = namedtuple("Device", "kind name label volume mute default form port")  # kind: "sink" / "source"


def _pactl(*args):
    try:
        res = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=3)
        return res.returncode == 0, res.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def devices(kind):
    """Every output (kind "sink") or microphone ("source"), the one in use marked default."""
    _, info = _pactl("info")
    default = (re.search(r"^Default %s: (.+)$" % ("Sink" if kind == "sink" else "Source"), info, re.M) or [None, ""])[1]
    found = []
    for block in _pactl("list", kind + "s")[1].split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, _, value = line.strip().partition(": ")
            fields.setdefault(key, value)
            if line.strip().startswith("device.form_factor = "):
                fields["form"] = line.split("=", 1)[1].strip().strip('"')
        name = fields.get("Name")
        if not name or name.endswith(".monitor"):
            continue
        percent = [int(p) for p in re.findall(r"(\d+)%", fields.get("Volume", ""))]
        found.append(Device(kind, name, label(kind, name, fields.get("Description", "")),
                            max(percent) if percent else 100, fields.get("Mute") == "yes", name == default,
                            fields.get("form", ""), fields.get("Active Port", "")))
    return found


def current(kind):
    return next((d for d in devices(kind) if d.default), None)


def label(kind, name, description):
    """What to call it out loud: "the Echo Dot", "the earphones", "the built-in output".
    This machine: the AB13X USB device is the earphones (wired), the Echo Dot is the speaker, and the
    built-in output has no socket on the board."""
    if name.startswith("bluez_"):
        return "the Echo Dot" if "echo" in description.lower() else description
    if ".usb-" in name:
        return "the earphones" if kind == "sink" else "the USB microphone"
    if "platform-sound" in name or description.startswith("Built-in"):
        return "the built-in output" if kind == "sink" else "the built-in input"
    return re.sub(r" Analog Stereo$| Digital Stereo.*$", "", description) or name


ALIASES = [  # (what you might call it, test on the device)
    (r"echo(?: dot)?|alexa|(?:smart |big |main )?speakers?", lambda d: d.name.startswith("bluez_") and "echo" in d.label.lower()),
    (r"bluetooth(?: speaker| headphones)?|bt", lambda d: d.name.startswith("bluez_")),
    (r"usb(?: speakers?| audio| mic(?:rophone)?| headset)?", lambda d: ".usb-" in d.name),
    (r"(?:head ?phones?|ear ?phones?|ear ?buds|headset)(?: jack)?|(?:audio )?jack|aux",
     lambda d: d.form in ("headphone", "headset", "hands-free") or "headphone" in d.port or ".usb-" in d.name),
    (r"built[- ]?in(?: audio| mic(?:rophone)?| input)?", lambda d: "platform-sound" in d.name),
]


def find(kind, words):
    """The device `words` names ("echo dot", "usb", "headphones"), or None."""
    words = re.sub(r"^(?:the|my|a)\s+|\s+(?:speakers?|output|device)$", "", words.strip().lower()) or words
    found = devices(kind)
    for pattern, test in ALIASES:
        if re.fullmatch(pattern, words):
            return next((d for d in found if test(d)), None)
    said = set(re.findall(r"[a-z0-9]+", words))
    scored = [(len(said & set(re.findall(r"[a-z0-9]+", (d.label + " " + d.name).lower()))), d) for d in found]
    best = max(scored, key=lambda s: s[0], default=(0, None))
    return best[1] if best[0] else None


def switch(device):
    """Make `device` the default, and move what's playing (or recording) over to it. True if it worked."""
    kind = device.kind
    ok, _ = _pactl("set-default-" + kind, device.name)
    streams = "sink-inputs" if kind == "sink" else "source-outputs"
    pinned = _pinned() if kind == "sink" else set()
    for line in _pactl("list", "short", streams)[1].splitlines():
        stream = line.split("\t")[0]
        if stream.isdigit() and stream not in pinned:
            _pactl("move-" + streams[:-1], stream, device.name)
    return ok


def _pinned():
    """Playing streams started on one device on purpose, which switching must leave there: JARVIS's voice in
    private mode (pacat --device=) and the Echo's keep-alive (pacat -d), which drops the link if moved."""
    ids = set()
    for block in _pactl("list", "sink-inputs")[1].split("\n\n"):
        stream, pid = re.search(r"^Sink Input #(\d+)", block), re.search(r'application\.process\.id = "(\d+)"', block)
        try:
            args = open("/proc/%s/cmdline" % pid.group(1), "rb").read().split(b"\0") if stream and pid else []
        except OSError:
            continue
        if any(a == b"-d" or a.startswith(b"--device") for a in args):
            ids.add(stream.group(1))
    return ids


def set_mute(device, on):
    """Mute (on=True) or unmute a speaker or microphone. True if pactl took it."""
    ok, _ = _pactl("set-%s-mute" % device.kind, device.name, "1" if on else "0")
    return ok


def set_volume(device, percent):
    ok, _ = _pactl("set-%s-volume" % device.kind, device.name, "%d%%" % max(0, min(150, percent)))
    if ok and device.mute and percent > 0:
        _pactl("set-%s-mute" % device.kind, device.name, "0")
    return ok


OWN = {"pacat", "aplay", "paplay"}  # JARVIS's voice, its beeps and the Echo's keep-alive: never turned down
DUCK_TO = 0.3  # what's playing drops to this share of its volume while JARVIS listens
_ducked = {}  # stream -> (its app, the volume (%) it had)
_seen = set()  # every stream there was while ducked
_duck_lock = threading.Lock()


def _streams():
    """(stream, app, binary, volume %, paused) for each sound being played; app is what PulseAudio keeps its volume under."""
    found = []
    for block in _pactl("list", "sink-inputs")[1].split("\n\n"):
        stream = re.search(r"^Sink Input #(\d+)", block)
        percent = [int(p) for p in re.findall(r"(\d+)%", (re.search(r"^\s*Volume: (.*)$", block, re.M) or
                                                           [None, ""])[1])]
        if stream and percent:
            prop = lambda name: (re.search(r'%s = "([^"]*)"' % re.escape(name), block) or [None, None])[1]  # noqa: E731
            binary = prop("application.process.binary")
            found.append((stream.group(1), prop("module-stream-restore.id") or binary, binary, max(percent),
                          "Corked: yes" in block))
    return found


def duck(on):
    """on: turn every other app's sound (music, videos) down while JARVIS listens; off: put it back as it was.
    PulseAudio remembers the lowered volume for the app, so a new stream it starts meanwhile ("play the music":
    Spotify starts, the video stops) comes up lowered too: that one is put back along with the others."""
    with _duck_lock:
        if not on:
            if _ducked:
                apps = {app: percent for app, percent in _ducked.values()}
                # shortcut: an app with no stream left now keeps the lowered volume for its next one; fix if seen.
                for stream, app, _, _, _ in _streams():
                    if stream in _ducked or (stream not in _seen and app in apps):
                        _pactl("set-sink-input-volume", stream,
                               "%d%%" % (_ducked[stream][1] if stream in _ducked else apps[app]))
            _ducked.clear()
            _seen.clear()
            return
        apps = {app for app, _ in _ducked.values()}
        for stream, app, binary, percent, paused in _streams():
            if stream in _seen or app in apps:  # (a new one from a ducked app is already lowered)
                continue
            _seen.add(stream)
            if not paused and binary not in OWN:
                _ducked[stream] = (app, percent)
                _pactl("set-sink-input-volume", stream, "%d%%" % (percent * DUCK_TO))
