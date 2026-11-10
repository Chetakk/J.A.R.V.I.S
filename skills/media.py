"""Music and video: pause, play, next, previous (any MPRIS player, or YouTube), what's playing (said, and a card
with the album art, the song, a progress bar that moves while it plays, and previous / pause / next buttons).
"Play music" starts the Spotify web app (a Chrome --app window) and plays; its card follows the song, and stays
on screen while it plays (it hides by itself only once paused, or when you close it)."""
import hashlib
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from skills.base import Skill
from system import desktop

ART_DIR = os.path.expanduser("~/.cache/jarvis/art")
FOLLOW_FOR = 12 * 3600  # seconds the now-playing card is kept in step at most
WINDOW = "open.spotify.com"  # Spotify's window: an --app window of your main Chrome
CONNECT = "spotify-connect"  # playing_player()'s name for Spotify playing on another device (no bus name)


class Media(Skill):
    group = "media"
    does = "Music and video: any MPRIS player, or YouTube's own key; \"play music\" starts Spotify"
    phrases = ("pause", "play", "next song", "previous song", "what's playing", "play music", "play spotify")
    gives = "playback changed, or speech and a now-playing card that follows the song"
    permission = "auto"
    checks = "says when nothing is playing; play music checks Spotify's player says Playing"

    def __init__(self, jarvis):
        self._following = None  # the token of the one thread keeping the now-playing card up to date
        super().__init__(jarvis)

    def patterns(self):
        return [
            (r"(?:play|start|put on)(?: some| me some| my)? (?:music|songs|tunes)(?: (?:on|in|from) spotify)?|"
             r"(?:play|start|put on)(?: some| my)?(?: music| songs)? (?:on |in |from )?spotify|spotify,? play",
             lambda m: self.spotify()),
            (r"(?:(?:press|hit|click) )?(?:pause|stop)(?: the)?(?: music| video| song| youtube| playback)?|(?:kill|mute) the (?:music|song|video)", lambda m: self.media("Pause")),
            (r"(?:(?:press|hit|click) )?(?:play|resume|continue|unpause)(?: the)?(?: music| video| song| youtube| playback)?",
             lambda m: self.media("Play", music="video" not in m.group(0) and "youtube" not in m.group(0))),
            (r"(?:next|skip)(?: this)?(?: song| track| video)?", lambda m: self.media("Next")),
            (r"(?:previous|last|go back a) (?:song|track|video)", lambda m: self.media("Previous")),
            (r"what'?s playing|what (?:song|music) is (?:this|playing)|what'?s this song|what am i listening to|"
             r"show (?:me )?(?:the |a )?(?:spotify|music|song|now playing) card",
             lambda m: self.playing()),
        ]

    def spotify(self):
        """Play in Spotify: resume it if it's open, else start it, press its Space key until it plays."""
        player = _spotify_player()
        if _elsewhere(player):
            self.follow(spotify=True)  # on another device (the Echo): its card comes from the window title
            self.say("Spotify's already playing.")
            return
        if player:
            if _mpris_status(player[1]) != "Playing":
                player[1].call_sync("Play", None, 0, 2000, None)
            self.follow(spotify=True)
            return
        app = self.j.apps.find("spotify")
        if not app:
            return False  # not installed: let the browser / Claude have "play music"
        self.say(["Starting Spotify.", "Putting some music on."])
        threading.Thread(target=self._start_spotify, args=(app,), daemon=True, name="spotify").start()

    def _start_spotify(self, app):
        win = desktop.find_window(WINDOW)
        if not win:
            desktop.go_to_jarvis_workspace()
            if not self.j.apps.launch(app):
                self.fail("Hmm, Spotify didn't start.")
                return
        end = time.time() + 30  # a cold start is the window, then the web player loading
        while time.time() < end:
            time.sleep(1.5)
            win = desktop.find_window(WINDOW)
            if not win:
                continue
            desktop.focus(win)
            time.sleep(0.4)
            desktop.keys("space")  # Spotify's play / pause key; a key press counts as you starting it
            time.sleep(3)  # its player only appears on the bus once audio starts
            player = _spotify_player()
            if player and _mpris_status(player[1]) == "Playing":
                self.follow(spotify=True)
                return
        self.fail("Spotify's open, but it didn't start playing. Pick something in it and say what's playing." if win
                  else "Spotify didn't open its window.")

    def follow(self, spotify=False):
        """Show the now-playing card and keep it in step (song, pause, art) while it's on screen."""
        token = self._following = object()
        find = _spotify_player if spotify else _mpris_player

        def loop():
            shown, song, end = None, "", time.time() + FOLLOW_FOR
            while time.time() < end and self._following is token:
                player = find()
                # Spotify Connect (the Echo): Chrome's player says Stopped and names nothing, the window title does
                connect = spotify and not (player and _mpris_status(player[1]) in ("Playing", "Paused"))
                if connect:
                    now = spotify_title()
                    song = now or song  # paused, the title drops the song: keep the last one
                    card = _connect_card(song, bool(now)) if song else None
                else:
                    card = _now_playing(player[1], _metadata(player[1]), spotify) if player else None
                if not card:
                    if shown and self.j.ui:
                        self.j.ui.close_card("playing")  # the player quit: nothing to stay up for
                    return
                if shown and not (self.j.ui and self.j.ui.card_open("playing")):
                    return  # closed or replaced: stop
                key = card["text"].rsplit("time:", 1)[0] + card["choices"][1]["label"]  # the song, and paused or not
                if key != shown:
                    self.j.show(card)  # the progress bar moves by itself in between
                    shown = key
                time.sleep(2)

        threading.Thread(target=loop, daemon=True, name="now-playing").start()

    def media(self, action, music=False):
        player = _mpris_player()
        if action == "Pause" and _elsewhere(player):
            desktop.focus(desktop.find_window(WINDOW))
            time.sleep(0.4)
            desktop.keys("space")  # the web app pauses the device it plays on
            return
        if player:
            player[1].call_sync(action, None, 0, 2000, None)  # Play / Pause / Next / Previous
            return
        win = desktop.find_window("youtube")
        if win and action in ("Play", "Pause"):
            desktop.focus(win)
            time.sleep(0.4)
            desktop.keys("k")  # YouTube's own play/pause key
            return
        if music:
            return self.spotify()  # "play the music" (and "play some music", "play a song"...) with nothing open
        if action in ("Play", "Pause"):
            return False  # nothing playing: let "play X" go to the browser / Claude
        self.fail("Nothing seems to be playing.")

    def playing(self):
        player = _mpris_player()
        elsewhere = _elsewhere(player)
        if elsewhere:
            self.follow(spotify=True)
            title, _, artist = elsewhere.partition(" • ")
            self.say("It's %s%s." % (title, " by %s" % artist if artist else ""))
            return
        if not player:
            self.fail("Nothing seems to be playing.")
            return
        md = _metadata(player[1])
        title, artist = md.get("xesam:title"), ", ".join(md.get("xesam:artist") or [])
        if not title:
            self.say("Something's playing, but it doesn't say what.")
        else:
            self.follow(spotify=_is_spotify(player[0]))
            self.say("It's %s%s." % (title, " by %s" % artist if artist else ""))


