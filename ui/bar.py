"""The voice bar: a glowing glass pill along the bottom of the screen, the orb at its left and the talk beside it.

It sits in the strip the dock reserves: while the bar is up only the dock's picture is hidden, the strip stays
reserved, so no window moves. A click opens the chat. It animates only while JARVIS is busy or the bar is moving.
"""
import math
import random
import re
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from ui.blocks import _round_rect  # noqa: E402
from ui.feelings import Body, Music  # noqa: E402
from ui.window import ANIMATED, CLAUDE_RGB, LABELS, _clear, _make_transparent  # noqa: E402

PAD = 12  # transparent room around the pill for the glow
PILL = 56
TEXT = 11.5  # point size of what you said
STRIP = 62  # the height of the dock's strip: the pill sits in its middle
HEIGHT = PILL + 2 * PAD
FACELESS = 22  # where the words start, from the pill's end, while a card shows the orb (the dashboard) instead
# (the bar keeps its size: the words just start further left)
FACE_FADE = 0.3  # seconds the bar's face takes to shrink away (and to come back) as the dashboard opens (closes)
FULL_W = 420 + 2 * PAD  # the widest the pill gets: shorter than the dock, about as tall; it fits what it says
COMPACT_W = 296
BREATH = 100  # ms between idle frames: the slow breath needs no more than 10 a second
BLINK = 5.0  # seconds between the idle eyes' blinks
FOLLOW = 2.0  # seconds the idle eyes keep watching the pointer after it stops
GAZE_W = 14.0  # the eyes' spring: they ease in and out and settle in about a third of a second, never overshooting
LAZY = {"sleepy", "hot"}  # drowsy moods: the eyes follow the pointer too, but slowly and only part of the way
LAZY_W, LAZY_REACH = 3.0, 0.4  # their spring (settles in about 1.5 s) and how far they turn
MOOD_SECS = 5.0  # how long the idle eyes show a mood
DANCES = ("hop", "nod", "groove", "seesaw", "pulse")  # dance moves, one at random at a time (ui/window.py draws them)
SWAYS = ("smile", "notes")  # moves for slow, relaxed music, rotated the same way
STYLE_SECS = (30, 90)  # a move is kept at least this long, then swapped for another at a random moment
MOVING = {"dance", "sway", "hot", "squeezed"}  # moods from music or the computer that move all the time
# (mood, who said it, words that bring it on), first match wins. Two failures in two minutes: stressed, not sad.
MOODS = (
    ("happy", "you", r"\b(thanks?|thank you|good (job|boy)|well done|great job|awesome|love you|you'?re the best|nice one)\b"),
    ("angry", "you", r"\b(stupid|idiot|useless|dumb|shut up|hate you|annoying)\b"),
    ("sad", "you", r"\b(i'?m (so |really |very )?(sad|upset|lonely|down)|bad day)\b"),
    ("sad", "jarvis", r"^sorry\b|^i (couldn'?t|can'?t)\b|didn'?t (work|take)|went wrong|\bfailed\b"),
    ("happy", "jarvis", r"\b(you'?re welcome|my pleasure|glad)\b"),
)
TALK_LINES = {("happy", "you"): "Anytime", ("happy", "jarvis"): "Happy to help", ("angry", "you"): "…noted",
              ("sad", "you"): "I'm here for you", ("sad", "jarvis"): "Sorry about that",
              ("stressed", "jarvis"): "Rough patch. I'm on it"}
ROTATE = 8.0  # seconds a lasting mood's line (music, a busy computer) takes turns with the plain one
EXTRA_SECS = 2 * ROTATE  # seconds each random extra after "Ready" stays
SCROLL, HOLD = 30.0, 1.2  # a line too long for the bar scrolls this many px a second, held this long at each end
GRAPH_W, GRAPH_H = 44, 22  # the busy computer's line at the compact bar's right end
RIGHT_SECS, SLIDE = 5.0, 0.6  # each thing at the compact bar's right end stays this long; the next slides in this fast
WORDS_HOLD = 1.2  # seconds your words stay up before the reply replaces them
SNOW = (1, 1, 1)
WALLPAPER = "org.gnome.desktop.background"
TIPS = {"ignored": "Speech JARVIS ignored. Click to see it in the chat", "alerts": "Unread alerts. Click to see them"}
DOCK = "imports.ui.main.uiGroup.get_children().filter(a=>a.name=='dashtodockContainer').forEach(a=>{%s})"
EASE = "duration:160,mode:imports.gi.Clutter.AnimationMode.EASE_OUT_QUAD"
# The shell's input area is switched too: it can make the dock visible again while faded, and then the dock's
# invisible icons would take the clicks meant for the bar.
CLICKS = ("let L=imports.ui.main.layoutManager;L._trackedActors.forEach(d=>{if(a.contains(d.actor)&&d.affectsStruts)"
          "d.affectsInputRegion=%s});L._queueUpdateRegions();")
HIDE_DOCK = DOCK % (CLICKS % "false" + "a.ease({opacity:0,%s,onComplete:()=>{a.visible=false}})" % EASE)
# _updateVisibility puts back the shell's own rule: no dock over a fullscreen window (a video, a game).
SHOW_DOCK = DOCK % (CLICKS % "true" + "a.visible=true;L._updateVisibility();a.ease({opacity:255,%s})" % EASE)


def rim_point(s, x, y, w, h):
    """The point a fraction `s` of the way round a box, clockwise from its top-left."""
    d = (s % 1) * 2 * (w + h)
    for dx, dy, side in ((1, 0, w), (0, 1, h), (-1, 0, w), (0, -1, h)):
        if d <= side:
            return x + dx * d, y + dy * d
        x, y, d = x + dx * side, y + dy * side, d - side
    return x, y


def cover(image_w, image_h, screen_w, screen_h):
    """A wallpaper set to "zoom": its scaled size, and where the screen starts inside that."""
    scale = max(screen_w / image_w, screen_h / image_h)
    w, h = max(screen_w, round(image_w * scale)), max(screen_h, round(image_h * scale))
    return w, h, (w - screen_w) // 2, (h - screen_h) // 2


