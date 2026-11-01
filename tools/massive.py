#!/usr/bin/env python3
"""Amazon MASSIVE (en-US, CC BY 4.0, https://github.com/alexa/massive) for JARVIS: each intent -> the JARVIS phrases
that would be right for it, or none.

  tools/massive.py test.jsonl tests/heldout.yaml   # rebuild the held-out sentences from the test split

The test split is held out (tests/heldout.yaml, core/heldout.py); tools/finetune.py trains on the train split.
"""
import json
import re
import sys

# Intent -> the phrases that would be right; "none" in the list: leaving it alone is right too (a skill's pattern
# takes most of those before the matcher sees them). Intents not here and not in SKIP mean none of JARVIS's phrases.
ACCEPT = {
    "audio_volume_up": ["volume up"],
    "audio_volume_down": ["volume down"],
    "audio_volume_mute": ["mute the sound", "quiet mode", "pause the music", "pause"],
    "alarm_query": ["what are my reminders"],
    "alarm_set": ["none", "set an alarm", "set a reminder", "set a timer"],
    "datetime_query": ["what time is it", "what's the date"],
    "weather_query": ["how is the weather", "weather tomorrow", "temperature outside",
                      "search the web for weather today and tell me"],
    "transport_query": ["none", "get directions"],
    "social_query": ["none", "open twitter explore"],
    "lists_query": ["what's on my todo list", "list my notes"],
    "lists_createoradd": ["none", "add a todo", "add a note"],
    "calendar_query": ["none", "what are my reminders", "how's my day going", "open google calendar", "what's pending",
                       "what's the date"],
    "calendar_set": ["none", "set a reminder", "set an alarm", "add a todo"],
}
# Could fairly go to one of JARVIS's phrases or not, depending on the words: left out.
SKIP = {"play_music", "music_query", "music_settings", "music_likeness", "music_dislikeness", "audio_volume_other",
        "iot_hue_lightchange", "iot_hue_lightoff", "iot_hue_lighton", "iot_hue_lightup", "iot_hue_lightdim",
        "alarm_remove", "calendar_remove", "lists_remove", "play_podcasts", "play_audiobook", "play_radio",
        "play_game", "iot_wemo_on", "iot_wemo_off"}
# The time somewhere else is the weather skill's pattern, not the matcher's "what time is it".
ELSEWHERE = re.compile(r"\b(?:in|zone|new york|london|est|gmt|pst)\b")


def read(path):
    """{sentence: [right phrases] or "none"}: wake word taken out, no numbers, 2-16 words."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            intent = r["label_text"]
            if intent in SKIP:
                continue
            text = re.sub(r"\s+", " ", re.sub(r"\bolly\b,?\s*", "", r["text"].lower()).strip(" ,.?!"))
            if re.search(r"\d", text) or not 2 <= len(text.split()) <= 16 or text in out or (
                    intent in ("datetime_query", "weather_query") and ELSEWHERE.search(text)):
                continue
            out[text] = list(ACCEPT[intent]) if intent in ACCEPT else "none"
    return out


HEADER = """# Held-out sentences for scoring the meaning matcher (core/heldout.py). Never taught from: teaching only
# checks itself against them. sentence: [phrases that would be right] or none (the matcher must leave it);
# "none" in a list: leaving it is right too (a skill's pattern takes most of those before the matcher sees them).
# From Amazon MASSIVE (en-US test split), CC BY 4.0, https://github.com/alexa/massive; wake word
# removed, sentences with numbers left out, each labelled by its MASSIVE intent (tools/massive.py).
"""


def main(argv):
    import yaml
    labels = read(argv[0])
    with open(argv[1], "w", encoding="utf-8") as f:
        f.write(HEADER)
        yaml.dump(labels, f, Dumper=type("D", (yaml.SafeDumper,), {"ignore_aliases": lambda *a: True}),
                  default_flow_style=None, allow_unicode=True, sort_keys=False, width=120)
    print("%d sentences, %d with phrases" % (len(labels), sum(1 for v in labels.values() if v != "none")))


if __name__ == "__main__":
    main(sys.argv[1:])
