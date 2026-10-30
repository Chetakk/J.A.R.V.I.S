""""What audio devices are there", "switch audio to the echo dot", "use the usb speaker", "which mic are you using",
"set the mic volume to 60": speakers and microphones by what you call them (system/audio.py). Switching moves what's
already playing too, and "undo that" goes back. The mic is what JARVIS hears you with, so changing it (or turning
it low) asks first. The Echo Dot connects by itself from the Alexa app; when it isn't there JARVIS says so.

Muting, three separate things, never one toggle:
  "mute the sound"  the speaker, so nothing on this computer is heard; "unmute" brings it back.
  "mute the mic"    JARVIS stops listening; the system microphone stays live for other apps. It asks first,
                    because afterwards JARVIS can't hear you: only the tray icon (or typing) turns it back on.
  "mute yourself"   JARVIS's own voice, which is quiet mode (skills/voice_prefs.py).
Each says which way it's going, so saying "unmute" when nothing is muted never mutes you."""
import re

from skills.base import Skill
from skills.helpers import and_list
from system import audio
from system.health import LOW_MIC_VOLUME

SOUND = r"(?:the )?(?:audio|sound|output|music|speaker|speakers|volume)"
MIC = r"(?:the |my )?(?:mic|microphone|input|mike)"
DEVICE_WORDS = "|".join(p for p, _ in audio.ALIASES)
NAMED = r"(?:the |my )?(?:%s)(?: speakers?)?" % DEVICE_WORDS  # "use the echo", "switch to headphones"
ECHO_HELP = ("The Echo Dot isn't connected right now. In the Alexa app, open its Bluetooth devices and tap "
             "biztelai-desktop; it stays connected after that.")
NOT_LISTENING = ["Okay, I'm not listening. Don't worry about it.",
                 "Alright, I'll stay quiet. Just let me know when you want me back.",
                 "Got it, not listening. Don't worry."]
# Muting the speaker and muting the microphone, each in its own direction (no toggles: "unmute" only unmutes).
MUTE_SOUND = (r"mute(?: the| my)?(?: sound| audio| speakers?| output| volume| everything)?|"
              r"silence (?:the )?(?:sound|audio|speakers?|everything)|"
              r"turn (?:the )?(?:sound|audio|volume|speakers?) off|"
              r"turn off (?:the )?(?:sound|audio|volume|speakers?)|(?:sound|audio|speakers?) off")
UNMUTE_SOUND = (r"unmute(?: the| my)?(?: sound| audio| speakers?| output| volume| everything)?|"
                r"turn (?:the )?(?:sound|audio|volume|speakers?) (?:back )?on|"
                r"turn (?:back )?on (?:the )?(?:sound|audio|volume|speakers?)|"
                r"(?:sound|audio|speakers?) (?:back )?on|bring (?:the )?(?:sound|audio) back")
MUTE_MIC = (r"mute (?:the |my )?(?:mic|microphone|mike|input)|"
            r"turn (?:the |my )?(?:mic|microphone|mike) off|turn off (?:the |my )?(?:mic|microphone|mike)|"
            r"(?:mic|microphone) off")
UNMUTE_MIC = (r"unmute (?:the |my )?(?:mic|microphone|mike|input)|"
              r"turn (?:the |my )?(?:mic|microphone|mike) (?:back )?on|"
              r"turn (?:back )?on (?:the |my )?(?:mic|microphone|mike)|(?:mic|microphone) (?:back )?on")


