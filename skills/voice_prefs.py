"""How JARVIS talks, set by voice and kept across restarts (memory.json "prefs"): "talk faster" / "slow down",
"be brief" / "give me detailed answers", private mode (only your headphones hear it) and quiet mode (nothing
spoken, everything in the chat panel)."""
import re

from skills.base import Skill
from voice import speech

STEP = 0.85  # "faster": Piper's length_scale times this; "slower": divided by it
BRIEF = r"(?:be |keep (?:it|your answers|answers) |answer )(?:brief|short|shorter|concise)(?: please| from now on)?|" \
        r"(?:give me |use )?(?:short|shorter|brief) answers(?: please| from now on)?|(?:talk|say) less|less detail"
DETAILED = r"(?:be |answer )more detailed|(?:give me |use )?(?:more )?detailed answers(?: please| from now on)?|" \
           r"(?:give me |use |go into )?more detail (?:from now on|in your answers)|always (?:give me )?more detail"
NORMAL = r"(?:normal|regular|usual) answers|answer normally|(?:normal|regular|usual) amount of detail"
PRIVATE_ON = r"(?:turn on |switch on |go into |use )?(?:private|headphones?(?: only)?|headset) mode(?: on)?|" \
             r"(?:only )?(?:speak|talk) (?:only )?(?:in|through|on|into|to) (?:my )?(?:headphones|headset|" \
             r"earphones|earbuds)(?: only)?|headphones only|only (?:through|in|on) (?:my )?headphones|" \
             r"(?:speak|talk) privately"
# "of": how speech-to-text often writes "off" ("private mode of" had turned it on).
PRIVATE_OFF = r"(?:turn off |switch off |stop |end |leave |exit )(?:private|privacy|headphones?|headset) mode|" \
              r"(?:turn |switch )?(?:private|privacy|headphones?|headset) mode (?:off|of)|" \
              r"(?:turn on |switch on |go into |go |use |back to )?public mode(?: on)?"
# Out of private mode, "switch to the speaker" means the Echo Dot (the Audio skill switches to it).
BACK_TO_SPEAKER = r"(?:use|back to|go back to|talk through|speak through|switch (?:back )?to) (?:the )?(speakers?)(?: again)?"
QUIET_ON = r"(?:turn on |switch on |go into |use )?(?:quiet|silent|text|cards?) (?:mode|only)(?: on)?|" \
           r"(?:don'?t|do not) (?:talk|speak)(?: out loud)?(?: for a while| any ?more| now)?|mute yourself|" \
           r"turn (?:off )?your (?:voice|speech)(?: off)?|(?:just|only) (?:write|type)(?: it)?(?: down| in the chat)?|" \
           r"(?:go|switch)(?: in)?(?: ?to)? (?:quiet|silent) mode|(?:stay|keep) quiet(?: for a while)?|no (?:more )?talking|" \
           r"(?:don'?t|do not) say anything(?: out loud)?|(?:shut up|hush) for a while"
QUIET_OFF = r"(?:turn off |switch off |stop |end |leave |exit )(?:quiet|silent|text) mode|" \
            r"(?:quiet|silent|text) mode off|(?:you can )?(?:talk|speak) (?:again|out loud(?: again)?)|" \
            r"unmute yourself|turn (?:on )?your (?:voice|speech)(?: on)?"
STOP_ECHO = re.compile(r"(?:(?:please |okay |ok )?(?:stop|quit|end|enough)(?: (?:repeating|repeating me|repeating after "
                       r"me|copying me|it|that|now|please))*|that'?s (?:enough|all)|(?:you can )?stop now)")


