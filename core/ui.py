"""JARVIS UI: a transparent floating orb, a chat panel and a top-right tray icon.

GTK 3 is already part of the system, so this adds no downloads. The orb only animates while
JARVIS is busy; when idle nothing is redrawn.
"""
import json
import math
import os
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")
from gi.repository import AppIndicator3, Gdk, GLib, Gtk  # noqa: E402
import cairo  # noqa: E402

from core.terminal import RUN_DIR  # noqa: E402

STATE_FILE = os.path.expanduser("~/.local/share/jarvis/ui.json")
LABELS = {"idle": "Listening for the wake word", "listening": "Listening...", "thinking": "Thinking...",
          "claude": "Asking Claude...", "speaking": "Speaking", "off": "Voice input off",
          "starting": "Starting voice..."}
ANIMATED = {"listening", "thinking", "claude", "speaking", "starting"}
ORB = 64
PANEL_W, PANEL_H = 360, 440

CSS = b"""
.panel { background-color: rgba(10, 10, 10, 0.88); border-radius: 14px;
         border: 1px solid rgba(255, 255, 255, 0.12); }
.panel textview, .panel textview text { background-color: transparent; color: #e6e6e6; }
.panel scrolledwindow { background-color: transparent; }
.panel entry { background-color: rgba(255, 255, 255, 0.06); color: #f2f2f2; border-radius: 8px;
               border: 1px solid rgba(255, 255, 255, 0.14); box-shadow: none; }
.title { color: rgba(255, 255, 255, 0.55); font-size: 9pt; }
"""