def _gdbus(*args):
    return subprocess.run(["gdbus", "call", "--session"] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=2, universal_newlines=True).stdout


def _player_property(name, prop):
    return _gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                  "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player", prop)


def playing_player():
    """The bus name of a player (Chrome, Spotify...) that says it's playing, else None (~20 ms).
    Asked through gdbus with a time limit: asked in-process, a player that never answered left the show recorder stuck."""
    try:
        names = _gdbus("--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus",
                       "--method", "org.freedesktop.DBus.ListNames")
        for name in re.findall(r"'(org\.mpris\.MediaPlayer2\.[^']+)'", names):
            if "'Playing'" in _player_property(name, "PlaybackStatus"):
                return name
    except (OSError, subprocess.SubprocessError):
        pass
    return CONNECT if spotify_title() else None


def something_playing():
    """True while a player says it's playing: its talk mustn't pass for yours."""
    return playing_player() is not None


def track(name):
    """"Song · Artist" of what the player on bus `name` plays, "" if it doesn't say."""
    if name == CONNECT:
        return spotify_title().replace(" • ", " · ")
    try:
        md = _player_property(name, "Metadata")
    except (OSError, subprocess.SubprocessError):
        return ""
    title = re.search(r"'xesam:title': <(['\"])(.+?)\1>", md)  # gdbus quotes with " when the text has a '
    artist = re.search(r"'xesam:artist': <\[(['\"])(.+?)\1", md)
    return " · ".join(m.group(2) for m in (title, artist) if m) if title else ""