class VoicePrefs(Skill):
    group = "voice"
    does = ("How JARVIS talks: speed, brief or detailed answers, private (headphones) and quiet mode; kept across "
             "restarts. Repeats your words: one sentence, or everything after \"repeat after me\" until "
             "\"stop repeating\"")
    phrases = ("talk faster", "slow down", "be brief", "give me detailed answers", "private mode",
               "private mode off", "quiet mode", "you can talk again", "repeat <text>", "repeat after me",
               "say <text>")
    gives = "the setting changed, and said"
    permission = "auto"
    asks = "going quiet instead when private mode finds no headphones"
    checks = "the saved setting is applied again at start; lost headphones in private mode go silent, never loud"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.echoing = False  # "repeat after me" until "stop repeating"

    def patterns(self):
        return [
            (r"(?:can you |please )?(?:talk|speak|read|go)(?: a (?:bit|little|lot))? (faster|quicker|slower)"
             r"(?: please)?|(?:can you |please )?(speed up|slow down)(?: (?:a bit|a little))?(?: please)?|"
             r"you(?:'re| are) (?:talking|speaking) (?:too|a bit|really) (fast|quick|slow)", self.speed),
            (r"(?:talk|speak)(?: at)? (?:your )?(?:normal|regular|default|usual) (?:speed|pace)|"
             r"(?:normal|regular|default|usual) (?:talking |speaking )?(?:speed|pace)", lambda m: self.normal_speed()),
            (BRIEF, lambda m: self.answers("brief")),
            (DETAILED, lambda m: self.answers("detailed")),
            (NORMAL, lambda m: self.answers(None)),
            (PRIVATE_OFF, lambda m: self.private(False)),
            (BACK_TO_SPEAKER, lambda m: self.private(False) if self.j.memory.pref("private") else
             self.j.skills.audio.output(m, sure=True)),
            (PRIVATE_ON, lambda m: self.private(True)),
            (QUIET_OFF, lambda m: self.quiet(False)),
            (QUIET_ON, lambda m: self.quiet(True)),
            # "Repeat after me": everything you say is said back until "stop repeating" (see echoing).
            (r"(?:can you |could you |please )?(?:repeat|say) after me(?: please)?", lambda m: self.echo_on("")),
            (r"(?:can you |could you |please )?(?:repeat|say) after me[:,]? (.+)", lambda m: self.echo_on(m.group(1))),
            # "Repeat ...": just the one sentence. Not "repeat that" / "repeat yourself" (its last line again).
            (r"(?:can you |could you |please )?(?:say this|repeat this|say the following|repeat the following|repeat)",
             lambda m: self.say_back("")),
            (r"(?:can you |could you |please )?(?:say this|repeat this|say the following|repeat the following|"
             r"repeat(?! (?:that|it|yourself|what|your|the last|again|my)\b))[:,]? (.+)",
             lambda m: self.say_back(m.group(1))),
            # Not "say that again" (repeats its last line), "say less" (above) or "say my notifications".
            (r"(?:can you |could you |please )?say (?!(?:that|it|this|something|anything|again|my|what)\b)(.+?)"
             r"(?: out loud| aloud)?", lambda m: self.say_back(m.group(1))),
        ]

    def say_back(self, text):
        """"Repeat after me ...", "say ...": your own words, spoken back exactly."""
        text = text.strip().strip("\"'\u201c\u201d")
        if not text:
            text = (self.j._answer("Okay, what should I say?") or "").strip()
        if text:
            self.say(text)

    def echo_on(self, text):
        self.echoing = True
        text = text.strip().strip("\"'\u201c\u201d")
        self.say(text or "Okay, I'll repeat everything you say. Say stop repeating when you're done.")

    def echo(self, text):
        """While repeating after you: says your words back (or stops on "stop repeating"). True if it did."""
        if not self.echoing:
            return False
        low = text.lower().strip(" .!?,")
        if STOP_ECHO.fullmatch(low):
            self.echoing = False
            self.say("Okay, I've stopped repeating.")
        else:
            self.say(text.strip())
        return True

    def apply(self):
        """At startup: what was set by voice last time."""
        mem, spk = self.j.memory, self.j.speaker
        if mem.pref("speed"):
            spk.set_speed(mem.pref("speed"))
        spk.brief = mem.pref("answers") == "brief"
        spk.muted = bool(mem.pref("quiet"))
        if mem.pref("private"):
            spk.sink = speech.headphones(self._preferred())
            if not spk.sink:
                spk.muted = True  # private, but the headphones aren't here: say nothing out loud
        spk.on_sink_lost = self._sink_lost
        self._show()

    def _show(self):
        """Quiet mode is JARVIS's voice muted: the tray icon shows it, whichever way it was set."""
        self.j.show_mutes(voice=self.j.speaker.muted)

    def speed(self, m):
        word = next(g for g in m.groups() if g).lower()
        faster = word in ("faster", "quicker", "speed up", "slow")  # "you're talking too slow" → faster
        spk = self.j.speaker
        new = spk.set_speed(spk.speed() * (STEP if faster else 1 / STEP))
        self.j.memory.set_pref("speed", new)
        if new in (0.6, 1.6):
            self.say("That's as %s as I go." % ("fast" if faster else "slow"))
        else:
            self.say(["Okay, is this better?", "Sure. How's this?"])

    def normal_speed(self):
        self.j.speaker.set_speed(self.j.speaker.default_speed)
        self.j.memory.set_pref("speed", None)
        self.say("Back to my normal speed.")

    def answers(self, how):
        self.j.memory.set_pref("answers", how)
        self.j.speaker.brief = how == "brief"
        self.say({"brief": "Okay, I'll keep it short.", "detailed": "Okay, I'll go into more detail.",
                  None: "Okay, back to normal answers."}[how])

    def _preferred(self):
        return (self.j.settings.get("speech") or {}).get("private_sink")

    def private(self, on):
        spk = self.j.speaker
        if not on:
            self.j.memory.set_pref("private", None)
            spk.sink = None
            spk.muted = bool(self.j.memory.pref("quiet"))
            self._show()
            self.say("Okay, I'm back on the speaker." if not spk.muted else
                     "Okay. I'm still in quiet mode, so I'll keep writing in the chat.")
            return
        sink = speech.headphones(self._preferred())
        if not sink:
            if self.j.ask("I can't find any headphones. Shall I go quiet instead, and just write in the chat?"):
                self.quiet(True)
            else:
                self.say("Okay. Connect your headphones and ask me again.")
            return
        self.j.memory.set_pref("private", True)
        name = "your earphones" if sink == self._preferred() else next(
            (d for n, d, _, _ in speech.sinks() if n == sink), "your headphones")
        self.say("Okay, only %s will hear me now." % name)  # said on the speaker, then the switch
        spk.sink = sink

    def quiet(self, on):
        self.j.memory.set_pref("quiet", True if on else None)
        if on:
            self.say("Okay, I'll stay quiet and write in the chat.")
            self.j.speaker.muted = True
            if self.j.ui:
                self.j.ui.show_chat(True)
        else:
            self.j.speaker.muted = bool(self.j.memory.pref("private")) and not self.j.speaker.sink
            self.say("Okay, I'm talking again." if not self.j.speaker.muted else
                     "I'm still in private mode and your headphones aren't here, so I'll keep writing.")
        self._show()

    def _sink_lost(self):
        """The headphones went away in private mode: nothing out loud; write instead."""
        self.j.speaker.muted = True
        self._show()
        self.j.chat("sys", "Your headphones disconnected, so I've gone quiet. Say private mode off to use the "
                           "speaker again.")
        if self.j.ui:
            self.j.ui.show_chat(True)