class Audio(Skill):
    group = "audio"
    does = "Speakers and microphones by name: switch, volume, which is in use, mute and unmute"
    phrases = ("what audio devices are there", "switch audio to <device>", "which mic are you using",
               "set the mic volume to 60", "mute the sound", "unmute the sound", "mute the mic", "unmute the mic")
    gives = "speech and a card"
    permission = "confirm"
    asks = "before changing the mic, muting it, or turning it low"
    checks = "pactl's answer: a device that refuses is said, not claimed"

    def patterns(self):
        return [
            # Muting first, and the mic before the speaker: "mute the mic" is never "mute everything".
            (MUTE_MIC, lambda m: self.mic_mute(True)),
            (UNMUTE_MIC, lambda m: self.mic_mute(False)),
            (MUTE_SOUND, lambda m: self.sound_mute(True)),
            (UNMUTE_SOUND, lambda m: self.sound_mute(False)),
            (r"(?:what|which) (?:audio|sound) (?:devices|outputs|inputs)(?: are there| do i have| are connected)?|"
             r"(?:list|show)(?: me)? (?:my |the )?(?:audio devices|sound devices|speakers and (?:mics|microphones)|"
             r"speakers|microphones|mics)|(?:what|which) (?:speakers|microphones|mics) (?:are there|do i have|"
             r"are connected|can you see)", lambda m: self.list()),
            (r"(?:which|what) (?:speaker|output|audio device|sound device) (?:am i using|are you using|is (?:on|in "
             r"use|playing|active|selected))|where(?:'s| is) (?:the )?(?:sound|audio|music) (?:going|coming out|"
             r"playing)(?: from| to)?|what(?:'s| is) (?:the )?(?:sound|audio) (?:playing (?:through|on)|going to|"
             r"output)", lambda m: self.which("sink")),
            (r"(?:which|what) (?:mic|microphone|input) (?:am i using|are you using|is (?:on|in use|active|selected)|"
             r"are you listening (?:on|with|through))|what are you listening (?:on|with|through)",
             lambda m: self.which("source")),
            (r"(?:switch|change|set)(?: the| my)? (?:mic|microphone|input|mike) to (?:the |my )?(.+)|"
             r"(?:use|listen (?:through|on|with)) (?:the |my )?(.+?) (?:mic|microphone|mike)|listen (?:through|on|"
             r"with) (%s)" % NAMED, self.mic_switch),
            (r"(?:switch|change|move|send|put|set)(?: the)? (?:audio|sound|output|music|speaker|playback)(?: output)? "
             r"(?:over |back )?to (?:the |my )?(.+)|(?:play|put) (?:the )?(?:sound|audio)? ?(?:on|through|out of|"
             r"from|to) (?:the |my )?(.+)", self.output),
            (r"(?:switch|go|change) (?:back )?to (%s)|use (%s)(?: for (?:the )?(?:sound|audio|music)| as (?:the |my )?"
             r"speakers?)?|(?:play|put) (?:the )?music (?:on|through|out of) (%s)" % (NAMED, NAMED, NAMED),
             self.output_named),
            (r"(?:set|turn|put|change) %s(?: volume| level| gain)? to (\d+)(?: percent| %%)?|%s (?:volume |level |"
             r"gain )?(?:to )?(\d+)(?: percent| %%)?" % (MIC, MIC), lambda m: self.mic_volume(to=int(_first(m)))),
            (r"(?:turn|make|put) %s(?: volume| level| gain)? (up|down|louder|quieter|higher|lower)|"
             r"(raise|increase|boost|lower|decrease|reduce) %s(?: volume| level| gain)?" % (MIC, MIC),
             lambda m: self.mic_volume(up=_first(m) in ("up", "louder", "higher", "raise", "increase", "boost"))),
            (r"(?:what(?:'s| is) %s(?: volume| level| gain)?(?: at| set to)?|how loud is %s|%s volume)" % (MIC, MIC, MIC),
             lambda m: self.which("source")),
        ]

    def list(self):
        sinks, sources = audio.devices("sink"), audio.devices("source")
        section = lambda items: "\n".join("- %s%s, %d%%%s" % (d.label, " **(in use)**" if d.default else "", d.volume,  # noqa: E731
                                                              ", muted" if d.mute else "") for d in items) or "- none"
        self.j.show({"title": "Audio devices", "text": "**Speakers**\n%s\n\n**Microphones**\n%s" % (
            section(sinks), section(sources))})
        out, mic = audio.current("sink"), audio.current("source")
        self.say("Sound is going to %s, and I'm listening on %s. %s" % (
            out.label if out else "nothing", mic.label if mic else "nothing",
            "Also connected: %s." % and_list(d.label for d in sinks + sources if not d.default and not any(
                o.label == d.label and o.default for o in sinks + sources)) if len(sinks) + len(sources) > 2 else ""))

    def which(self, kind):
        d = audio.current(kind)
        if not d:
            self.say("There's no speaker in use." if kind == "sink" else "I can't find a microphone.")
            return
        if kind == "sink":
            self.say("Sound is going to %s, at %d percent%s." % (d.label, d.volume, ", but it's muted" if d.mute else ""))
            return
        if self.j.deaf:
            self.say("I'm not listening right now.")
            return
        if d.mute:
            self.say("I'm listening on %s, but it's muted in the system." % d.label)
            return
        low = " That's low, so I may miss what you say." if d.volume < LOW_MIC_VOLUME else ""
        self.say("I'm listening on %s, at %d percent.%s" % (d.label, d.volume, low))

    def output_named(self, m):
        return self.output(m, sure=True)

    def output(self, m, sure=False):
        words = _first(m).strip()
        d = audio.find("sink", words)
        if not d:
            if re.search(r"\b(?:echo|alexa)\b", words, re.IGNORECASE):
                self.say(ECHO_HELP)
                return
            if not sure and not re.fullmatch(NAMED, words, re.IGNORECASE) and not m.group(0).lower().startswith(
                    ("switch", "change", "move", "send", "set")):
                return False  # "play music on youtube": not a speaker
            self.say("I can't find %s. You have %s." % (words, and_list(x.label for x in audio.devices("sink"))))
            return
        before = audio.current("sink")
        if before and before.name == d.name:
            self.say("Sound is already going to %s." % d.label)
            return
        if not audio.switch(d):
            self.say("Sorry, %s didn't take it." % d.label)
            return
        if before:
            self.j.last_undo = lambda: self._back(before)
        self.say("Sound is going to %s now." % d.label)

    def _back(self, d):
        if audio.switch(d):
            self.say("Sound is back on %s." % d.label)
        else:
            self.say("%s isn't there any more." % d.label.capitalize())

    def mic_switch(self, m):
        words = _first(m).strip()
        level = re.fullmatch(r"(\d+)(?: percent| %)?", words)
        if level:  # "set the mic to 60": a level, not a mic called 60
            return self.mic_volume(to=int(level.group(1)))
        d = audio.find("source", words)
        if not d:
            if m.group(0).lower().startswith(("use", "listen")) and not re.fullmatch(NAMED, words, re.IGNORECASE):
                return False
            self.say("I can't find a microphone called %s. You have %s." % (
                words, and_list(x.label for x in audio.devices("source"))))
            return
        before = audio.current("source")
        if before and before.name == d.name:
            self.say("I'm already listening on %s." % d.label)
            return
        if not self.j.ask("Listen on %s instead? If it can't hear you, type switch mic to %s to go back." % (
                d.label, before.label.replace("the ", "") if before else "usb")):
            self.say("Okay, I'll keep listening on %s." % (before.label if before else "this one"))
            return
        if not audio.switch(d):
            self.say("Sorry, %s didn't take it." % d.label)
            return
        if before:
            self.j.last_undo = lambda: audio.switch(before) and self.say("I'm back on %s." % before.label)
        self.say("I'm listening on %s now." % d.label)

    def sound_mute(self, on):
        """The speaker itself: muted, nothing on this computer is heard, JARVIS included. Said either way, so
        the reply is in the chat panel even while you can't hear it."""
        d = audio.current("sink")
        if not d:
            self.say("There's no speaker in use.")
            return
        if d.mute == on or not audio.set_mute(d, on):
            self.j.show_mutes(_asked=False, speaker=d.mute)  # the tray shows what's true, not what was asked
            if d.mute != on:
                self.say("Sorry, %s didn't take it." % d.label)
            else:
                self.say("It's already muted." if on else "Sound's already on.")
            return
        self.j.show_mutes(speaker=on)
        self.j.last_undo = lambda: self.sound_mute(not on)
        self.say(["Okay, sound's off.", "Done, it's quiet now.", "Muted."] if on
                 else ["Okay, sound's back.", "There you go, sound's back."])

    def mic_mute(self, on, ask=True):
        """JARVIS stops listening (or starts again). Only JARVIS: the system microphone stays live, so Zoom,
        Meet and recordings are unaffected. Muting asks first, because afterwards a spoken "unmute" can't be
        heard: the tray icon (or typing in the chat) turns it back on."""
        j = self.j
        if j.deaf == on:
            self.say("I'm already not listening, don't worry." if on else "I'm already listening.")
            return
        if on and ask and not j.ask("Want me to stop listening?"):
            self.say(["Okay, I'm still here.", "Alright, still listening."])
            return
        j.deaf = on
        j.show_mutes(mic=on)
        j.last_undo = lambda: self.mic_mute(not on, ask=False)
        self.say(NOT_LISTENING if on else ["I'm back, I can hear you.", "Okay, I'm listening again."])

    def mic_volume(self, to=None, up=None):
        d = audio.current("source")
        if not d:
            self.say("I can't find a microphone.")
            return
        level = to if to is not None else d.volume + (10 if up else -10)
        level = max(0, min(100, level))
        if level < LOW_MIC_VOLUME and not self.j.ask("At %d percent I may not hear you. Set it anyway?" % level):
            self.say("Okay, it stays at %d percent." % d.volume)
            return
        if not audio.set_volume(d, level):
            self.say("Sorry, that didn't work.")
            return
        self.j.last_undo = lambda: audio.set_volume(d, d.volume) and self.say("The mic is back at %d percent." % d.volume)
        self.say("The mic is at %d percent." % level)


def _first(m):
    return next((g for g in m.groups() if g), "")
