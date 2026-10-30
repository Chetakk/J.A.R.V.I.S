"""Network check (skills/network.py, system/network.py): says where the connection stops, offers a fix only when
one could help, and only changes anything after a yes."""
import pytest

ROUTE = "default via 192.168.1.1 dev wlan0 proto dhcp metric 600"
DEVICES = "wlan0:wifi:Home WiFi\neth0:ethernet:"
WIRELESS = ("Inter-| sta-|   Quality        |   Discarded packets\n face | tus | link level noise |  nwid\n"
            " wlan0: 0000   56.  -54.  -256        0      0      0      0      0        0\n")  # 56/70 = 80%


def pings(ms=12.3, loss=0):
    if loss == 100:
        return "3 packets transmitted, 0 received, 100% packet loss, time 2002ms\n"
    return ("3 packets transmitted, 3 received, %d%% packet loss, time 400ms\n"
            "rtt min/avg/max/mdev = 1.0/%s/20.0/1.0 ms\n" % (loss, ms))


@pytest.fixture
def online(jv):
    """Wi-Fi up, router and internet answering quickly, names resolving."""
    jv.shell.on(r"^ip route show default", out=ROUTE)
    jv.shell.on(r"^nmcli radio wifi$", out="enabled\n")
    jv.shell.on(r"^nmcli -t -f DEVICE,TYPE,CONNECTION dev", out=DEVICES)
    jv.shell.on(r"^nmcli -t -f NAME,TYPE,TIMESTAMP con show", out="Home WiFi:802-11-wireless:1761800000\n")
    jv.shell.on(r"^ping ", out=pings(3.1))
    jv.shell.on(r"^ping .* (1\.1\.1\.1|8\.8\.8\.8)$", out=pings(12.3))
    jv.shell.on(r"^getent ahosts", out="142.250.1.1 STREAM google.com\n")
    jv.files["/proc/net/wireless"] = WIRELESS
    return jv


def test_all_fine_says_so_and_offers_nothing(online):
    online.do("why is my internet slow")
    assert online.says("Everything looks fine", "12 milliseconds")
    assert "Home WiFi" in online.card_text() and "signal 80%" in online.card_text()
    assert not online.questions and not online.shell.ran(r"nmcli (radio wifi on|con up)")


def test_slow_router_is_named_as_the_reason(online):
    online.shell.on(r"^ping .* 192\.168\.1\.1$", out=pings(85))
    online.do("my wifi is laggy")
    assert online.says("slow", "router takes 85 milliseconds")


def test_lost_pings_are_reported(online):
    online.shell.on(r"^ping .* (1\.1\.1\.1|8\.8\.8\.8)$", out=pings(20, loss=33))
    online.do("diagnose my network")
    assert online.says("33 percent of the pings got lost")


def test_dns_failure_is_called_dns(online):
    online.shell.on(r"^getent ahosts", code=2)
    online.do("websites won't load", "no")
    assert online.says("That's DNS")
    assert online.questions == ["Want me to reconnect to Home WiFi?"]
    assert not online.shell.ran(r"nmcli con up")


def test_router_down_reconnects_only_after_yes(online):
    online.shell.on(r"^ping .* (1\.1\.1\.1|8\.8\.8\.8|192\.168\.1\.1)$", out=pings(loss=100))
    online.do("what's wrong with my wifi", "yes")
    assert online.says("router isn't answering")
    assert online.shell.ran(r"^nmcli con up id 'Home WiFi'$")
    assert online.says("Reconnecting")
    assert len([c for c in online.cards if c["title"] == "Network check"]) == 2  # checked again afterwards


def test_router_down_no_answer_changes_nothing(online):
    online.shell.on(r"^ping .* (1\.1\.1\.1|8\.8\.8\.8|192\.168\.1\.1)$", out=pings(loss=100))
    online.do("what's wrong with my wifi")  # no reply to "Want me to reconnect?"
    assert online.questions and not online.shell.ran(r"nmcli con up")


def test_internet_down_past_the_router_blames_the_router_not_wifi(online):
    online.shell.on(r"^ping .* (1\.1\.1\.1|8\.8\.8\.8)$", out=pings(loss=100))
    online.do("is my internet down")
    assert online.says("The router answers, but nothing past it does")
    assert not online.questions  # reconnecting this computer wouldn't help


def test_wifi_off_offers_to_turn_it_on(jv):
    jv.shell.on(r"^nmcli radio wifi$", out="disabled\n")
    jv.shell.on(r"^ping ", out=pings(loss=100))
    jv.shell.on(r"^getent", code=2)

    def wifi_comes_up(line):  # once it's on, the route and the internet come back
        jv.shell.on(r"^nmcli radio wifi$", out="enabled\n").on(r"^ip route show default", out=ROUTE)
        jv.shell.on(r"^nmcli -t -f DEVICE,TYPE,CONNECTION dev", out=DEVICES)
        jv.shell.on(r"^ping ", out=pings(9)).on(r"^getent", out="142.250.1.1 STREAM google.com")
        return ""
    jv.shell.on(r"^nmcli radio wifi on$", out=wifi_comes_up)
    jv.do("why isn't my wifi working", "yes")
    assert jv.says("Wi-Fi is turned off")
    assert jv.questions[0] == "Turn Wi-Fi on?"
    assert jv.shell.ran(r"^nmcli radio wifi on$")
    assert jv.says("Everything looks fine")  # checked again, and said what it found


def test_wifi_off_and_declined_leaves_it_off(jv):
    jv.shell.on(r"^nmcli radio wifi$", out="disabled\n")
    jv.shell.on(r"^ping ", out=pings(loss=100))
    jv.do("why isn't my wifi working", "no")
    assert not jv.shell.ran(r"nmcli radio wifi on")


def test_signal_strength(online):
    online.do("how strong is my wifi")
    assert online.said == ["The Wi-Fi signal on Home WiFi is 80 percent, that's strong."]


def test_signal_on_a_cable_says_not_on_wifi(online):
    online.shell.on(r"^ip route show default", out="default via 10.0.0.1 dev eth0")
    online.shell.on(r"^nmcli -t -f DEVICE,TYPE,CONNECTION dev", out="eth0:ethernet:Wired\n")
    online.do("wifi signal strength")
    assert online.said == ["You're not on Wi-Fi right now."]


def test_signal_with_nmcli_missing_does_not_crash(jv):
    jv.shell.on(r"^(nmcli|ip) ", raises=FileNotFoundError)
    jv.do("how strong is my wifi")
    assert jv.said == ["You're not connected to Wi-Fi."]
