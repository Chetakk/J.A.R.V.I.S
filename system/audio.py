"""Speakers and microphones through pactl: what's there, which is in use, switching (what's already playing or
recording moves along) and the volume. Monitor sources (a speaker's own echo) aren't microphones and are left out."""
import re
import subprocess
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
    """What to call it out loud: "the Echo Dot", "the USB speaker", "the headphone jack"."""
    if name.startswith("bluez_"):
        return "the Echo Dot" if "echo" in description.lower() else description
    if ".usb-" in name:
        return "the USB speaker" if kind == "sink" else "the USB microphone"
    if "platform-sound" in name or description.startswith("Built-in"):
        return "the headphone jack" if kind == "sink" else "the built-in input"
    return re.sub(r" Analog Stereo$| Digital Stereo.*$", "", description) or name


ALIASES = [  # (what you might call it, test on the device)
    (r"echo(?: dot)?|alexa|smart speaker", lambda d: d.name.startswith("bluez_") and "echo" in d.label.lower()),
    (r"bluetooth(?: speaker| headphones)?|bt", lambda d: d.name.startswith("bluez_")),
    (r"usb(?: speakers?| audio| mic(?:rophone)?| headset)?", lambda d: ".usb-" in d.name),
    (r"(?:head ?phones?|ear ?phones?|ear ?buds|headset)", lambda d: d.form in ("headphone", "headset", "hands-free")
     or "headphone" in d.port),
    (r"(?:head ?phone|audio) jack|jack|built[- ]?in(?: audio| mic(?:rophone)?| input)?|aux",
     lambda d: "platform-sound" in d.name),
]


def find(kind, words):
    """The device `words` names ("echo dot", "usb", "headphones"), or None. Headphones with none connected
    means the jack."""
    words = re.sub(r"^(?:the|my|a)\s+|\s+(?:speakers?|output|device)$", "", words.strip().lower()) or words
    found = devices(kind)
    for pattern, test in ALIASES:
        if re.fullmatch(pattern, words):
            hit = next((d for d in found if test(d)), None)
            if hit or not re.match(r"head|ear", words):
                return hit
            return next((d for d in found if "platform-sound" in d.name), None)
    said = set(re.findall(r"[a-z0-9]+", words))
    scored = [(len(said & set(re.findall(r"[a-z0-9]+", (d.label + " " + d.name).lower()))), d) for d in found]
    best = max(scored, key=lambda s: s[0], default=(0, None))
    return best[1] if best[0] else None


def switch(device):
    """Make `device` the default, and move what's playing (or recording) over to it. True if it worked."""
    kind = device.kind
    ok, _ = _pactl("set-default-" + kind, device.name)
    streams = "sink-inputs" if kind == "sink" else "source-outputs"
    for line in _pactl("list", "short", streams)[1].splitlines():
        stream = line.split("\t")[0]
        if stream.isdigit():
            _pactl("move-" + streams[:-1], stream, device.name)
    return ok


def set_volume(device, percent):
    ok, _ = _pactl("set-%s-volume" % device.kind, device.name, "%d%%" % max(0, min(150, percent)))
    if ok and device.mute and percent > 0:
        _pactl("set-%s-mute" % device.kind, device.name, "0")
    return ok
