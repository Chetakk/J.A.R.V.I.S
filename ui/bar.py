"""The voice bar: a glowing glass pill along the bottom of the screen, the orb at its left and the talk beside it.

It sits in the strip the dock reserves: while the bar is up only the dock's picture is hidden, the strip stays
reserved, so no window moves. A click opens the chat. It animates only while JARVIS is busy or the bar is moving.
"""
import math
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
from ui.window import ANIMATED, CLAUDE_RGB, LABELS, _clear, _make_transparent  # noqa: E402

PAD = 12  # transparent room around the pill for the glow
PILL = 56
TEXT = 11.5  # point size of what you said
STRIP = 62  # the height of the dock's strip: the pill sits in its middle
HEIGHT = PILL + 2 * PAD
FULL_W = 420 + 2 * PAD  # the pill: shorter than the dock, about as tall
COMPACT_W = 296
WORDS_HOLD = 1.2  # seconds your words stay up before the reply replaces them
SNOW = (1, 1, 1)
WALLPAPER = "org.gnome.desktop.background"
TIPS = {"ignored": "Speech JARVIS ignored. Click to see it in the chat", "bar": "Click to open the chat. Right-click for options"}
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


def _show(cr, layout, x, mid, alpha):
    h = layout.get_pixel_size()[1]
    cr.set_source_rgba(*SNOW, alpha)
    cr.move_to(x, mid - h / 2)
    PangoCairo.show_layout(cr, layout)
    cr.new_path()


class Bar:
    """GTK-thread only; ui.UI wraps it with thread-safe calls."""

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
        self._settle = 0.0
        self._cached = None
        self._glow = self._glass = None
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
            self.words, self.understood, self.reply = text, "", ""
            self._words_at = time.time()
            self._changed()

    def said(self, who, text):
        if who == "you":
            self.heard(text)
        elif who in ("jarvis", "claude"):
            self.reply = text
            self._changed()

    def set_understood(self, text):
        self.understood = text
        self._changed()

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
            return geo.x + (geo.width - FULL_W) / 2, FULL_W
        return geo.x + geo.width - COMPACT_W, COMPACT_W

    def set_mode(self, mode):
        if mode == self.mode:
            return
        was, self.mode = self.mode, mode
        if mode == "full":
            self._dock(HIDE_DOCK)
        elif was == "full":
            self.show_dock()
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
        if not self._timer:
            self._timer = GLib.timeout_add(33, self._tick)

    def _state(self):
        return self.ui._shown[0] if self.ui._shown else "idle"

    def _tick(self):
        moving = self._step()
        alive = moving or time.time() < self._settle or (self.mode != "hidden" and self._state() in ANIMATED)
        if self.win.get_visible():
            self.win.queue_draw()
        if not alive:
            self._timer = None
        return alive

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
                tip = TIPS[zone]
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
        else:
            strength = 0.45
        return min(1.0, strength + (0.2 if self.hover else 0))

    def _sweep(self, cr, w, state, t):
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
            cr.paint_with_alpha(alpha)

    def _draw(self, _win, cr):
        _clear(cr)
        w, t, state = _win.get_allocated_width(), time.time(), self._state()
        self._layers(w)
        cr.set_source_surface(self._glow, 0, 0)
        cr.paint_with_alpha(self._strength(state, t))
        cr.set_source_surface(self._glass, 0, 0)
        cr.paint()
        if state in ("thinking", "claude"):
            self._sweep(cr, w, state, t)
        cr.save()
        cr.translate(PAD, PAD)
        self.ui._paint_orb(cr, PILL, PILL)
        cr.restore()
        zones = self._zones(w)
        self._paint_talk(cr, w, state, t, zones)
        self._paint_zones(cr, zones)
        if state == "listening" and t < self._follow_end and self.mode == "full":
            part = (self._follow_end - t) / (self._follow_total or 1)
            half = (w - 2 * PAD - 56) / 2 * min(1.0, part)
            cr.set_source_rgba(*SNOW, 0.9)
            cr.set_line_width(1.6)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(w / 2 - half, HEIGHT - PAD - 3)
            cr.line_to(w / 2 + half, HEIGHT - PAD - 3)
            cr.stroke()
        return False

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

    def _paint_talk(self, cr, w, state, t, zones):
        left = PAD + PILL + 8
        if self.mode == "compact":
            _show(cr, _layout(cr, self._compact_text(state), TEXT - 1, w - left - PAD - 14, keep_end=state == "thinking"),
                  left, HEIGHT / 2, 0.92)
            return
        room = min([a for a, _b in zones.values()] or [w - PAD - 20]) - left - 8
        note = "Asking Claude…" if state == "claude" else \
            "→ " + self.understood if self.understood and self.show_understood else ""
        mid = HEIGHT / 2
        if self._reply_up(t):
            fade = min(1.0, (t - max(self._text_at, self._words_at + WORDS_HOLD)) / 0.2)
            _show(cr, _layout(cr, self.reply, TEXT, room), left, mid, 0.96 * fade)
        elif self.words:
            self._row(cr, left, mid, room, self.words, note, TEXT, 0.98 * min(1.0, (t - self._words_at) / 0.2))
        else:
            self._row(cr, left, mid, room, "Listening…" if state == "listening" else "", note, TEXT, 0.6)

    def _reply_up(self, t):
        """A reply waits a moment after your words, or a quick one would wipe them before you could read them."""
        return bool(self.reply) and t >= self._words_at + WORDS_HOLD

    def _compact_text(self, state):
        spent = int(self.ui.long_claude())
        if spent:
            return "Claude is working  %d:%02d" % (spent // 60, spent % 60)
        return {"thinking": self.words, "speaking": self.reply}.get(state) or LABELS.get(state, state)

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
            text = "%d ⊘" % self.ignored if name == "ignored" else "→"
            alpha = 0.8 if name == "ignored" or self.show_understood else 0.28
            layout = _layout(cr, text, 9.5, bold=name == "understood")
            _show(cr, layout, centre - layout.get_pixel_size()[0] / 2, mid, alpha)
