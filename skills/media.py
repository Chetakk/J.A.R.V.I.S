"""Music and video: pause, play, next, previous (any MPRIS player, or YouTube), what's playing (said, and a card
with the song, a progress bar that moves while it plays, and previous / pause / next buttons)."""
import time

from skills.base import Skill
from system import desktop


class Media(Skill):
    group = "media"
    does = "Music and video: any MPRIS player, or YouTube's own key"
    phrases = ("pause", "play", "next song", "previous song", "what's playing")
    gives = "playback changed, or speech and a now-playing card"
    permission = "auto"
    checks = "not checked; says when nothing is playing"

    def patterns(self):
        return [
            (r"(?:(?:press|hit|click) )?(?:pause|stop)(?: the)?(?: music| video| song| youtube| playback)?|(?:kill|mute) the (?:music|song|video)", lambda m: self.media("Pause")),
            (r"(?:(?:press|hit|click) )?(?:play|resume|continue|unpause)(?: the)?(?: music| video| song| youtube| playback)?",
             lambda m: self.media("Play")),
            (r"(?:next|skip)(?: this)?(?: song| track| video)?", lambda m: self.media("Next")),
            (r"(?:previous|last|go back a) (?:song|track|video)", lambda m: self.media("Previous")),
            (r"what'?s playing|what (?:song|music) is (?:this|playing)|what'?s this song|what am i listening to",
             lambda m: self.playing()),
        ]

    def media(self, action):
        player = _mpris_player()
        if player:
            player[1].call_sync(action, None, 0, 2000, None)  # Play / Pause / Next / Previous
            return
        win = desktop.find_window("youtube")
        if win and action in ("Play", "Pause"):
            desktop.focus(win)
            time.sleep(0.4)
            desktop.keys("k")  # YouTube's own play/pause key
            return
        if action in ("Play", "Pause"):
            return False  # nothing playing: let "play X" go to the browser / Claude
        self.say("Nothing seems to be playing.")

    def playing(self):
        player = _mpris_player()
        if not player:
            self.say("Nothing seems to be playing.")
            return
        md = player[1].get_cached_property("Metadata")
        md = md.unpack() if md else {}
        title, artist = md.get("xesam:title"), ", ".join(md.get("xesam:artist") or [])
        if not title:
            self.say("Something's playing, but it doesn't say what.")
        else:
            self.j.show(_now_playing(player[1], md))
            self.say("It's %s%s." % (title, " by %s" % artist if artist else ""))


def _mpris_player():
    """(name, proxy) of the media player that's playing (or any, if none is)."""
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                              None, None, 0, 2000, None).unpack()[0]
        players = []
        for n in names:
            if n.startswith("org.mpris.MediaPlayer2."):
                proxy = Gio.DBusProxy.new_sync(bus, 0, None, n, "/org/mpris/MediaPlayer2",
                                               "org.mpris.MediaPlayer2.Player", None)
                players.append((n, proxy))
        players.sort(key=lambda p: _mpris_status(p[1]) != "Playing")
        return players[0] if players else None
    except Exception:
        return None


def _now_playing(proxy, md):
    """The now-playing card: title, artist and album, a ```progress bar, and buttons."""
    playing = _mpris_status(proxy) == "Playing"
    length = (md.get("mpris:length") or 0) / 1e6
    try:
        from gi.repository import GLib
        pos = proxy.call_sync("org.freedesktop.DBus.Properties.Get",
                              GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "Position")), 0, 1000,
                              None).unpack()[0] / 1e6
    except Exception:  # some players don't say where they are
        pos = None
    lines = ["**%s**" % md.get("xesam:title")]
    who = " · ".join(x for x in (", ".join(md.get("xesam:artist") or []), md.get("xesam:album") or "") if x)
    if who:
        lines.append(who)
    if length and pos is not None:
        lines += ["", "```progress\n%s: %d/%d %s\n```" % ("Playing" if playing else "Paused", min(pos, length), length,
                                                         "playing" if playing else "paused")]
    return {"id": "playing", "title": "Now playing", "text": "\n".join(lines),
            "choices": [{"label": "Previous", "do": "previous song"},
                        {"label": "Pause" if playing else "Play", "do": "pause" if playing else "play"},
                        {"label": "Next", "do": "next song"}]}


def _mpris_status(proxy):
    st = proxy.get_cached_property("PlaybackStatus")
    return st.unpack() if st else None
