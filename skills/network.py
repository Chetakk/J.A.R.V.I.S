""""Why is my internet slow", "what's wrong with my wifi", "diagnose my network": every hop is checked at once
(system/network.py, ~1 s) and the answer says where it stops: Wi-Fi off, no network, the router not answering, the
internet past the router, or names (DNS). When reconnecting could help, JARVIS offers it, then checks again.
"Am I online" and "what wifi am I on" stay quick commands in config/commands.yaml."""
import shlex
import time

from skills.base import Skill
from system import network

NET = r"(?:internet|internet connection|wi-?fi|wifi connection|network|network connection|connection|net)"
BAD = (r"(?:so )?(?:slow|down|bad|laggy|lagging|not working|broken|dropping|dropping out|acting up|flaky|"
       r"unstable|weird|keeps? dropping|cutting out|keeps? cutting out)")


class Network(Skill):
    group = "network"
    does = "Checks every hop of the connection and says where it stops"
    phrases = ("why is my internet slow", "what's wrong with my wifi", "how strong is my wifi")
    gives = "speech and a card"
    permission = "confirm"
    asks = "before turning Wi-Fi on or reconnecting"
    checks = "checks again after a fix and says what it found"

    def patterns(self):
        return [
            (r"(?:can you |please )?(?:diagnose|check|test|troubleshoot|look at|debug|fix) (?:my |the )?%s(?: for me)?|"
             r"(?:run )?(?:a )?(?:network|internet|wi-?fi) (?:check|test|diagnostics?|diagnosis)|"
             r"why (?:is|isn'?t|is not|won'?t|wont|doesn'?t|does not|can'?t|cannot|did) (?:my |the )?%s(?: so)? "
             r"(?:working|work|slow|down|loading|load|connecting|connect|lagging|bad|dropping|dropping out|"
             r"so slow|cutting out|acting up)(?: today| now| right now| anymore)?|"
             r"what'?s (?:wrong|up|going on) with (?:my |the )?%s|(?:my |the )?%s (?:is |keeps )?%s|"
             r"is (?:my |the )?%s (?:slow|down|okay|ok|fine|good|bad|stable)(?: today| now| right now)?|"
             r"(?:websites?|web ?pages?|pages?|the web|sites?) (?:aren'?t|are not|won'?t|wont|don'?t|do not) "
             r"(?:load|loading|open|opening)|how'?s (?:my |the )?%s(?: doing| today| now)?"
             % (NET, NET, NET, NET, BAD, NET, NET), lambda m: self.diagnose()),
            (r"how (?:strong|good) is (?:my |the )?wi-?fi(?: signal)?|(?:what'?s )?(?:my |the )?wi-?fi signal"
             r"(?: strength)?|wi-?fi strength|signal strength", lambda m: self.signal()),
        ]

    def diagnose(self, again=False):
        report = network.diagnose()
        self.j.show({"title": "Network check", "text": "\n".join("- " + l for l in report["lines"])
                     + "\n\n" + report["verdict"]})
        self.say(report["verdict"])
        if again or not report["fix"]:
            return
        if report["fix"] == "wifi_on":
            if not self.j.ask("Turn Wi-Fi on?"):
                return
            self.j.executor.run("nmcli radio wifi on", reply="Turning Wi-Fi on.")
        else:
            current = report["link"].get("connection") if report["link"].get("type") == "wifi" else None
            name = current or next(iter(network.saved_wifi()), None)
            if not name or not self.j.ask("Want me to %s %s?" % ("reconnect to" if current else "connect to", name)):
                return
            self.say("Reconnecting. It takes a few seconds.")
            self.j.executor.run("nmcli con up id %s" % shlex.quote(name), output=True, timeout=30)
        for _ in range(10):  # the new connection needs a moment for its address and route
            if network.link().get("gateway"):
                break
            time.sleep(1)
        self.diagnose(again=True)

    def signal(self):
        here = network.link()
        if here.get("type") != "wifi" or here.get("signal") is None:
            self.say("You're not on Wi-Fi right now." if here.get("gateway") else "You're not connected to Wi-Fi.")
            return
        quality = "strong" if here["signal"] >= 70 else "okay" if here["signal"] >= network.WEAK_SIGNAL else "weak"
        self.say("The Wi-Fi signal on %s is %d percent, that's %s." % (here["connection"], here["signal"], quality))