def spotify_title():
    """"Song • Artist" from the Spotify web app's window title while it plays, here or on another device (Spotify
    Connect, e.g. the Echo: its Chrome player then says Stopped, but the title still names the song); else ""."""
    win = desktop.find_window(WINDOW)
    return win.title if win and " • " in win.title else ""


def _elsewhere(player):
    """Spotify's "Song • Artist" when it plays on another device, i.e. no player here says Playing; else ""."""
    return "" if player and _mpris_status(player[1]) == "Playing" else spotify_title()


def progress(name):
    """(seconds in, song length) of what the player on bus `name` plays; (0, 0) if it doesn't say."""
    try:
        length = re.search(r"'mpris:length': <u?int64 (\d+)>", _player_property(name, "Metadata"))
        pos = re.search(r"<int64 (-?\d+)>", _player_property(name, "Position"))
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0.0
    return (int(pos.group(1)) / 1e6, int(length.group(1)) / 1e6) if pos and length else (0.0, 0.0)


def _mpris_player():
    """(name, proxy) of the media player that's playing (or any, if none is)."""
    players = _players()
    return players[0] if players else None


def _spotify_player():
    return next((p for p in _players() if _is_spotify(p[0])), None)


def _is_spotify(name):
    """True if the bus name is Chrome's player and it holds Spotify: Spotify's window is open and the song names an
    album (YouTube's videos don't). Spotify runs in your main Chrome, which also plays YouTube, on the same bus.
    shortcut: YouTube Music names an album too; tell them apart (e.g. by window title) if both get used."""
    if "chrom" not in name or not desktop.find_window(WINDOW):
        return False
    try:
        return bool(re.search(r"'xesam:album': <(['\"])(?!\1)", _player_property(name, "Metadata")))
    except (OSError, subprocess.SubprocessError):
        return False


def _players():
    """[(name, proxy)] of every MPRIS player, the playing ones first."""
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
        return players
    except Exception:
        return []


def _now_playing(proxy, md, spotify=False):
    """The now-playing card: a ```music block (art, song, artist, album, moving progress) and buttons."""
    playing = _mpris_status(proxy) == "Playing"
    length = (md.get("mpris:length") or 0) / 1e6
    try:
        from gi.repository import GLib
        pos = proxy.call_sync("org.freedesktop.DBus.Properties.Get",
                              GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "Position")), 0, 1000,
                              None).unpack()[0] / 1e6
    except Exception:  # some players don't say where they are
        pos = None
    one = lambda v: " ".join(str(v or "").split())  # noqa: E731  (a line each in the block)
    lines = ["title: " + one(md.get("xesam:title")), "artist: " + one(", ".join(md.get("xesam:artist") or [])),
             "album: " + one(md.get("xesam:album"))]
    art = _art(md.get("mpris:artUrl"))
    if art:
        lines.append("art: " + art)
    state = "playing" if playing else "paused"
    lines.append("time: %d/%d %s" % (min(pos, length), length, state) if length and pos is not None else
                 "time: " + state)
    return _music_card(lines, playing, "Spotify" if spotify else "Now playing")


def _connect_card(song, playing):
    """The card for Spotify playing on another device: song and artist from "Song • Artist", no art or progress."""
    title, _, artist = song.partition(" • ")
    return _music_card(["title: " + title, "artist: " + artist, "album: ",
                        "time: " + ("playing" if playing else "paused")], playing, "Spotify")


def _music_card(lines, playing, title):
    return {"id": "playing", "title": title, "stay": playing,  # up while it plays
            "text": "```music\n%s\n```" % "\n".join(lines),
            "choices": [{"label": "Previous", "do": "previous song"},
                        {"label": "Pause" if playing else "Play", "do": "pause" if playing else "play"},
                        {"label": "Next", "do": "next song"}]}


def _metadata(proxy):
    md = proxy.get_cached_property("Metadata")
    return md.unpack() if md else {}


def _art(url):
    """A local file for the player's album art (file:// as it is, http(s) fetched once into ART_DIR), or None."""
    if not url:
        return None
    if url.startswith("file://"):
        path = urllib.parse.unquote(url[7:])
        return path if os.path.isfile(path) else None
    if not url.startswith(("https://", "http://")):
        return None
    path = os.path.join(ART_DIR, hashlib.sha1(url.encode()).hexdigest()[:16])
    if not os.path.isfile(path):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                data = r.read(5_000_000)
            os.makedirs(ART_DIR, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        except OSError:
            return None
    return path


def _mpris_status(proxy):
    st = proxy.get_cached_property("PlaybackStatus")
    return st.unpack() if st else None
