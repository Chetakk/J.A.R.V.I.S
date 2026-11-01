""""Why is my internet slow", "what's wrong with my wifi", "diagnose my network": every hop is checked at once
(system/network.py, ~1 s) and the answer says where it stops: Wi-Fi off, no network, the router not answering, the
internet past the router, or names (DNS). When reconnecting could help, JARVIS offers it, then checks again.
"Am I online" and "what wifi am I on" stay quick commands in config/commands.yaml."""
import http.client
import shlex
import threading
import time
import urllib.request

from skills.base import Skill
from system import network

NET = r"(?:internet|internet connection|wi-?fi|wifi connection|network|network connection|connection|net)"
BAD = (r"(?:so )?(?:slow|down|bad|laggy|lagging|not working|broken|dropping|dropping out|acting up|flaky|"
       r"unstable|weird|keeps? dropping|cutting out|keeps? cutting out)")


class Network(Skill):
    group = "network"
    does = "Checks every hop of the connection and says where it stops"
    phrases = ("why is my internet slow", "what's wrong with my wifi", "how strong is my wifi (a live signal card)",
               "run a speed test (download and upload on gauges)")
    gives = "speech and a card"
    permission = "confirm"
    asks = "before turning Wi-Fi on or reconnecting"
    checks = "checks again after a fix and says what it found"

    def patterns(self):
        return [
            (r"(?:run |do |start |try )?(?:a |an )?(?:internet |network |wi-?fi |broadband )?speed ?test|"
             r"test (?:my |the )?(?:internet|network|wi-?fi|connection|broadband)(?:'s)? speed|"
             r"how fast is (?:my |the )?(?:internet|wi-?fi|connection|network|broadband)(?: right now| now)?|"
             r"(?:what'?s |check )?(?:my |the )?(?:internet|download|upload|connection|broadband) speed(?: now)?",
             lambda m: self.speed()),
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
        self.j.show({"title": "Network check", "text": "```path\n%s\n```\n\n%s\n\n%s" % (
            "\n".join(_path(report)), report["verdict"], "\n".join("- " + l for l in report["lines"]))})
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
        history = [here["signal"]]
        self.j.show(_signal_card(here, history))
        self.say("The Wi-Fi signal on %s is %d percent, that's %s." % (here["connection"], here["signal"], quality))
        if self.j.ui:  # kept live for a minute: walk around and watch it change
            threading.Thread(target=self._keep_signal, args=(history,), daemon=True).start()

    def speed(self):
        """Download, then upload, against Cloudflare's speed test, the gauges moving as it goes (about 15 s)."""
        self.say("Testing your connection, about fifteen seconds.")
        ping = network.ping("1.1.1.1")["ms"]
        show = self.j.show
        show(_speed_card(0, None, ping, "testing download…"))
        try:
            down = _download(lambda v: show(_speed_card(v, None, ping, "testing download…")))
            show(_speed_card(down, 0, ping, "testing upload…"))
            up = _upload(lambda v: show(_speed_card(down, v, ping, "testing upload…")))
        except (OSError, http.client.HTTPException, ValueError) as e:
            show(_speed_card(0, None, ping, "couldn't finish"))
            self.say("The speed test couldn't reach its server: %s." % getattr(e, "reason", e))
            return
        show(_speed_card(down, up, ping, "done"))
        self.say("Download %.0f megabits a second, upload %.0f%s." % (down, up, ", ping %.0f milliseconds" % ping
                                                                       if ping else ""))

    def _keep_signal(self, history, every=2, until=60):
        end = time.time() + until
        while time.time() < end:
            time.sleep(every)
            ui = self.j.ui
            if not (ui and ui.card_open("wifi")):
                return
            here = network.link()
            if here.get("signal") is None:
                return
            history.append(here["signal"])
            del history[:-30]
            ui.show_card(_signal_card(here, history))


SPEED_HOST = "speed.cloudflare.com"  # Cloudflare's public speed test: no account, nothing kept
SPEED_DOWN = 25 * 10 ** 6  # bytes at most, and SPEED_SECONDS at most, each way
SPEED_UP = 8 * 10 ** 6
SPEED_SECONDS = 8
SPEED_AGENT = "JARVIS-speedtest/1.0"  # Cloudflare turns away Python's default user agent


def _speed_card(down, up, ping, note):
    top = next(t for t in (50, 100, 300, 1000, 10 ** 6) if max(down or 0, up or 0) <= t)
    rows = ["Download: %.1f / %d Mbps" % (down or 0, top)] + (["Upload: %.1f / %d Mbps" % (up, top)] if up is not None
                                                              else [])
    return {"id": "speed", "title": "Speed test", "text": "```gauge\n%s\n```\n\n%s" % (
        "\n".join(rows), " · ".join(x for x in ("ping %.0f ms" % ping if ping else "", note) if x))}


def _download(show):
    done, start = 0, time.time()
    req = urllib.request.Request("https://%s/__down?bytes=%d" % (SPEED_HOST, SPEED_DOWN),
                                 headers={"User-Agent": SPEED_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        shown = start
        while time.time() - start < SPEED_SECONDS:
            chunk = r.read(65536)
            if not chunk:
                break
            done += len(chunk)
            if time.time() - shown > 0.5:
                shown = time.time()
                show(done * 8 / (shown - start) / 1e6)
    return done * 8 / max(0.001, time.time() - start) / 1e6


def _upload(show):
    conn = http.client.HTTPSConnection(SPEED_HOST, timeout=10)
    conn.putrequest("POST", "/__up")
    conn.putheader("Content-Type", "application/octet-stream")
    conn.putheader("User-Agent", SPEED_AGENT)
    conn.putheader("Content-Length", str(SPEED_UP))
    conn.endheaders()
    block, sent, start = b"0" * 65536, 0, time.time()
    shown = start
    try:
        while sent < SPEED_UP:
            conn.send(block[:min(len(block), SPEED_UP - sent)])
            sent += min(len(block), SPEED_UP - sent)
            if time.time() - shown > 0.5:
                shown = time.time()
                show(sent * 8 / (shown - start) / 1e6)
            if time.time() - start > SPEED_SECONDS:
                break
        took = time.time() - start
        conn.getresponse().read()
    finally:
        conn.close()
    return sent * 8 / max(0.001, took) / 1e6


def _path(report):
    """The checks as linked boxes (ui/blocks.py ```path): this computer, its link, the router, the internet, DNS."""
    here, router, net, dns = report["link"], report["router"], report["internet"], report["dns"]
    wifi = here.get("type") == "wifi"
    signal = here.get("signal")
    out = ["Jetson: ok"]
    if not here.get("gateway"):
        out.append("%s: bad %s" % ("Wi-Fi" if wifi or not here.get("type") else "Link",
                                   "radio off" if not here.get("wifi_on") else "not connected"))
        return out + ["Router: off", "Internet: off", "DNS: off"]
    weak = wifi and signal is not None and signal < network.WEAK_SIGNAL
    out.append("%s: %s %s" % ("Wi-Fi" if wifi else "Cable", "warn" if weak else "ok",
                              "%d%%" % signal if signal is not None else (here.get("connection") or "")))
    if router:
        out.append("Router: %s" % ("bad no reply" if not router["ok"] else "%s %.0f ms" % (
            "warn" if router["ms"] > network.SLOW_ROUTER_MS else "ok", router["ms"])))
    if not net["ok"]:
        out.append("Internet: bad no reply")
    else:
        slow = net["loss"] >= network.LOSSY or (net["ms"] or 0) > network.SLOW_INTERNET_MS
        out.append("Internet: %s %.0f ms" % ("warn" if slow else "ok", net["ms"] or 0))
    out.append("DNS: %s" % ("ok %.0f ms" % dns["ms"] if dns["ok"] else "bad not resolving"))
    return out


def _signal_card(here, history):
    signal = here["signal"]
    state = "ok" if signal >= 70 else "warn" if signal >= network.WEAK_SIGNAL else "bad"
    text = "```ring\nSignal: %d%% %s\n```" % (signal, state)
    if len(history) > 1:
        text += "\n\n```spark\nSignal: %s | %%\n```" % " ".join(str(v) for v in history)
    text += "\n\n%s · updates while this card is open" % (here.get("connection") or "Wi-Fi")
    return {"id": "wifi", "title": "Wi-Fi signal", "text": text}
