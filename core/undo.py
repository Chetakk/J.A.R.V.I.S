""""Undo that": the saved command that reverses the last one."""
import re

UNDO = re.compile(r"^(?:undo(?: that| it| the last (?:one|thing|command))?|reverse that|put it back|change it back|"
                  r"go back to how it was|cancel that)$", re.IGNORECASE)

# command -> its opposite (both ways are added below). Toggles undo themselves.
PAIRS = {
    "volume_up": "volume_down",
    "night_light_on": "night_light_off",
    "dark_mode": "light_mode",
    "text_bigger": "text_normal",
    "dnd_on": "dnd_off",
    "wifi_on": "wifi_off",
    "hotspot_on": "hotspot_off",
    "airplane_on": "airplane_off",
    "bluetooth_on": "bluetooth_off",
    "rotate_left": "rotate_normal",
}
TOGGLES = {"mute", "mute_mic"}

OPPOSITE = dict(PAIRS)
OPPOSITE.update({b: a for a, b in PAIRS.items()})
OPPOSITE.update({t: t for t in TOGGLES})


def opposite(name):
    return OPPOSITE.get(name)
