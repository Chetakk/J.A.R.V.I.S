"""Is the internet working, and if not, where does it stop? Each hop is checked, all at once: the link (nmcli, and
the Wi-Fi signal from /proc/net/wireless, so no scan), the router (ping the gateway), the internet by number (ping
1.1.1.1 and 8.8.8.8) and names (DNS). Read-only: nothing here changes a setting. diagnose() takes ~1-2 s."""
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

SLOW_ROUTER_MS = 40  # a router next door answers in a few ms; more is a busy or weak Wi-Fi
SLOW_INTERNET_MS = 150
LOSSY = 10  # percent of pings lost
WEAK_SIGNAL = 35  # percent


def _run(args, timeout=5):
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def ping(host, count=3):
    """{"ok", "loss" (percent), "ms" (average)} for a few quick pings to `host`."""
    _, out = _run(["ping", "-n", "-c", str(count), "-i", "0.2", "-W", "1", host], timeout=count + 3)
    loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)
    rtt = re.search(r"= [\d.]+/([\d.]+)/", out)
    loss = float(loss.group(1)) if loss else 100.0
    return {"ok": loss < 100, "loss": loss, "ms": float(rtt.group(1)) if rtt else None}


def resolve(name="google.com"):
    """{"ok", "ms"}: whether a name turns into an address (DNS), and how long it took."""
    start = time.time()
    code, out = _run(["getent", "ahosts", name], timeout=4)
    return {"ok": code == 0 and bool(out.strip()), "ms": (time.time() - start) * 1000}


def link():
    """The connection the internet goes through: {"device", "type", "connection", "gateway", "signal"} (signal in
    percent, Wi-Fi only), or {} when there's no default route. "wifi_on" says whether the Wi-Fi radio is on."""
    _, route = _run(["ip", "route", "show", "default"])
    _, radio = _run(["nmcli", "radio", "wifi"])
    found = {"wifi_on": radio.strip() == "enabled"}
    m = re.search(r"default via (\S+) dev (\S+)", route)
    if not m:
        return found
    found.update(gateway=m.group(1), device=m.group(2))
    _, devs = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,CONNECTION", "dev"])
    for line in devs.splitlines():
        dev, _, rest = line.partition(":")
        if dev == found["device"]:
            found["type"], _, found["connection"] = rest.partition(":")
    try:
        with open("/proc/net/wireless") as f:
            for line in f:
                if line.strip().startswith(found["device"] + ":"):
                    found["signal"] = min(100, round(float(line.split()[2].rstrip(".")) * 100 / 70))
    except (OSError, ValueError, IndexError):
        pass
    return found


def saved_wifi():
    """Saved Wi-Fi connections, most recently used first."""
    _, out = _run(["nmcli", "-t", "-f", "NAME,TYPE,TIMESTAMP", "con", "show"])
    rows = [line.rsplit(":", 2) for line in out.splitlines() if line.count(":") >= 2]
    wifi = [(int(ts or 0), name.replace("\\:", ":")) for name, kind, ts in rows if kind == "802-11-wireless"]
    return [name for _, name in sorted(wifi, reverse=True)]


def diagnose():
    """{"link", "router", "internet", "dns", "verdict" (one spoken line), "fix" ("wifi_on" / "reconnect" / None),
    "lines" (a line per check, for a card)}."""
    with ThreadPoolExecutor(4) as pool:
        here = pool.submit(link)
        net = [pool.submit(ping, "1.1.1.1"), pool.submit(ping, "8.8.8.8")]
        dns = pool.submit(resolve)
        here = here.result()
        router = ping(here["gateway"]) if here.get("gateway") else None
        net = min((f.result() for f in net), key=lambda p: (not p["ok"], p["loss"]))
        dns = dns.result()
    name = here.get("connection") or here.get("device") or ""
    wifi = here.get("type") == "wifi"
    signal = here.get("signal")
    lines = []
    if here.get("gateway"):
        lines.append("Link: %s (%s)%s" % (name, here.get("type") or here["device"],
                                          ", signal %d%%" % signal if signal is not None else ""))
    else:
        lines.append("Link: none (Wi-Fi radio %s)" % ("on" if here["wifi_on"] else "off"))
    for label, p in (("Router %s" % here.get("gateway", ""), router), ("Internet (1.1.1.1 / 8.8.8.8)", net)):
        if p:
            lines.append("%s: %s" % (label, "%.0f ms, %.0f%% lost" % (p["ms"], p["loss"]) if p["ok"] else
                                     "no answer"))
    lines.append("Names (DNS): %s" % ("%.0f ms" % dns["ms"] if dns["ok"] else "not resolving"))

    fix = None
    weak = wifi and signal is not None and signal < WEAK_SIGNAL
    if not here.get("gateway"):
        if not here["wifi_on"]:
            verdict, fix = "Wi-Fi is turned off, and there's no cable connection.", "wifi_on"
        else:
            verdict, fix = "You're not connected to any network.", "reconnect"
    elif not net["ok"]:
        if router and not router["ok"]:
            verdict, fix = "You're connected to %s, but the router isn't answering%s." % (
                name, ", and the signal is weak" if weak else ""), "reconnect"
        else:
            verdict = ("The router answers, but nothing past it does: the problem is the router's own internet "
                       "or your provider. Restarting the router usually fixes that.")
    elif not dns["ok"]:
        verdict, fix = ("The internet itself works, but names aren't turning into addresses, so websites won't "
                        "load. That's DNS."), "reconnect"
    else:
        slow = []
        if router and router["ok"] and router["ms"] > SLOW_ROUTER_MS:
            slow.append("the router takes %.0f milliseconds to answer, so the Wi-Fi is busy or weak" % router["ms"])
        if net["loss"] >= LOSSY:
            slow.append("%.0f percent of the pings got lost" % net["loss"])
        if net["ms"] and net["ms"] > SLOW_INTERNET_MS:
            slow.append("the internet takes %.0f milliseconds to answer" % net["ms"])
        if weak:
            slow.append("the Wi-Fi signal is only %d percent" % signal)
        if slow:
            verdict = "It works, but it's slow: %s." % "; ".join(slow)
        else:
            verdict = "Everything looks fine: the internet answers in %.0f milliseconds and names resolve." % (
                net["ms"] or 0)
    return {"link": here, "router": router, "internet": net, "dns": dns, "verdict": verdict, "fix": fix,
            "lines": lines}
