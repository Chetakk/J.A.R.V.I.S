""""Share YouTube audio with my mic", "share audio only", "what's being shared", "stop sharing audio": an app's
sound into a Discord/Vesktop call, through ~/share-audio.sh (both / audio / status / off).

Which app: the one you name ("share mpv audio"); unnamed, the one app playing sound, or "which one?" when
several are, and none playing just readies the share mic. JARVIS's own voice (aplay), the Echo Dot keepalive
(pacat) and the call itself (Vesktop, Discord) never count as playing."""
import os
import re
import subprocess

from skills.base import Skill
from skills.helpers import and_list

SCRIPT = os.path.expanduser("~/share-audio.sh")
NOT_APPS = ("pacat", "aplay", "vesktop", "discord")
# Browsers name their stream "Playback", not the tab, so "YouTube" has to mean the browser.
ALIASES = {"youtube": "chrome|chromium|firefox|brave", "browser": "chrome|chromium|firefox|brave"}
APP = r"(?:(?:the |my )?(.+?) )?"
MIC = r"(?:my )?(?:mic|microphone|mike|voice)"


def playing():
    """[(sink input id, app name, binary, media name)] of apps playing sound now, leaving out ours and the call."""
    out = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=5).stdout
    found = []
    for block in re.split(r"^Sink Input #", out, flags=re.M)[1:]:
        field = lambda k: (re.search(r'%s = "([^"]*)"' % k, block) or [None, ""])[1]
        app, binary = field(r"application\.name"), field(r"application\.process\.binary")
        # module-loopback streams are the share script's own wiring, not apps
        if "Corked: yes" in block or "module-" in block.split("Driver: ")[-1][:20] or not (app or binary) \
                or binary.lower() in NOT_APPS:  # exact: "paplay" is someone's sound, "aplay" is JARVIS's voice
            continue
        found.append((block.split()[0], app or binary, binary, field(r"media\.name")))
    return found


def named(said, apps):
    """The apps `said` names ("mpv", "youtube", "godzilla")."""
    said = said.lower().strip()
    want = ALIASES.get(said, re.escape(said))
    return [a for a in apps if re.search(want, " ".join(a[1:]).lower())]


def labels(apps):
    """What to call each in a question: its app name, plus what it plays when two share a name."""
    names = [a[1] for a in apps]
    return [a[1] if names.count(a[1]) == 1 else "%s, %s" % (a[1], a[3]) for a in apps]


class ShareAudio(Skill):
    group = "audio sharing"
    does = "Shares an app's sound into a Discord/Vesktop call, with or without your mic, and undoes it"
    phrases = ("share <app> audio with my mic", "share audio only", "share <app> audio", "turn on audio sharing",
               "what's being shared", "stop sharing audio", "turn off audio sharing")
    gives = "speech; the call hears the app through the Share_App_Audio mic"
    permission = "auto"
    checks = "pactl afterwards: which app is in the share mix and whether the call is listening to it"

    def patterns(self):
        return [
            (r"(?:turn on|start|switch on) (?:the )?audio shar(?:ing|e)", lambda m: self.share("both", None)),
            (r"share %s(?:audio|sound) (?:with|and|plus) %s|share (?:the )?(?:sound|audio) and %s|"
             r"let the call hear (?:the )?(.+?) and me" % (APP, MIC, MIC),
             lambda m: self.share("both", m.group(1) or m.group(2))),
            (r"share %s(?:audio|sound)(?: only| without %s)?|play (?:the )?(.+?) into the call" % (APP, MIC),
             lambda m: self.share("audio", m.group(1) or m.group(2))),
            (r"what(?:'s| is) being shared|audio shar(?:e|ing) status|what can the call hear|"
             r"(?:is|am i) (?:audio )?shar(?:ing|e) (?:on|audio)", lambda m: self.status()),
            (r"(?:stop|end|undo) (?:sharing (?:the )?(?:audio|sound)|(?:the )?audio shar(?:e|ing))|"
             r"turn off (?:the )?audio shar(?:ing|e)|(?:turn|switch) (?:the )?audio shar(?:ing|e) off",
             lambda m: self.off()),
        ]

    def run(self, mode, answer=""):
        if not os.access(SCRIPT, os.X_OK):
            raise RuntimeError("%s is missing" % SCRIPT)
        done = subprocess.run([SCRIPT, mode], input=answer + "\n", capture_output=True, text=True, timeout=20)
        if done.returncode:
            raise RuntimeError((done.stderr or done.stdout).strip().splitlines()[-1])
        return done.stdout

    def share(self, mode, said):
        apps = playing()
        if said and said.lower() in ("the", "my", "some", "this", "that", "all"):  # "share the audio" names no app
            said = None
        if said:
            apps = named(said, apps)
            if not apps:
                others = [a[1] for a in playing()]
                return self.fail("I can't hear %s playing.%s" % (
                    said, " Playing now: %s." % and_list(others) if others else " Nothing is playing."))
        if len(apps) > 1:
            options = labels(apps)
            pick = self.j.choose("%s are playing. Which one?" % and_list(options), options)
            if pick is None:
                return self.say("Okay, not sharing.")
            apps = [apps[options.index(pick)]]
        out = self.run(mode, apps[0][0] if apps else "")
        mic = " with your mic" if mode == "both" else ", your mic stays out"
        line = ("Sharing %s into the call%s." % (apps[0][1], mic) if apps else
                "Nothing's playing, so the share mic is ready%s. Start the app and say share it by name." % mic)
        if "pointed" not in out:  # the call isn't listening yet
            line += " In Vesktop, set the input device to Share App Audio."
        self.say(line)

    def status(self):
        short = lambda *k: subprocess.run(["pactl", "list", "short"] + list(k), capture_output=True, text=True,
                                          timeout=5).stdout
        if "sharesink" not in short("sinks"):
            return self.say("Audio sharing is off.")
        ids = {line.split()[0] for line in short("sink-inputs").splitlines()
               if line.split()[1] == next((s.split()[0] for s in short("sinks").splitlines() if "sharesink" in s), "")}
        apps = [a[1] for a in playing() if a[0] in ids]
        mic = any("module-loopback" in m and "sink=micmix" in m and "sharesink" not in m
                  for m in short("modules").splitlines())
        self.say("Sharing %s into the call, mic %s." % (and_list(apps) if apps else "no app yet",
                                                         "on" if mic else "off"))

    def off(self):
        self.run("off")
        self.say("Audio sharing is off. Set Vesktop's input back to your microphone.")
