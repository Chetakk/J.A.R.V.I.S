"""Music and video: pause, play, next, previous, skip / rewind, restart the song (any MPRIS player, or YouTube),
what's playing (said, and a card with the album art, the song, a progress bar that moves while it plays, and
previous / pause / next buttons). "Play music" starts the Spotify web app (a Chrome --app window) and plays; its card
follows the song, and stays on screen while it plays (it hides by itself only once paused, or when you close it).
Through JARVIS Link (tools/chrome-link) it also drives Spotify's own page: "play <a playlist, album or artist of
yours>", "play <song> by <artist>" / "play <x> on spotify" (Spotify's search), "play my usual", "what playlists do I
have", shuffle, repeat, "like this song", "spotify volume 40". Your library is kept in STORE, with how often JARVIS
played each one: what you usually listen to is the most played, then Spotify's own Recents order."""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from skills.base import Skill
from skills.studybuddy import N, NUM
from skills.videos import best
from system import chrome_link, desktop

ART_DIR = os.path.expanduser("~/.cache/jarvis/art")
FOLLOW_FOR = 12 * 3600  # seconds the now-playing card is kept in step at most
WINDOW = "open.spotify.com"  # Spotify's window: an --app window of your main Chrome
CONNECT = "spotify-connect"  # playing_player()'s name for Spotify playing on another device (no bus name)
STORE = os.path.expanduser("~/.local/share/jarvis/spotify.json")  # {"library": [...], "at": read at, "plays": {path: n}}
LIBRARY_FOR = 600  # seconds a read of your library is trusted while Spotify is open
SPOTIFY_WORDS = r"(?:on|in|from|with) spotify"