def _layout(cr, text, size, width=None, keep_end=False, bold=False):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription("Sans %s %g" % ("Bold" if bold else "", size)))
    layout.set_text(text.replace("\n", " "), -1)
    if width:
        layout.set_width(int(max(width, 1) * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.START if keep_end else Pango.EllipsizeMode.END)
    return layout


def _breath(t):
    """0..1..0 once every 4 s, easing in and out like a breath."""
    return 0.5 - 0.5 * math.cos(t * math.pi / 2)


def _eyes(t):
    """The idle orb's eyes at time t: how open (0..1), where they look (-1 left .. 1 right), and whether they're
    on the move (blinking or glancing), which needs the fast frame rate."""
    p, glance = t % BLINK, t % 13
    shut = 0.0
    for at in (0.0, 0.32) if int(t // BLINK) % 3 == 0 else (0.0,):  # every third blink is a double one
        q = (p - at) / 0.16
        if 0 <= q < 1:
            shut = 1 - abs(2 * q - 1)
    side = 1 if int(t // 13) % 2 else -1  # towards the words, then away
    look = side * _smooth(min((glance - 7) / 0.3, (9 - glance) / 0.3))
    moving = p < 0.55 or p > BLINK - 0.12 or 6.9 < glance < 9.05
    return 1 - shut, look, moving


def mood_for(who, text):
    """The mood these words from "you" or "jarvis" bring on, or None."""
    return next((mood for mood, by, words in MOODS if by == who and re.search(words, text.lower())), None)


def greeting(hour):
    return ("Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 17 else
            "Good evening" if 17 <= hour < 22 else "")


def pick_extra(n, extras, hour):
    """What takes Ready's place in slot n: one at random (the same all through the slot) of the greeting, the
    weather, the next reminder or a thing to try; nothing only when there are none."""
    rng, tips = random.Random(n), extras.get("tips") or []
    some = [greeting(hour), extras.get("weather"), extras.get("next"), tips and "Try “%s”" % rng.choice(tips)]
    return rng.choice([x for x in some if x] or [""])


def _fade(at, span):
    return min(1.0, at / 0.3, (span - at) / 0.3)


def idle_line(mood, by, music, body, extras, t, mic_off):
    """(words, alpha) for the idle compact bar: one line, never crowded. A talk mood's line shows while it lasts; a
    lasting mood's (music, the computer) takes turns with the plain line every ROTATE s. The plain line is an extra
    (pick_extra), fading across when it changes; Ready (Mic off with the mic off) only when there are none."""
    if (mood, by) in TALK_LINES:
        return TALK_LINES[mood, by], 1.0
    cpu = "GPU %d%%" % body.gpu if body.gpu > body.cpu else "CPU %d%%" % body.cpu
    line = {"dance": "♪ " + (music.track or "%d bpm" % music.bpm), "sway": "♪ " + (music.track or "Easy listening"),
            "hot": "Running hot · %d°C" % body.hottest, "straining": "Working hard · " + cpu, "busy": "Busy · " + cpu,
            "squeezed": "Low on memory · %d%% free" % (body.free * 100), "sleepy": "It's late"}.get(mood)
    at = t % EXTRA_SECS
    if line and at < ROTATE:
        return line, _fade(at, ROTATE)
    n, hour = int(t // EXTRA_SECS), time.localtime(t).tm_hour
    extra = pick_extra(n, extras, hour)
    words = extra or ("Mic off" if mic_off else "Ready")
    if line:
        return words, _fade(at - ROTATE, ROTATE)
    return words, min(1.0, at / 0.3 if pick_extra(n - 1, extras, hour) != extra else 1.0,
                      (EXTRA_SECS - at) / 0.3 if pick_extra(n + 1, extras, hour) != extra else 1.0)


def _mute_mark(cr, x, y, kind):
    """A small "mic" or "speaker" with a slash through it, centred on (x, y)."""
    cr.push_group()
    cr.set_source_rgba(*SNOW, 1)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(1.5)
    if kind == "mic":
        _round_rect(cr, x - 2.5, y - 7.5, 5, 10, 2.5)
        cr.fill()
        cr.arc(x, y - 2, 5, 0, math.pi)  # the cup round its bottom (cairo's angles turn downwards)
        cr.stroke()
        cr.move_to(x, y + 3)
        cr.line_to(x, y + 6.5)
        cr.stroke()
    else:
        for px, py in ((-6, -2.5), (-3, -2.5), (2.5, -7), (2.5, 7), (-3, 2.5), (-6, 2.5)):
            cr.line_to(x + px, y + py)
        cr.close_path()
        cr.fill()
    cr.move_to(x - 7, y - 7)
    cr.line_to(x + 7, y + 7)
    cr.set_operator(cairo.OPERATOR_CLEAR)  # a gap either side of the slash, so it reads over the shape
    cr.set_line_width(4.5)
    cr.stroke_preserve()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.set_line_width(1.6)
    cr.stroke()
    cr.pop_group_to_source()
    cr.paint_with_alpha(0.85)
    cr.set_line_cap(cairo.LINE_CAP_BUTT)


def look_at(px, py, ox, oy):
    """Where eyes at (ox, oy) look to see the pointer at (px, py): a direction, shorter when it's close."""
    dx, dy = px - ox, py - oy
    d = math.hypot(dx, dy) or 1.0
    reach = min(1.0, d / 250)
    return dx / d * reach, dy / d * reach


def spring(pos, vel, aim, dt, w=GAZE_W):
    """One step of a critically damped spring (exact, so smooth at any frame rate): new positions and speeds."""
    e = math.exp(-w * dt)
    out_p, out_v = [], []
    for p, v, a in zip(pos, vel, aim):
        x = p - a
        out_p.append(a + (x + (v + w * x) * dt) * e)
        out_v.append((v - w * (v + w * x) * dt) * e)
    return out_p, out_v


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _show(cr, layout, x, mid, alpha):
    h = layout.get_pixel_size()[1]
    cr.set_source_rgba(*SNOW, alpha)
    cr.move_to(x, mid - h / 2)
    PangoCairo.show_layout(cr, layout)
    cr.new_path()


class Bar:
    """GTK-thread only; ui.UI wraps it with thread-safe calls."""

    hint, _hint_end = "", 0.0  # why it can't hear you now ("say Jarvis first"), for a few seconds
    _face_now = _face_aim = 1.0  # the face: 1 shown, 0 gone to a card (class defaults: a bare Bar has them too)
    _face_t = 0.0
    alerts = 0  # JARVIS's own unread alerts (ui/extras.py, via set_extras): a button on the full bar

    def __init__(self, ui, show_understood):
        self.ui = ui
        self.show_understood = show_understood
        self.mode = "hidden"
        self.hover = None  # which part the pointer is on: a key of _zones(), or "bar"
        self.words = self.understood = self.reply = ""
        self.ignored = 0  # ignored since the chat was last opened
        self._text_at = self._words_at = 0.0
        self._follow_end = self._follow_total = 0.0
        self._w, self._x, self._vis = float(FULL_W), 0.0, 0.0
        self._placed = None
        self._timer = None
        self.gaze = [0.0, 0.0]  # where the idle eyes look: -1..1 across and down
        self._pointer = self._last_pointer = None
        self._face_now, self._face_aim, self._face_t = 1.0, 1.0, 0.0  # the face: 1 shown, 0 gone to a card
        self._far = {"spot": None, "moved": 0.0, "t": 0.0, "pos": [0.0, 0.0], "vel": [0.0, 0.0]}  # gaze_from's eyes
        self._pointer_at = self._gaze_t = 0.0
        self._gaze_v = [0.0, 0.0]  # the eyes' speed, for the spring
        self._watch_k = 0.0  # 0..1, eased: how much the eyes are on the pointer (a mood's own darting gives way)
        self.mood, self.feeling = None, (None, 0.0)  # the mood waiting to be shown, and (mood, strength 0..1) now
        self._mood_seen, self._sorry, self.mood_by = None, [], None  # mood_by: who said what brought it on
        self.music, self.body = Music(), Body()  # the moods music and the computer bring on
        self._ambient, self._ambient_k, self._ambient_t, self.beat = None, 0.0, 0.0, 0.0
        self.style, self._style_until, self._style_beat = None, 0.0, 0  # the dance or sway move, and till when
        self._old_style, self._style_at = None, 0.0  # the move before it and when it changed: they cross-fade
        self.layers, self._sleep_k, self._blend_t = [], 0.0, 0.0  # the looks the eyes show; how far asleep
        self._glow_now, self._drawn_t = None, 0.0  # the glow eases to each state's strength
        self._seen_state, self._prev_state, self._state_at = "idle", None, 0.0  # for fading the rim comet
        GLib.timeout_add_seconds(5, self.body.check)
        self._every = None  # the timer's ms: 33 while busy, moving or blinking, BREATH while idle, None when stopped
        self._settle = 0.0
        self._scroll, self._scrolling = (None, 0.0), False  # the idle line too long to fit: (it, since when)
        self._cached = None
        self._glow = self._glass = None
        self._measure = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))  # sizes text off screen
        self._wall = Gio.Settings.new(WALLPAPER) if Gio.SettingsSchemaSource.get_default().lookup(WALLPAPER, True) else None
        self._wall_uri = self._blurred = None
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self._bus = None
        self.show_dock(wait=True)  # in case the last run died with it hidden

        w = self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        _make_transparent(w)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_type_hint(Gdk.WindowTypeHint.DOCK)
        w.set_default_size(FULL_W, HEIGHT)
        w.stick()
        w.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK
                     | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        w.connect("draw", self._draw)
        w.connect("button-press-event", self._press)
        w.connect("motion-notify-event", self._moved)
        w.connect("leave-notify-event", self._left)
        w.connect("realize", lambda _w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "pointer")))

    # --- what it says ---

    def heard(self, text):
        if text:
            self._feel("you", text)
            self.words, self.understood, self.reply = text, "", ""
            self._words_at = time.time()
            self._changed()

    def said(self, who, text):
        if who == "you":
            self.heard(text)
        elif who in ("jarvis", "claude"):
            self._feel("jarvis", text)
            self.reply = text
            self._changed()

    def _feel(self, who, text):
        """Pick the eyes' next mood from what was said. What you say sets it afresh; JARVIS's words only add one."""
        mood = mood_for(who, text)
        if mood == "sad" and who == "jarvis":
            now = time.time()
            self._sorry = [s for s in self._sorry if now - s < 120] + [now]
            mood = "stressed" if len(self._sorry) >= 2 else mood
        if who == "you" or mood:
            self.mood, self._mood_seen, self.mood_by = mood, None, who

    def _mood_now(self, t):
        """(mood, strength): the mood shows for MOOD_SECS once the eyes are up, fading in and out."""
        if not self.mood or self._state() != "idle" or self.mode == "hidden":
            return None, 0.0
        self._mood_seen = self._mood_seen or t
        age = t - self._mood_seen
        if age > MOOD_SECS:
            self.mood = None
            return None, 0.0
        return self.mood, _smooth(min(age / 0.35, (MOOD_SECS - age) / 0.5))

    def _ambience(self, t):
        """(mood, strength) from the music, else the computer: the old one fades out before the new one fades in."""
        aim = self.music.kind or self.body.mood
        dt, self._ambient_t = min(0.2, t - (self._ambient_t or t)), t
        if aim != self._ambient:
            self._ambient_k -= dt / 0.4
            if self._ambient_k <= 0:
                self._ambient, self._ambient_k = aim, 0.0
        elif aim:
            self._ambient_k = min(1.0, self._ambient_k + dt / 0.6)
        if self._ambient in ("dance", "sway"):  # beats since the last one heard: the eyes move on them
            self.beat = (t - self.music.beat_at) * self.music.bpm / 60 if self.music.bpm else t / 2
            moves = DANCES if self._ambient == "dance" else SWAYS
            # A new move when the music starts, else once its time is up, right on a beat so the change is in time.
            if self.style not in moves or t >= self._style_until and int(self.beat) != self._style_beat:
                # The new move fades in over the old one; a first move fades in with the music itself.
                self._old_style, self._style_at = (self.style if self.style in moves else None), t
                self.style = random.choice([m for m in moves if m != self.style] or moves)
                self._style_until = t + random.uniform(*STYLE_SECS)
            self._style_beat = int(self.beat)
        return self._ambient, _smooth(self._ambient_k)

    def _blend(self, t, talk, around, asleep):
        """The looks the eyes show now, [(mood, strength, style)], so every change is a fade: a new dance move over
        the old one, a talk mood taking over from the music or the computer's mood and handing it back, and the eyes
        closing when asked to sleep."""
        dt, self._blend_t = min(0.2, t - (self._blend_t or t)), t
        self._sleep_k = min(1.0, self._sleep_k + dt / 0.5) if asleep else max(0.0, self._sleep_k - dt / 0.5)
        layers, (mood, k) = [], around
        if mood:
            k *= 1 - talk[1]
            w = _smooth((t - self._style_at) / 0.6) if self._old_style and mood in ("dance", "sway") else 1.0
            if w < 1:
                layers.append((mood, k * (1 - w), self._old_style))
            layers.append((mood, k * w, self.style))
        if talk[0]:
            layers.append((talk[0], talk[1], None))
        awake = 1 - _smooth(self._sleep_k)
        layers = [(m, s * awake, style) for m, s, style in layers]
        if self._sleep_k > 0:
            layers.append(("asleep", _smooth(self._sleep_k), None))
        return layers

    def gaze_from(self, ox, oy):
        """The idle eyes' gaze for an orb drawn at (ox, oy) on screen (the dashboard's): towards the pointer while it
        moves and a moment after, as the bar's eyes do but from there, else the bar's own glances. Drowsy moods
        follow slowly and part of the way, as on the bar."""
        t, g = time.time(), self._far
        try:
            self._pointer = self._pointer or Gdk.Display.get_default().get_default_seat().get_pointer()
            spot = tuple(self._pointer.get_position()[1:])
        except (AttributeError, TypeError):
            spot = None
        if spot != g["spot"]:
            g["moved"], g["spot"] = (t if g["spot"] is not None else g["moved"]), spot
        watching = spot is not None and t - g["moved"] < FOLLOW and self._ambient not in ("dance", "sway")
        lazy = self.feeling[1] if self.feeling[0] in LAZY else 0.0
        reach = 1 - (1 - LAZY_REACH) * lazy
        aim = [a * reach for a in look_at(*spot, ox, oy)] if watching else list(self.gaze)
        g["pos"], g["vel"] = spring(g["pos"], g["vel"], aim, min(0.25, t - (g["t"] or t)),
                                    GAZE_W + (LAZY_W - GAZE_W) * lazy)
        g["t"] = t
        return g["pos"]

    def _watch(self, t):
        """Turn the idle eyes towards the pointer while it moves and a moment after, else ahead with the odd
        glance. The frame rate that needs: 60 while the pointer moves, 30 while the eyes settle, else 0."""
        try:
            self._pointer = self._pointer or Gdk.Display.get_default().get_default_seat().get_pointer()
            spot = tuple(self._pointer.get_position()[1:])
        except (AttributeError, TypeError):
            spot = None
        if spot != self._last_pointer:
            if self._last_pointer is not None:
                self._pointer_at = t
            self._last_pointer = spot
        music = self._ambient in ("dance", "sway")  # the music has the eyes: they dance and don't chase the pointer
        watching = spot is not None and t - self._pointer_at < FOLLOW and not music
        if watching:
            aim = look_at(*spot, self._x + PAD + PILL / 2, self.top() + PAD + PILL / 2)
        else:
            aim = (0.0, 0.0) if music else (_eyes(t)[1], 0.0)
        lazy = self.feeling[1] if self.feeling[0] in LAZY else 0.0
        reach, dt = 1 - (1 - LAZY_REACH) * lazy, min(0.25, t - (self._gaze_t or t))
        self.gaze, self._gaze_v = spring(self.gaze, self._gaze_v, [a * reach for a in aim], dt,
                                         GAZE_W + (LAZY_W - GAZE_W) * lazy)
        self._watch_k += ((1.0 if watching else 0.0) - self._watch_k) * (1 - math.exp(-dt / 0.25))
        self._gaze_t = t
        if watching and t - self._pointer_at < 0.3:
            return 60
        return 30 if max(map(abs, self._gaze_v)) > 0.05 or watching or t - self._pointer_at < FOLLOW + 0.8 else 0

    def set_understood(self, text):
        self.understood = text
        self._changed()

    def show_hint(self, text, seconds):
        self.hint, self._hint_end = text, time.time() + seconds
        self.poke()

    def add_ignored(self):
        self.ignored += 1
        self.poke()

    def follow(self, seconds):
        """Listening without the name for `seconds`: a line drains along the bar."""
        now = time.time()
        if now >= self._follow_end:
            self._follow_total = seconds
        self._follow_end = now + seconds
        self.poke()

    def _changed(self):
        self._text_at = time.time()
        self.poke()

    # --- showing and hiding ---

    def top(self):
        """The screen height the chat and cards sit on, just above the bar's glow."""
        geo = self._screen()
        return geo.y + geo.height - (STRIP + HEIGHT) // 2

    @staticmethod
    def _screen():
        return Gdk.Display.get_default().get_monitor(0).get_geometry()

    def _target(self, mode):
        geo = self._screen()
        if mode == "full":
            w = self._fit()
            return geo.x + (geo.width - w) / 2, w
        return geo.x + geo.width - COMPACT_W, COMPACT_W

    def _fit(self):
        """The full bar's width: just room for what it shows now, so it grows as you talk, up to FULL_W."""
        state = self._state()
        if self._reply_up(time.time()):
            room = self._text_w(self.reply, TEXT)
        else:
            words = self.words or self._waiting(state)
            note = self._text_w(self._note(state), TEXT - 1)
            room = max((self._text_w(words, TEXT) + 12 if words else 0) + note, 2 * note)  # _row gives a note half
        buttons = FULL_W - min(a for a, _b in self._zones(FULL_W).values())
        return min(FULL_W, PAD + PILL + 16 + room + 2 + buttons)  # the face's room too: the bar's size never jumps

    def _text_w(self, text, size):
        return _layout(self._measure, text, size).get_pixel_size()[0] if text else 0

    def set_mode(self, mode):
        if mode == self.mode:
            return
        was, self.mode = self.mode, mode
        if mode == "full":
            self._dock(HIDE_DOCK)
        elif was == "full":
            self.show_dock()
        if "compact" in (mode, was):
            self.ui.card._place()  # the chat and cards follow the bar to the right and back
        if not self.win.get_visible():
            self._x, self._w = self._target(mode)
            self._vis = 0.0
            self._apply()
            self.win.show_all()
        self.poke()

    def show_dock(self, wait=False):
        self._dock(SHOW_DOCK, wait)

    def _dock(self, script, wait=False):
        if not self._bus:
            return
        args = ("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell", "Eval", GLib.Variant("(s)", (script,)), None,
                Gio.DBusCallFlags.NONE, 1000, None)
        try:
            self._bus.call_sync(*args) if wait else self._bus.call(*args, None, None)
        except GLib.Error:
            pass  # no GNOME Shell to ask: the bar still works, over whatever is there

    def poke(self):
        """Something changed: redraw, and keep animating for a moment."""
        self._settle = time.time() + 0.4
        if self.win.get_visible():
            self.win.queue_draw()
        if self._every not in (16, 33):  # already fast enough: leave it
            if self._timer:
                GLib.source_remove(self._timer)
            self._timer, self._every = GLib.timeout_add(33, self._tick), 33

    def _state(self):
        return self.ui._shown[0] if self.ui._shown else "idle"

    def _tick(self):
        moving = self._step()
        # A reply comes up WORDS_HOLD after your words, and the bar's width follows it.
        t = time.time()
        busy = moving or t < max(self._settle, self._words_at + WORDS_HOLD + 0.4) or (self.mode != "hidden" and self._state() in ANIMATED)
        busy = busy or self.mode == "compact" and (t % RIGHT_SECS < SLIDE + 0.1 or self._scrolling)  # sliding, scrolling
        if self.win.get_visible():
            self.win.queue_draw()
        state = self._state()
        idle = self.mode != "hidden" and state == "idle"
        self.music.want = idle  # a mute doesn't touch the eyes: they stay awake and animated
        self.feeling, lively, fps = (None, 0.0), False, 0  # no feeling while the eyes are busy: no old mood shows
        if idle:  # the eyes: they blink, watch the pointer and show moods (a talk mood over the rest)
            talk, around = self._mood_now(t), self._ambience(t)
            self.feeling = talk if talk[0] else around
            self.layers = self._blend(t, talk, around, False)
            fps = self._watch(t)
            if self._watch_k > 0.01:  # not a mood: tells the look to hold its own darting still
                self.layers = self.layers + [("watching", self._watch_k, None)]
            lively = (fps or _eyes(t)[2] or bool(self.mood) or self._ambient in MOVING
                      or 0 < self._ambient_k < 1 or 0 < self._sleep_k < 1 or t - self._style_at < 0.7)
        # Asleep or voice off, the eyes still breathe: the slow frame rate.
        every = 16 if lively and fps == 60 else 33 if busy or lively else BREATH if idle or state == "off" else None
        if every == self._every:
            return True
        self._timer, self._every = (GLib.timeout_add(every, self._tick) if every else None), every
        return False

    def _step(self):
        """One frame towards where the bar should be. True while it's still on its way."""
        moving = False

        def ease(now, aim, closest):
            nonlocal moving
            if abs(aim - now) <= closest:
                return aim
            moving = True
            return now + (aim - now) * 0.32

        if self.mode == "hidden":
            self._vis = ease(self._vis, 0.0, 0.03)
            if not moving and self.win.get_visible():
                self.win.hide()
                self.hover = None
                self.words = self.understood = self.reply = ""
                return False
        else:
            aim_x, aim_w = self._target(self.mode)
            self._x, self._w, self._vis = ease(self._x, aim_x, 1), ease(self._w, aim_w, 1), ease(self._vis, 1.0, 0.03)
        self._apply()
        return moving

    def _apply(self):
        spot = (int(self._x), int(self._w), round((1 - self._vis) * 14))
        self.win.set_opacity(self._vis)
        if spot != self._placed:
            self._placed = spot
            geo = self._screen()
            self.win.resize(spot[1], HEIGHT)
            self.win.move(spot[0], geo.y + geo.height - (STRIP + HEIGHT) // 2 + spot[2])

    # --- the pointer ---

    def _zones(self, width):
        """The clickable parts on the right of the full bar, name -> (x from, x to)."""
        if self.mode != "full":
            return {}
        x, out = width - PAD - 14, {}
        out["chat"], x = (x - 26, x), x - 28
        if self.ignored:
            out["ignored"], x = (x - 42, x), x - 44
        if self.alerts:
            out["alerts"], x = (x - 42, x), x - 44
        if self.words or self.understood:  # nothing to show yet: no button for it
            out["understood"] = (x - 28, x)
        return out

    def _zone(self, x):
        return next((name for name, (a, b) in self._zones(self.win.get_allocated_width()).items() if a <= x < b), "bar")

    def _press(self, _w, ev):
        if ev.button == 3:
            self.ui.menu.popup_at_pointer(ev)
        elif ev.button == 1:
            if self._zone(ev.x) == "understood":
                self.ui._set_understood(None)
            elif self._zone(ev.x) == "alerts":
                self.ui.on_command("show my alerts")
            else:
                self.ui._set_chat(None)
        return True

    def _moved(self, _w, ev):
        zone = self._zone(ev.x)
        if zone != self.hover:
            self.hover = zone
            if zone == "understood":
                tip = "Click to %s what JARVIS understood" % ("hide" if self.show_understood else "show")
            elif zone == "chat":
                tip = "Close the chat" if self.ui.panel.get_visible() else "Open the chat"
            else:
                tip = TIPS.get(zone)  # the plain bar has none
            self.win.set_tooltip_text(tip)
            self.poke()
        return False

    def _left(self, _w, _ev):
        self.hover = None
        self.poke()
        return False

    # --- drawing ---

    def _backdrop(self):
        """The bottom of the wallpaper, screen-wide and blurred. It's what's behind the bar, as no window covers the
        dock's strip; GTK can't blur what's really behind a window on this desktop."""
        uri = self._wall.get_string("picture-uri") if self._wall else None
        if uri == self._wall_uri:
            return self._blurred
        self._wall_uri, self._blurred = uri, None
        geo = self._screen()
        try:
            image = GdkPixbuf.Pixbuf.new_from_file(Gio.File.new_for_uri(uri).get_path())
        except (GLib.Error, TypeError):
            return None  # a plain colour, or the file is gone: the glass is just see-through then
        # shortcut: assumes the wallpaper is set to "zoom" (it is here); other placements blur the wrong part.
        w, h, x, y = cover(image.get_width(), image.get_height(), geo.width, geo.height)
        strip = image.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR).new_subpixbuf(
            x, y + geo.height - HEIGHT, geo.width, HEIGHT)
        tiny = strip.scale_simple(max(geo.width // 18, 1), max(HEIGHT // 18, 2), GdkPixbuf.InterpType.BILINEAR)
        self._blurred = tiny.scale_simple(geo.width, HEIGHT, GdkPixbuf.InterpType.BILINEAR)
        return self._blurred

    def _layers(self, w):
        """The glow and the glass for this place on the screen, drawn once: each frame only fades the glow."""
        spot = (int(self._x), w)
        if spot == self._cached:
            return
        self._cached = spot
        self._glow = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, HEIGHT)
        cr = cairo.Context(self._glow)
        for i in range(PAD, 0, -1):
            _round_rect(cr, PAD, PAD, w - 2 * PAD, PILL, PILL / 2)
            cr.set_source_rgba(*SNOW, 0.018 + 0.12 * (1 - i / PAD) ** 1.8)
            cr.set_line_width(i * 2)
            cr.stroke()

        self._glass = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, HEIGHT)
        cr = cairo.Context(self._glass)
        behind = self._backdrop()
        if behind:
            cr.save()
            _round_rect(cr, PAD, PAD, w - 2 * PAD, PILL, PILL / 2)
            cr.clip()
            Gdk.cairo_set_source_pixbuf(cr, behind, -spot[0], 0)
            cr.paint()
            cr.restore()
        _round_rect(cr, PAD, PAD, w - 2 * PAD, PILL, PILL / 2)
        cr.set_source_rgba(*((0.12, 0.13, 0.15, 0.9) if getattr(self.ui, "look", "glass") == "solid" else (1, 1, 1, 0.04)))
        cr.fill()
        sheen = cairo.LinearGradient(0, PAD, 0, PAD + PILL * 0.55)
        sheen.add_color_stop_rgba(0, *SNOW, 0.09)
        sheen.add_color_stop_rgba(1, *SNOW, 0)
        _round_rect(cr, PAD + 1, PAD + 1, w - 2 * PAD - 2, PILL * 0.55, PILL * 0.27)
        cr.set_source(sheen)
        cr.fill()
        _round_rect(cr, PAD + 0.6, PAD + 0.6, w - 2 * PAD - 1.2, PILL - 1.2, PILL / 2 - 0.6)
        rim = cairo.LinearGradient(0, PAD, 0, PAD + PILL)
        rim.add_color_stop_rgba(0, *SNOW, 0.42)
        rim.add_color_stop_rgba(1, *SNOW, 0.18)
        cr.set_source(rim)
        cr.set_line_width(1.2)
        cr.stroke()

    def _strength(self, state, t):
        if state == "listening":
            strength = 0.5 + 0.5 * min(1.0, self.ui._voice * 1.6)  # the bar breathes with your voice
        elif state == "speaking":
            strength = 0.62 + 0.3 * abs(math.sin(t * 5))
        elif state in ("thinking", "claude"):
            strength = 0.55 + 0.15 * math.sin(t * 3)
        elif state == "idle":
            strength = 0.42 + 0.1 * _breath(t)
        else:
            strength = 0.45
        return min(1.0, strength + (0.2 if self.hover else 0))

    def _sweep(self, cr, w, state, t, fade=1.0):
        """Thinking: a bright comet running round the rim (Claude's is orange)."""
        x, y = rim_point(t * 0.42, PAD, PAD, w - 2 * PAD, PILL)
        r, g, b = CLAUDE_RGB if state == "claude" else SNOW
        spot = cairo.RadialGradient(x, y, 0, x, y, 100)
        spot.add_color_stop_rgba(0, r, g, b, 1)
        spot.add_color_stop_rgba(1, r, g, b, 0)
        for width, alpha in ((7, 0.25), (1.8, 1.0)):
            _round_rect(cr, PAD + 0.6, PAD + 0.6, w - 2 * PAD - 1.2, PILL - 1.2, PILL / 2 - 0.6)
            cr.set_line_width(width)
            cr.push_group()
            cr.set_source(spot)
            cr.stroke()
            cr.pop_group_to_source()
            cr.paint_with_alpha(alpha * fade)

    def _draw(self, _win, cr):
        _clear(cr)
        w, t, state = _win.get_allocated_width(), time.time(), self._state()
        self._layers(w)
        # The glow eases to the new state's strength, and the rim comet fades in and out, rather than jumping.
        dt, self._drawn_t = min(0.1, t - (self._drawn_t or t)), t
        aim = self._strength(state, t)
        self._glow_now = aim if self._glow_now is None else self._glow_now + (aim - self._glow_now) * min(1.0, dt / 0.15)
        cr.set_source_surface(self._glow, 0, 0)
        cr.paint_with_alpha(self._glow_now)
        cr.set_source_surface(self._glass, 0, 0)
        cr.paint()
        if state != self._seen_state:
            self._prev_state, self._seen_state, self._state_at = self._seen_state, state, t
        p = min(1.0, (t - self._state_at) / 0.3)
        if state in ("thinking", "claude"):
            self._sweep(cr, w, state, t, p)
        elif self._prev_state in ("thinking", "claude") and p < 1:
            self._sweep(cr, w, self._prev_state, t, 1 - p)
        k = self._face_k(t)
        if k > 0.01:  # shrinking and fading as it goes to a card, growing back as it returns
            cr.save()
            cr.translate(PAD + PILL / 2, PAD + PILL / 2)
            cr.scale(0.6 + 0.4 * k, 0.6 + 0.4 * k)
            cr.translate(-PILL / 2, -PILL / 2)
            cr.push_group()
            self.ui._paint_orb(cr, PILL, PILL)
            cr.pop_group_to_source()
            cr.paint_with_alpha(k)
            cr.restore()
        zones = self._zones(w)
        self._paint_talk(cr, w, state, t, zones)
        self._paint_zones(cr, zones)
        if state == "listening" and t < self._follow_end and self.mode == "full":
            self._paint_follow(cr, w, t)
        return False

    def _paint_follow(self, cr, w, t):
        """The follow-up timer: a glowing line draining to the middle, sparks running out along it. It brightens
        while you speak and beats faster in its last quarter."""
        part = min(1.0, (self._follow_end - t) / (self._follow_total or 1))
        half = (w - 2 * PAD - 56) / 2 * part
        if half < 1:
            return
        c, y = w / 2, HEIGHT - PAD - 3
        beat = 0.8 + 0.2 * math.sin(t * (4 if part > 0.25 else 11))
        lift = 0.75 + 0.25 * min(1.0, self.ui._voice * 1.6)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for width, alpha in ((8, 0.12), (3.5, 0.3), (1.6, 1.0)):  # glow, halo, core
            line = cairo.LinearGradient(c - half, 0, c + half, 0)
            for at, a in ((0, 0), (0.18, 0.55), (0.5, 1), (0.82, 0.55), (1, 0)):  # tapered ends
                line.add_color_stop_rgba(at, *SNOW, a * alpha * beat * lift)
            cr.set_source(line)
            cr.set_line_width(width)
            cr.move_to(c - half, y)
            cr.line_to(c + half, y)
            cr.stroke()
        for phase in (0.0, 0.5):  # two sparks a side, born in the middle, fading as they reach the ends
            s = (t * 0.7 + phase) % 1
            for side in (-1, 1):
                x = c + side * half * s
                spark = cairo.RadialGradient(x, y, 0, x, y, 7)
                spark.add_color_stop_rgba(0, *SNOW, 0.95 * (1 - s) * lift)
                spark.add_color_stop_rgba(1, *SNOW, 0)
                cr.arc(x, y, 7, 0, 2 * math.pi)
                cr.set_source(spark)
                cr.fill()
        cr.set_line_cap(cairo.LINE_CAP_BUTT)

    def _row(self, cr, x, mid, room, words, note, size, alpha):
        """What you said (keeping its end if it's long) and, right after it, the fainter note."""
        layout = _layout(cr, note, size - 1, room * 0.5) if note else None
        note_w = layout.get_pixel_size()[0] + 12 if layout else 0
        used = 0
        if words:
            said = _layout(cr, words, size, room - note_w, keep_end=True)
            used = said.get_pixel_size()[0] + 12
            _show(cr, said, x, mid, alpha)
        if layout:
            _show(cr, layout, x + used, mid, alpha * 0.62)

    def _face(self):
        """Does the bar show the orb? Not while a card does (the dashboard's, "face" in ui/card.py): one face at a
        time, whatever the bar's mode."""
        deck = getattr(self.ui, "card", None)  # the Deck is made after the bar
        return not (deck and deck.face_up())

    def _face_k(self, t):
        """How much of the face shows, 0..1 eased: it goes over FACE_FADE when a card takes it, and comes back."""
        aim = 1.0 if self._face() else 0.0
        if aim != self._face_aim:
            self._face_aim = aim
            self.poke()  # smooth frames for the fade, not the idle 10 a second
        step = min(0.1, t - (self._face_t or t)) / FACE_FADE
        self._face_t = t
        self._face_now += max(-step, min(step, aim - self._face_now))
        return _smooth(self._face_now)

    def _words_x(self):
        """Where the bar's words start: after the face, or near the pill's end without it, sliding as it fades."""
        return PAD + FACELESS + (PILL + 8 - FACELESS) * _smooth(self._face_now)

    def _paint_talk(self, cr, w, state, t, zones):
        left = self._words_x()
        if self.mode == "compact":
            text, alpha = self._compact_text(state, t)
            tip = text.startswith("Try “")  # a thing to try gets the whole bar: the right end steps aside
            room = (w - PAD - 16 if tip else self._paint_right(cr, w, t)) - left
            if tip and alpha < 1:
                self._paint_right(cr, w, t, 1 - alpha)  # fading out as the tip fades in, and back
            self._paint_progress(cr, w, t)
            if state == "idle":
                self._paint_line(cr, text, left, room, t, 0.92 * alpha)
                return
            _show(cr, _layout(cr, text, TEXT - 1, room, keep_end=state == "thinking"), left, HEIGHT / 2, 0.92 * alpha)
            return
        room = min([a for a, _b in zones.values()] or [w - PAD - 20]) - left - 8
        note = self._note(state)
        mid = HEIGHT / 2
        if self._reply_up(t):
            fade = min(1.0, (t - max(self._text_at, self._words_at + WORDS_HOLD)) / 0.2)
            _show(cr, _layout(cr, self.reply, TEXT, room), left, mid, 0.96 * fade)
        elif self.words:
            self._row(cr, left, mid, room, self.words, note, TEXT, 0.98 * min(1.0, (t - self._words_at) / 0.2))
        else:
            self._row(cr, left, mid, room, self._waiting(state), note, TEXT, 0.6 if state == "listening" else 0.42)

    def _waiting(self, state):
        """The words in the bar before you've said anything."""
        if state == "listening":
            return "Listening…"
        if state == "idle" and time.time() < self._hint_end:
            return self.hint
        if state in ("idle", "off"):
            return "Mic off" if self.ui.mutes["mic"] else "Voice off" if state == "off" else "Say “Jarvis”"
        return ""

    def _note(self, state):
        if state == "claude":
            return "Asking Claude…"
        return "→ " + self.understood if self.understood and self.show_understood else ""

    def _reply_up(self, t):
        """A reply waits a moment after your words, or a quick one would wipe them before you could read them."""
        return bool(self.reply) and t >= self._words_at + WORDS_HOLD

    def _compact_text(self, state, t):
        """(words, alpha) for the compact bar."""
        spent = int(self.ui.long_claude())
        if spent:
            return "Claude is working  %d:%02d" % (spent // 60, spent % 60), 1.0
        if state == "idle" and t < self._hint_end:
            return self.hint, 1.0
        if state == "idle":
            mood, k = self.feeling
            if mood in ("dance", "sway") and self._song_on_card():
                mood = None  # the card says what's playing: the bar says something else
            return idle_line(mood if k > 0.5 else None, self.mood_by, self.music, self.body, self.ui.extras, t,
                             self.ui.mutes["mic"])
        return {"thinking": self.words, "speaking": self.reply}.get(state) or LABELS.get(state, state), 1.0

    def _right_items(self, cr, t):
        """What the compact bar's right end can show now, in turn: [(width, draw(right edge x))]. A timer's draining
        ring and time left, bars on the music's beat, the CPU's last two minutes while it's busy or hot, the
        notifications in GNOME's list, the mic off, JARVIS's voice or the speaker off, and the time."""
        mid, items, mutes = HEIGHT / 2, [], self.ui.mutes
        mood = self.feeling[0] if self.feeling[1] > 0.5 else None
        left, total = min(((end - t, total) for _what, end, total in list(self.ui.timers.values()) if end > t),
                          default=(0, 0))

        def text(words, alpha=0.85):
            layout = _layout(cr, words, 9.5)
            return layout.get_pixel_size()[0], lambda right: _show(cr, layout, right - layout.get_pixel_size()[0],
                                                                    mid, alpha)
        if left:
            tw, words = text("%d:%02d" % (left // 60, left % 60))

            def timer(right):
                words(right)
                cx = right - tw - 11
                cr.set_line_width(2)
                cr.set_source_rgba(*SNOW, 0.2)
                cr.arc(cx, mid, 6, 0, 2 * math.pi)
                cr.stroke()
                cr.set_source_rgba(*SNOW, 0.9)
                cr.arc(cx, mid, 6, -math.pi / 2, -math.pi / 2 + 2 * math.pi * left / max(total, 1))
                cr.stroke()
            items.append((tw + 17, timer))
        if mood in ("dance", "sway"):
            def bars(right):
                cr.set_line_width(3)
                cr.set_source_rgba(*SNOW, 0.85)
                for i in range(4):
                    if mood == "dance":  # each bar jumps on the beat by its own amount, a new mix every beat
                        jump = (1 - self.beat % 1) ** 2 * (0.55 + 0.45 * math.sin(i * 1.7 + math.floor(self.beat) * 2.3))
                    else:  # slow music: a gentle rise and fall
                        jump = 0.45 + 0.35 * math.sin(math.pi * self.beat / 2 + i * 1.1)
                    x = right - 1.5 - (3 - i) * 5.5
                    cr.move_to(x, mid + 6)
                    cr.line_to(x, mid + 6 - 2 - 10 * max(0.0, jump))
                    cr.stroke()
            items.append((20, bars))
        history = self.body.history
        if mood in ("busy", "straining", "hot") and len(history) > 1:
            pw, percent = text("%d%%" % max(self.body.cpu, self.body.gpu))

            def graph(right):  # how busy now, and a line of the last two minutes filling its whole width
                percent(right)
                right -= pw + 6
                step, bottom = GRAPH_W / (len(history) - 1), mid + GRAPH_H / 2
                for i, v in enumerate(history):
                    cr.line_to(right - GRAPH_W + i * step, bottom - GRAPH_H * min(v, 100) / 100)
                cr.set_line_width(1.8)
                cr.set_source_rgba(*SNOW, 0.9)
                cr.stroke_preserve()
                cr.line_to(right, bottom)
                cr.line_to(right - GRAPH_W, bottom)
                cr.set_source_rgba(*SNOW, 0.18)
                cr.fill()
                cr.set_line_width(1)
                cr.set_source_rgba(*SNOW, 0.25)
                cr.move_to(right - GRAPH_W, bottom + 0.5)
                cr.line_to(right, bottom + 0.5)
                cr.stroke()
            items.append((pw + 6 + GRAPH_W, graph))
        unread = self.ui.extras.get("unread")
        if unread:
            nw, count = text(str(unread))

            def notes(right):  # an envelope and how many
                count(right)
                x = right - nw - 17
                cr.set_source_rgba(*SNOW, 0.8)
                cr.set_line_width(1.3)
                cr.rectangle(x, mid - 4.5, 13, 9)
                cr.move_to(x, mid - 4.5)
                cr.line_to(x + 6.5, mid + 0.5)
                cr.line_to(x + 13, mid - 4.5)
                cr.stroke()
            items.append((nw + 17, notes))
        if self.alerts:  # JARVIS's own unread alerts (the full bar's button opens them)
            aw, acount = text(str(self.alerts))

            def alerts(right):  # a dot, then how many: laid out like the envelope's
                acount(right)
                cr.set_source_rgba(*SNOW, 0.8)
                cr.arc(right - aw - 10.5, mid, 4.5, 0, 2 * math.pi)
                cr.fill()
            items.append((aw + 17, alerts))
        for kind, on in (("mic", mutes["mic"]), ("speaker", mutes["voice"] or mutes["speaker"])):
            if on:
                items.append((16, lambda right, kind=kind: _mute_mark(cr, right - 8, mid, kind)))
        items.append(text(time.strftime("%-I:%M %p", time.localtime(t)).lower(), 0.6))
        return items

    def _paint_line(self, cr, text, x, room, t, alpha):
        """The idle line, whole: one too long for `room` holds a moment, scrolls left to its end, holds, starts over."""
        layout, mid = _layout(cr, text, TEXT - 1), HEIGHT / 2
        over = layout.get_pixel_size()[0] - room
        self._scrolling = over > 0
        if not self._scrolling:
            _show(cr, layout, x, mid, alpha)
            return
        if self._scroll[0] != text:
            self._scroll = (text, t)
        at = (t - self._scroll[1]) % (over / SCROLL + 2 * HOLD)
        cr.save()
        cr.rectangle(x, 0, room, HEIGHT)
        cr.clip()
        _show(cr, layout, x - min(over, max(0.0, (at - HOLD) * SCROLL)), mid, alpha)
        cr.restore()

    def _paint_right(self, cr, w, t, fade=1.0):
        """The compact bar's right end: one thing at a time, each up for RIGHT_SECS, the next sliding up from below
        as the last slides out the top, all at `fade`. Returns where the words must stop: short of the widest, so they
        keep still."""
        items, right = self._right_items(cr, t), w - PAD - 16
        n, at = int(t // RIGHT_SECS), t % RIGHT_SECS
        k = _smooth(at / SLIDE) if len(items) > 1 else 1.0
        shown = [(items[n % len(items)], 12 * (1 - k), k)]
        if k < 1:
            shown.append((items[(n - 1) % len(items)], -12 * k, 1 - k))
        cr.save()
        _round_rect(cr, PAD, PAD, w - 2 * PAD, PILL, PILL / 2)  # sliding things vanish at the pill's edge
        cr.clip()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for (_width, draw), dy, alpha in shown:
            cr.push_group()
            cr.translate(0, dy)
            draw(right)
            cr.pop_group_to_source()
            cr.paint_with_alpha(alpha * fade)
        cr.restore()
        return right - max(width for width, _draw in items) - 10

    def _song_on_card(self):
        """The now-playing card (skills/media.py, id "playing") is open: it shows the song and how far in."""
        return self.ui.card_open("playing")

    def _paint_progress(self, cr, w, t):
        """While music with a known length plays: how far into the song, a thin line along the bottom of the pill."""
        m = self.music
        if not m.length or self.feeling[0] not in ("dance", "sway") or self._song_on_card():
            return
        part = max(0.0, min(1.0, (m.pos + t - m.pos_at) / m.length))
        x0, x1, y = self._words_x(), w - PAD - 22, PAD + PILL - 7
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(2)
        for to, alpha in ((x1, 0.12 * self.feeling[1]), (x0 + (x1 - x0) * part, 0.6 * self.feeling[1])):
            cr.set_source_rgba(*SNOW, alpha)
            cr.move_to(x0, y)
            cr.line_to(to, y)
            cr.stroke()
        cr.set_line_cap(cairo.LINE_CAP_BUTT)

    def _paint_zones(self, cr, zones):
        mid = HEIGHT / 2
        for name, (a, b) in zones.items():
            if self.hover == name:
                _round_rect(cr, a, mid - 13, b - a, 26, 10)
                cr.set_source_rgba(*SNOW, 0.16)
                cr.fill()
            centre = (a + b) / 2
            if name == "chat":
                up = -1 if self.ui.panel.get_visible() else 1  # up to open, down to close
                cr.set_source_rgba(*SNOW, 0.85)
                cr.set_line_width(1.8)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.move_to(centre - 6, mid + up * 3)
                cr.line_to(centre, mid - up * 3)
                cr.line_to(centre + 6, mid + up * 3)
                cr.stroke()
                continue
            text = "%d ⊘" % self.ignored if name == "ignored" else "%d ●" % self.alerts \
                if name == "alerts" else "→"
            alpha = 0.8 if name in ("ignored", "alerts") or self.show_understood else 0.28
            layout = _layout(cr, text, 9.5, bold=name == "understood")
            _show(cr, layout, centre - layout.get_pixel_size()[0] / 2, mid, alpha)