# Tray icons: white/grey rings, one per state.
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
<circle cx="11" cy="11" r="8.5" fill="none" stroke="#{ring}" stroke-width="1.6" {dash}/>
<circle cx="11" cy="11" r="{core}" fill="#{fill}"/></svg>"""
ICONS = {"idle": dict(ring="bbbbbb", core=2.6, fill="ffffff", dash=""),
         "listening": dict(ring="ffffff", core=4.2, fill="ffffff", dash=""),
         "thinking": dict(ring="ffffff", core=2.6, fill="bbbbbb", dash='stroke-dasharray="4 3"'),
         "claude": dict(ring="ffffff", core=3.4, fill="888888", dash='stroke-dasharray="2 2"'),
         "speaking": dict(ring="ffffff", core=3.6, fill="ffffff", dash=""),
         "off": dict(ring="777777", core=2.2, fill="777777", dash=""),
         "starting": dict(ring="999999", core=2.2, fill="999999", dash='stroke-dasharray="1 3"')}


def _make_transparent(win):
    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual and screen.is_composited():
        win.set_visual(visual)
    win.set_app_paintable(True)


def _clear(cr):
    cr.set_source_rgba(0, 0, 0, 0)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)


class UI:
    """All methods are safe to call from any thread."""

    def __init__(self, cfg, on_text, on_command, is_speaking):
        self.on_text = on_text          # typed text from the chat panel
        self.on_command = on_command    # menu actions, sent as slash commands / phrases
        self.is_speaking = is_speaking
        self.state = "starting"
        self._shown = None
        self._anim = None
        self._pos = self._load_pos()

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.orb_mode = cfg.get("orb", "auto")  # "auto" | True | False
        self.orb_hide_after = float(cfg.get("orb_hide_after", 4))
        self.card_hide_after = float(cfg.get("card_hide_after", 30))  # with a card open, both go after this
        self._quiet_since = None
        self._build_orb()
        self._build_panel()
        from core.card import Card  # needs the names above, so imported here
        self.card = Card(lambda: self._orb_xy, self.panel.get_visible, self.on_command)  # a choice = said it
        self._build_tray()
        if self.orb_mode is True:
            self.orb.show_all()
        if cfg.get("chat", False):
            self._set_chat(True)
        GLib.timeout_add(200, self._refresh)

    # --- thread-safe API ---

    def set_state(self, state):
        GLib.idle_add(self._set_state, state)

    def add_chat(self, who, text):
        GLib.idle_add(self._add_chat, who, text)

    def show_chat(self, on=None):
        GLib.idle_add(self._set_chat, on)

    def show_orb(self, on=None):
        GLib.idle_add(self._set_orb, on)

    def show_card(self, card=None):
        """Show a card ({"title", "text" markdown, "image" path}); None shows the last one again."""
        def show():
            self._quiet_since = None  # full time on screen; live cards and timers stay while they update
            return self.card.show(card)
        GLib.idle_add(show)

    def close_card(self):
        GLib.idle_add(self.card.close)

    def card_visible(self):
        return self.card.visible

    def current_card(self):
        """The card on screen (or last shown), or None."""
        return self.card.last

    def card_step(self, delta):
        """-1: the previous card from the history, +1: the next one."""
        GLib.idle_add(self.card.step, delta)

    def pin_card(self):
        GLib.idle_add(self.card.pin)

    def unpin_cards(self):
        GLib.idle_add(self.card.unpin_all)

    def run(self):
        Gtk.main()

    def quit(self):
        GLib.idle_add(Gtk.main_quit)

    # --- orb ---

    def _build_orb(self):
        w = self.orb = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        _make_transparent(w)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_type_hint(Gdk.WindowTypeHint.DOCK)  # not tiled, no focus stealing
        w.set_default_size(ORB, ORB)
        w.set_size_request(ORB, ORB)
        w.stick()  # visible on every workspace
        w.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                     | Gdk.EventMask.POINTER_MOTION_MASK)
        w.connect("draw", self._draw_orb)
        w.connect("button-press-event", self._orb_press)
        w.connect("button-release-event", self._orb_release)
        w.connect("motion-notify-event", self._orb_motion)
        w.connect("configure-event", self._orb_moved)
        w.set_tooltip_text("JARVIS: click for chat, drag to move, right-click for menu")
        geo = Gdk.Display.get_default().get_monitor(0).get_workarea()
        x, y = self._pos.get("x"), self._pos.get("y")
        if x is None:
            x, y = geo.x + geo.width - ORB - 24, geo.y + geo.height - ORB - 24
        w.move(x, y)
        self._orb_xy = (x, y)
        self._press = None

    def _draw_orb(self, widget, cr):
        _clear(cr)
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 3
        t = time.time()
        s = self._shown or "idle"

        grad = cairo.RadialGradient(cx, cy - r * 0.3, 0, cx, cy, r)
        grad.add_color_stop_rgba(0, 0.22, 0.22, 0.22, 0.72)
        grad.add_color_stop_rgba(1, 0.02, 0.02, 0.02, 0.60)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.set_source(grad)
        cr.fill()

        ring = 0.22 if s in ("idle", "off") else 0.45
        cr.set_line_width(1.2)
        cr.set_source_rgba(1, 1, 1, ring)
        cr.arc(cx, cy, r - 0.6, 0, 2 * math.pi)
        cr.stroke()

        if s == "listening":
            for i in range(2):
                p = (t * 1.6 + i * 0.5) % 1.0
                cr.set_source_rgba(1, 1, 1, 0.85 * (1 - p))
                cr.set_line_width(1.6)
                cr.arc(cx, cy, r * (0.30 + 0.62 * p), 0, 2 * math.pi)
                cr.stroke()
            self._core(cr, cx, cy, r * 0.20, 0.95)
        elif s == "thinking":
            a = t * 4.0
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(2.4)
            cr.arc(cx, cy, r * 0.62, a, a + 1.4)
            cr.stroke()
            self._core(cr, cx, cy, r * 0.14, 0.6)
        elif s == "claude":
            # Claude is answering: a bright outer ring that breathes slowly, so it's easy to tell apart.
            glow = 0.5 + 0.5 * math.sin(t * 2.5)
            cr.set_line_width(2.0 + 2.0 * glow)
            cr.set_source_rgba(1, 1, 1, 0.35 + 0.6 * glow)
            cr.arc(cx, cy, r - 2.0, 0, 2 * math.pi)
            cr.stroke()
            a = t * 3.0
            cr.set_line_width(2.0)
            cr.set_source_rgba(1, 1, 1, 0.85)
            cr.arc(cx, cy, r * 0.66, a, a + 2.2)
            cr.stroke()
            cr.set_source_rgba(0.7, 0.7, 0.7, 0.75)
            cr.arc(cx, cy, r * 0.46, -a * 1.3, -a * 1.3 + 2.2)
            cr.stroke()
            self._core(cr, cx, cy, r * 0.12, 0.8)
        elif s == "starting":
            cr.set_source_rgba(1, 1, 1, 0.35 + 0.25 * math.sin(t * 3))
            cr.set_line_width(1.4)
            cr.set_dash([2, 4], t * 8)
            cr.arc(cx, cy, r * 0.6, 0, 2 * math.pi)
            cr.stroke()
            cr.set_dash([])
            self._core(cr, cx, cy, r * 0.12, 0.35)
        elif s == "speaking":
            self._core(cr, cx, cy, r * (0.20 + 0.08 * abs(math.sin(t * 9)) + 0.04 * math.sin(t * 23)), 0.95)
        elif s == "off":
            self._core(cr, cx, cy, r * 0.13, 0.25)
        else:
            self._core(cr, cx, cy, r * 0.15, 0.5)
        return False

    @staticmethod
    def _core(cr, cx, cy, radius, alpha):
        g = cairo.RadialGradient(cx, cy, 0, cx, cy, radius * 2.2)
        g.add_color_stop_rgba(0, 1, 1, 1, alpha)
        g.add_color_stop_rgba(0.45, 1, 1, 1, alpha * 0.55)
        g.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.arc(cx, cy, radius * 2.2, 0, 2 * math.pi)
        cr.set_source(g)
        cr.fill()

    def _orb_press(self, widget, ev):
        if ev.button == 3:
            self.menu.popup_at_pointer(ev)
            return True
        if ev.button == 1:
            self._press = (ev.x_root, ev.y_root, False)
        return True

    def _orb_motion(self, widget, ev):
        if self._press and not self._press[2] and (ev.state & Gdk.ModifierType.BUTTON1_MASK):
            if abs(ev.x_root - self._press[0]) + abs(ev.y_root - self._press[1]) > 4:
                self._press = (self._press[0], self._press[1], True)
                widget.begin_move_drag(1, int(ev.x_root), int(ev.y_root), ev.time)
        return True

    def _orb_release(self, widget, ev):
        if ev.button == 1 and self._press and not self._press[2]:
            self._set_chat(None)
        self._press = None
        return True

    def _orb_moved(self, widget, ev):
        if widget.get_visible():
            self._orb_xy = (ev.x, ev.y)
        if (ev.x, ev.y) != (self._pos.get("x"), self._pos.get("y")):
            self._pos = {"x": ev.x, "y": ev.y}
            GLib.timeout_add(800, self._save_pos)
        if self.panel.get_visible():
            self._place_panel()
        self.card._place()
        return False

    # --- chat panel ---

    def _build_panel(self):
        w = self.panel = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        _make_transparent(w)
        w.connect("draw", lambda widget, cr: _clear(cr) or False)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_type_hint(Gdk.WindowTypeHint.DOCK)  # not tiled or outlined by Pop Shell; still takes typing
        w.set_default_size(PANEL_W, PANEL_H)
        w.stick()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("panel")
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_border_width(12)
        box.pack_start(inner, True, True, 0)

        self.title = Gtk.Label(label="JARVIS", xalign=0)
        self.title.get_style_context().add_class("title")
        inner.pack_start(self.title, False, False, 0)

        self.view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(2)
        buf = self.view.get_buffer()
        buf.create_tag("you", foreground="#9c9c9c")
        buf.create_tag("jarvis", foreground="#f4f4f4")
        buf.create_tag("claude", foreground="#cfcfcf", style=2)  # italic
        buf.create_tag("sys", foreground="#6f6f6f", scale=0.85)
        buf.create_tag("heard", foreground="#858585", style=2, scale=0.85)  # what speech recognition heard
        buf.create_tag("who", weight=700, scale=0.85)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.view)
        inner.pack_start(scroll, True, True, 0)

        self.entry = Gtk.Entry(placeholder_text="Type to JARVIS...")
        self.entry.connect("activate", self._entry_send)
        inner.pack_start(self.entry, False, False, 0)
        w.add(box)
        w.connect("key-press-event", lambda _w, ev: ev.keyval == Gdk.KEY_Escape and self._set_chat(False))

    def _place_panel(self):
        ox, oy = self._orb_xy  # works even while the orb is hidden
        geo = Gdk.Display.get_default().get_monitor(0).get_workarea()
        x = ox - PANEL_W - 10 if ox - PANEL_W - 10 >= geo.x else ox + ORB + 10
        y = min(max(geo.y, oy + ORB - PANEL_H), geo.y + geo.height - PANEL_H)
        self.panel.move(x, y)

    def _set_chat(self, on):
        on = (not self.panel.get_visible()) if on is None else on
        if on:
            self._place_panel()
            self.panel.show_all()
            self.panel.present()
            self.entry.grab_focus()
        else:
            self.panel.hide()
        if hasattr(self, "card"):
            self.card._place()  # it sits beside the chat while that's open
        if hasattr(self, "chat_item"):
            self.chat_item.handler_block_by_func(self._menu_chat)
            self.chat_item.set_active(on)
            self.chat_item.handler_unblock_by_func(self._menu_chat)
        return False

    def _set_orb(self, on):
        """Show/hide the orb by hand; this switches it out of auto mode until restart."""
        on = (not self.orb.get_visible()) if on is None else on
        self.orb_mode = bool(on)
        self.orb.show_all() if on else self.orb.hide()
        return False

    def _auto_orb(self, active):
        """Auto mode: show the orb while JARVIS is busy (or the chat is open); hide it after a quiet spell.
        The card goes with it (pinned copies stay); while a card is open the quiet spell is longer, to read it."""
        if self.orb_mode != "auto":
            return
        card = hasattr(self, "card") and self.card.visible
        if active or self.panel.get_visible():
            self._quiet_since = None
            if not self.orb.get_visible():
                self.orb.show_all()
        elif self.orb.get_visible() or card:
            if not self.orb.get_visible():
                self.orb.show_all()  # a card appeared while it was quiet (e.g. from the Claude terminal)
            self._quiet_since = self._quiet_since or time.time()
            if time.time() - self._quiet_since >= (self.card_hide_after if card else self.orb_hide_after):
                self.orb.hide()
                if card:
                    self.card.close()
                self._quiet_since = None

    def _entry_send(self, entry):
        text = entry.get_text().strip()
        entry.set_text("")
        if text:
            self.on_text(text)

    def _add_chat(self, who, text):
        buf = self.view.get_buffer()
        tag = {"you": "you", "jarvis": "jarvis", "claude": "claude", "heard": "heard"}.get(who, "sys")
        end = buf.get_end_iter()
        if buf.get_char_count():
            buf.insert(end, "\n")
        if who in ("you", "jarvis"):
            buf.insert_with_tags_by_name(buf.get_end_iter(), ("You  " if who == "you" else "JARVIS  "), "who", tag)
        buf.insert_with_tags_by_name(buf.get_end_iter(), text, tag)
        if buf.get_line_count() > 300:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(buf.get_line_count() - 300))
        self.view.scroll_to_mark(buf.get_insert(), 0, False, 0, 1)
        buf.place_cursor(buf.get_end_iter())
        self.view.scroll_mark_onscreen(buf.get_insert())
        return False

    # --- tray icon ---

    def _build_tray(self):
        icon_dir = os.path.join(RUN_DIR, "icons")
        os.makedirs(icon_dir, exist_ok=True)
        for name, v in ICONS.items():
            with open(os.path.join(icon_dir, "jarvis-%s.svg" % name), "w") as f:
                f.write(ICON_SVG.format(**v))
        ind = self.tray = AppIndicator3.Indicator.new("jarvis", "jarvis-idle",
                                                      AppIndicator3.IndicatorCategory.APPLICATION_STATUS)
        ind.set_icon_theme_path(icon_dir)
        ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        ind.set_title("JARVIS")

        m = self.menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="JARVIS: starting")
        self.status_item.set_sensitive(False)
        m.append(self.status_item)
        m.append(Gtk.SeparatorMenuItem())
        self.chat_item = Gtk.CheckMenuItem(label="Show chat")
        self.chat_item.connect("toggled", self._menu_chat)
        m.append(self.chat_item)
        orb_item = Gtk.CheckMenuItem(label="Always show orb")
        orb_item.set_active(self.orb_mode is True)
        orb_item.connect("toggled", self._menu_orb)
        m.append(orb_item)
        sys_item = Gtk.CheckMenuItem(label="System mode (no Claude)")
        sys_item.connect("toggled", lambda i: self.on_command("/sys" if i.get_active() else "/agent"))
        m.append(sys_item)
        mute_item = Gtk.CheckMenuItem(label="Mute voice")
        mute_item.connect("toggled", lambda i: self.on_command("/mute"))
        m.append(mute_item)
        m.append(Gtk.SeparatorMenuItem())
        for label, command in (("Open Claude terminal", "open claude"),
                               ("Open Claude in VS Code", "open claude in vs code"),
                               ("Go to JARVIS workspace", "/desk")):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, c=command: self.on_command(c))
            m.append(item)
        m.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit JARVIS")
        quit_item.connect("activate", lambda _i: self.on_command("/quit"))
        m.append(quit_item)
        m.show_all()
        ind.set_menu(m)

    def _menu_orb(self, item):
        if item.get_active():
            self._set_orb(True)
        else:
            self.orb_mode = "auto"  # back to: appears while talking

    def _menu_chat(self, item):
        self._set_chat(item.get_active())

    # --- state ---

    def _set_state(self, state):
        self.state = state
        self._refresh()
        return False

    def _refresh(self):
        shown = "speaking" if self.state == "idle" and self.is_speaking() else self.state
        self._auto_orb(shown in ANIMATED and shown != "starting")
        if shown != self._shown:
            self._shown = shown
            self.tray.set_icon_full("jarvis-%s" % shown, LABELS.get(shown, shown))
            self.status_item.set_label("JARVIS: %s" % LABELS.get(shown, shown))
            self.title.set_text("JARVIS  ·  %s" % LABELS.get(shown, shown))
            self.orb.queue_draw()
            if shown in ANIMATED and not self._anim:
                self._anim = GLib.timeout_add(40, self._tick)
        return True

    def _tick(self):
        if self._shown not in ANIMATED:
            self._anim = None
            self.orb.queue_draw()
            return False
        self.orb.queue_draw()
        return True

    # --- position memory ---

    @staticmethod
    def _load_pos():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_pos(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self._pos, f)
        except OSError:
            pass
        return False