class Media(Skill):
    group = "media"
    does = "Music and video: any MPRIS player, or YouTube's own key; \"play music\" starts Spotify"
    phrases = ("pause", "play", "next song", "previous song", "what's playing", "play music", "play spotify",
               "play my <x> playlist", "play <x> by <y>", "play <x> on spotify", "play my usual",
               "what playlists do I have", "shuffle on", "repeat this song", "repeat off", "like this song",
               "spotify volume <n>", "restart the song")
    gives = "playback changed, or speech and a now-playing card that follows the song; your playlists as a card"
    permission = "auto"
    checks = ("says when nothing is playing; play music checks Spotify's player says Playing; Spotify's page answers "
              "with its shuffle / repeat / like / volume state after each change")

    def __init__(self, jarvis):
        self._following = None  # the token of the one thread keeping the now-playing card up to date
        super().__init__(jarvis)

    def patterns(self):
        return [
            (r"(?:play|start|put on)(?: some| me some| my)? (?:music|songs|tunes)(?: (?:on|in|from) spotify)?|"
             r"(?:play|start|put on)(?: some| my)?(?: music| songs)? (?:on |in |from )?spotify|spotify,? play",
             lambda m: self.spotify()),
            (r"(?:play|put on) (?:my usual|what i (?:usually|normally|always) (?:listen to|play)|my (?:favourite|favorite|"
             r"usual) (?:music|songs|playlist))(?: %s)?" % SPOTIFY_WORDS, lambda m: self.play_usual()),
            (r"(?:what|which) (?:playlists|albums|music) (?:do i have|have i got)(?: %s)?|(?:show|list|read)(?: me)? my "
             r"(?:spotify )?(?:playlists|library|albums)|my (?:spotify )?playlists|what do i (?:usually|normally) "
             r"listen to|(?:refresh|update|reload) my (?:spotify )?(?:playlists|library)" % SPOTIFY_WORDS,
             lambda m: self.show_library(fresh=m.group(0).lower().startswith(("refresh", "update", "reload")))),
            (r"(?:play|put on|start)(?: me)?(?: (?:the|some|a))? (?:songs?|tracks?|music) (?:by|from) (.+?)(?: %s)?"
             % SPOTIFY_WORDS, lambda m: self.play_named(m.group(1))),
            (r"(?:play|put on|start)(?: me)?(?: the)? (?:song|track) (.+?)(?: %s)?" % SPOTIFY_WORDS,
             lambda m: self.play_named(m.group(1), song=True)),
            (r"(?:play|put on|start)(?: me)? (.+?) %s" % SPOTIFY_WORDS, lambda m: self.play_named(m.group(1))),
            (r"(?:play|put on|start) (?:my|the) (.+?) (?:playlist|album|mix)", lambda m: self.play_named(m.group(1))),
            (r"(?:play|put on|start)(?: me)? (.+? by .+)", lambda m: self.play_named(m.group(1), song=True)),
            (r"(?:turn |switch |put )?shuffle (on|off)|(?:turn|switch) (on|off) (?:the )?shuffle|(shuffle)(?: (?:the |this "
             r"|my )?(?:music|songs|playlist|album|it))?|(?:stop|don't|no) (shuffl)(?:e|ing)|(unshuffle)",
             lambda m: self.toggle("shuffle", next(g for g in m.groups() if g).lower() in ("on", "shuffle"))),
            (r"repeat (?:this|the) (?:song|track)|(?:put |keep )?(?:this|the) (?:song|track) on repeat|repeat one",
             lambda m: self.toggle("repeat", "one")),
            (r"(?:turn |switch |put )?repeat (on|off)|(?:turn|switch) (on|off) (?:the )?repeat|repeat (?:the |this "
             r"|my )?(playlist|album|all)|(?:stop|don't|no) (repeat)(?:ing)?(?: (?:the|this) (?:song|track|playlist))?",
             lambda m: self.toggle("repeat", next(g for g in m.groups() if g).lower() not in ("off", "repeat"))),
            (r"(?:like|love|save|heart) (?:this|the|that)(?: song| track)?(?: %s)?|add (?:this|the|that)(?: song| track)? "
             r"to (?:my )?liked songs|i (?:like|love) this (?:song|track)" % SPOTIFY_WORDS, lambda m: self.like()),
            (r"(?:set |turn )?spotify(?:'s)? volume(?: to)? (\d+)(?: ?%| percent)?|(?:set |turn )?(?:the )?volume (?:of|on|in) "
             r"spotify(?: to)? (\d+)(?: ?%| percent)?", lambda m: self.spotify_volume(int(m.group(1) or m.group(2)))),
            (r"(?:turn )?spotify (up|down|louder|quieter)|(?:turn|make) spotify (up|down|louder|quieter)",
             lambda m: self.spotify_volume(step=1 if (m.group(1) or m.group(2)).lower() in ("up", "louder") else -1)),
            (r"(?:(?:press|hit|click) )?(?:pause|stop)(?: the)?(?: music| video| song| youtube| playback)?|(?:kill|mute) the (?:music|song|video)", lambda m: self.media("Pause")),
            (r"(?:(?:press|hit|click) )?(?:play|resume|continue|unpause)(?: the)?(?: music| video| song| youtube| playback)?",
             lambda m: self.media("Play", music="video" not in m.group(0) and "youtube" not in m.group(0))),
            (r"(?:next|skip)(?: this)?(?: song| track| video)?", lambda m: self.media("Next")),
            (r"(?:previous|last|go back a) (?:song|track|video)", lambda m: self.media("Previous")),
            (r"what'?s playing|what (?:song|music) is (?:this|playing)|what'?s this song|what am i listening to|"
             r"show (?:me )?(?:the |a )?(?:spotify|music|song|now playing) card",
             lambda m: self.playing()),
            (r"(?:restart|replay) (?:this|the) (?:song|track)|(?:play )?(?:this |the )?(?:song|track) (?:again )?from the "
             r"(?:start|beginning|top)|start (?:this|the) (?:song|track) (?:again|over)", lambda m: self.restart()),
            # After videos.py's "skip 10 seconds" (the video on the page): with no video there, the playing player
            (r"(?:skip|jump|go|fast forward|move)(?: ahead| forward| forwards| on)?(?: by)? %s (seconds?|secs?|"
             r"minutes?|mins?)(?: ahead| forward| on)?" % N, lambda m: self.seek_by(m, 1)),
            (r"(?:go back|rewind|jump back|skip back|back)(?: by)? %s (seconds?|secs?|minutes?|mins?)|(?:go|jump|skip) "
             r"%s (seconds?|secs?|minutes?|mins?) back(?:wards)?" % (N, N), lambda m: self.seek_by(m, -1)),
            # Last: "play <x>" is Spotify's only when x is in your library (else the browser / Claude has it)
            (r"(?:play|put on)(?: me)? (.+)", lambda m: self.play_named(m.group(1), only_yours=True)),
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

    # --- Spotify's own page, through JARVIS Link ---

    def library(self, fresh=False):
        """Your library as Spotify lists it ([{"name", "sub", "path"}]), read again when it's old and Spotify's open."""
        data = _load()
        if fresh or time.time() - data.get("at", 0) > LIBRARY_FOR:
            r = _page("library", timeout=15)
            if r and r.get("items"):
                data.update(library=r["items"], at=time.time())
                _save(data)
        return data.get("library", [])

    def _usual(self, n=1):
        """What you usually listen to: the most played by JARVIS, then Spotify's own order (Recents by default)."""
        lib, plays = self.library(), _load().get("plays", {})
        return sorted(lib, key=lambda it: -plays.get(it["path"], 0))[:n]

    def _ready(self):
        """True once Spotify's page answers; starts it when it isn't open (False when it can't be reached)."""
        if _page("state"):
            return True
        if desktop.find_window(WINDOW):
            self.fail("Spotify's open, but JARVIS Link can't reach it. Reload JARVIS Link in chrome://extensions.")
            return None
        app = self.j.apps.find("spotify")
        if not app:
            return False
        self.say("Starting Spotify.")
        desktop.go_to_jarvis_workspace()
        if not self.j.apps.launch(app):
            self.fail("Hmm, Spotify didn't start.")
            return None
        end = time.time() + 30
        while time.time() < end:
            time.sleep(1.5)
            if _page("state"):
                return True
        self.fail("Spotify didn't open its window.")
        return None

    def play_named(self, said, song=False, only_yours=False):
        """One of yours by name (a playlist, album, artist), else Spotify's search: its top result, or for a song
        its first track. only_yours ("play <x>" without Spotify in it): hand it on unless x is in your library."""
        said = re.sub(r"^(?:my|the|some|a) |\s+(?:playlist|album|mix|songs|music)$", "", said.strip(), flags=re.I)
        if not said or re.fullmatch(r"(?:it|this|that|music|songs?|something|anything|again|.* on youtube)", said, re.I):
            return False
        item = None if song else best(said, [dict(it, title=it["name"]) for it in self.library()],
                                       strict=only_yours)
        if only_yours and not item:
            return False
        ready = self._ready()
        if not ready:
            return ready  # False: no Spotify at all, so the browser / Claude can have it
        path = item["path"] if item else "/search/" + urllib.parse.quote(said) + ("/tracks" if song else "")
        r = _page("play", timeout=12, path=path)
        if not (r and r.get("played")):
            self.fail("Spotify didn't find %s." % said if not item else "Spotify wouldn't play %s." % item["name"])
            return
        if item:
            data = _load()
            data.setdefault("plays", {})[item["path"]] = data.get("plays", {}).get(item["path"], 0) + 1
            _save(data)
        self.say("Playing %s." % (item["name"] if item else said))
        self.follow(spotify=True)

    def play_usual(self):
        usual = self._usual()
        if not usual:
            return self.spotify()  # nothing known yet: whatever Spotify had on
        return self.play_named(usual[0]["name"])

    def show_library(self, fresh=False):
        lib = self.library(fresh=fresh)
        if not lib:
            self.fail("I haven't seen your Spotify library yet. Open Spotify and ask again.")
            return
        usual = self._usual(3)
        self.j.show({"id": "spotify-library", "title": "Your Spotify",
                     "text": "\n".join("- **%s**%s" % (it["name"], " · " + it["sub"] if it["sub"] else "") for it in lib[:15])
                             + ("\n\n…and %d more" % (len(lib) - 15) if len(lib) > 15 else ""),
                     "choices": [{"label": it["name"][:24], "do": "play %s on spotify" % it["name"]} for it in usual]})
        self.say("You usually listen to %s." % _and(it["name"] for it in usual))

    def _control(self, do, **args):
        """Spotify's state after `do`, or None (said why) when its page can't be reached or has no such button."""
        if not _page("state"):
            self.fail("Spotify isn't open." if not desktop.find_window(WINDOW) else
                      "JARVIS Link can't reach Spotify. Reload it in chrome://extensions.")
            return None
        r = _page(do, **args)
        if not r:
            self.fail("Spotify's %s button isn't there." % do)
        return r

    def toggle(self, what, on):
        r = self._control(what, on=on, timeout=4)
        if r is None:
            return
        now = r.get(what)
        if what == "repeat":
            self.say({True: "Repeating the playlist.", "one": "Repeating this song.", False: "Repeat off."}.get(now, "Done."))
        else:
            self.say("Shuffle on." if now else "Shuffle off.")

    def like(self):
        r = self._control("like")
        if r is not None:
            self.say("Added to your Liked Songs." if r.get("liked") else "Hmm, it didn't take.")

    def spotify_volume(self, percent=None, step=0):
        state = _page("state") or {}
        to = percent / 100 if percent is not None else (state.get("volume") or 0.5) + 0.15 * step
        r = self._control("volume", to=max(0.0, min(1.0, to)))
        if r is not None and r.get("volume") is not None:
            self.say("Spotify at %d%%." % round(r["volume"] * 100))

    def seek_by(self, m, sign):
        n, unit = [x for x in m.groups() if x][:2]
        secs = (NUM[n.lower()] if n.lower() in NUM else float(n)) * (60 if unit.lower().startswith("m") else 1)
        player = _mpris_player()
        if not player or _mpris_status(player[1]) not in ("Playing", "Paused"):
            self.fail("Nothing seems to be playing.")
            return
        from gi.repository import GLib
        player[1].call_sync("Seek", GLib.Variant("(x)", (int(sign * secs * 1e6),)), 0, 2000, None)

    def restart(self):
        player = _mpris_player()
        track = player and _metadata(player[1]).get("mpris:trackid")
        if not track:
            self.fail("Nothing seems to be playing.")
            return
        from gi.repository import GLib
        player[1].call_sync("SetPosition", GLib.Variant("(ox)", (track, 0)), 0, 2000, None)

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


def _page(do, timeout=3.0, **args):
    """What Spotify's page answered (its state, or what `do` gave), or None: not open, no JARVIS Link, or it said
    why it couldn't (no such button)."""
    r = chrome_link.call("spotify", timeout=timeout, do=do, **args)
    result = r and r.get("result")
    return result if isinstance(result, dict) and "error" not in result else None


def _load():
    try:
        with open(STORE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(STORE + ".tmp", STORE)


def _and(names):
    names = list(names)
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else "".join(names)


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
                        {"label": "Next", "do": "next song"}] +
                       ([{"label": "Like", "do": "like this song"}] if title == "Spotify" else [])}


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
