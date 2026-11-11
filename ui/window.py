"""JARVIS UI: a glass voice bar with the orb at its left (ui/bar.py), a chat panel and a top-right tray icon.

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
from gi.repository import AppIndicator3, Gdk, GLib, Gtk, Pango  # noqa: E402
import cairo  # noqa: E402

from system.terminal import RUN_DIR  # noqa: E402
from voice.level import level as mic_level  # noqa: E402

STATE_FILE = os.path.expanduser("~/.local/share/jarvis/ui.json")
LABELS = {"idle": "Ready", "listening": "Listening…", "thinking": "Thinking…", "claude": "Asking Claude…",
          "speaking": "Speaking…", "off": "Voice off", "starting": "Starting…"}
ANIMATED = {"listening", "thinking", "claude", "speaking", "starting"}
MUTED = {"voice": "voice muted", "speaker": "speaker muted", "mic": "not listening"}
LONG_CLAUDE = 10  # seconds of Claude before the dock comes back
ORB_FADE = 0.3  # seconds the orb takes to cross-fade from one state to the next
CLAUDE_RGB = (0.85, 0.47, 0.34)  # Claude's warm orange, as in the chat bubbles
PANEL_W, PANEL_H = 390, 640
GLOW = 12  # the .panel margin: transparent room round the chat and cards for their glow, like the bar's
CHAT_KEEP = 200  # messages kept in the panel

CSS = b"""
.panel { background-color: rgba(255, 255, 255, 0.05); border-radius: 16px; margin: 12px;
         background-image: linear-gradient(to bottom, rgba(255, 255, 255, 0.09), rgba(255, 255, 255, 0) 90px);
         border: 1px solid rgba(255, 255, 255, 0.30); border-top-color: rgba(255, 255, 255, 0.42);
         border-bottom-color: rgba(255, 255, 255, 0.18);
         box-shadow: 0 0 12px rgba(255, 255, 255, 0.10), 0 0 3px rgba(255, 255, 255, 0.12); }
.panel * { font-size: 11pt; }
.panel scrolledwindow, .panel viewport, .panel list { background-color: transparent; }
.panel entry { background-color: rgba(255, 255, 255, 0.07); color: #f2f2f2; border-radius: 12px;
               border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: none;
               padding: 7px 12px; min-height: 20px; }
.panel entry:focus { border-color: rgba(255, 255, 255, 0.34); }
.panel scrolledwindow undershoot.top, .panel scrolledwindow undershoot.bottom { background: none; }
.panel scrollbar slider { background-color: rgba(255, 255, 255, 0.22); min-width: 4px; }
.title { color: #f4f4f4; font-weight: bold; font-size: 11pt; }
.status { color: rgba(255, 255, 255, 0.5); font-size: 9.5pt; }
.header { padding: 0 4px 8px 4px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); }
.dot { color: rgba(255, 255, 255, 0.35); font-size: 9pt; }
.dot.live { color: #ffffff; }
.bubble { border-radius: 13px; padding: 6px 11px; }
.bubble label selection { background-color: rgba(255, 255, 255, 0.28); }
.you { background-color: rgba(255, 255, 255, 0.24); color: #f6f6f6; border-bottom-right-radius: 4px; }
.jarvis { background-color: rgba(20, 21, 24, 0.38); color: #f2f2f2;
          border: 1px solid rgba(255, 255, 255, 0.09); border-bottom-left-radius: 4px; }
.claude { background-color: rgba(217, 119, 87, 0.10); border: 1px solid rgba(217, 119, 87, 0.30);
          border-bottom-left-radius: 4px; }
.you label { color: #f6f6f6; }
.jarvis label { color: #f2f2f2; }
.claude label { color: #f2e6e1; }
.sender { color: #e08a6d; font-size: 8.5pt; font-weight: bold; }
.heard { color: rgba(255, 255, 255, 0.4); font-style: italic; font-size: 9pt; }
.sys { color: rgba(255, 255, 255, 0.45); font-size: 9pt; }
"""
# ui.look in settings.yaml: "glass" (the default above, glowing glass like the bar), "smoke" (the same glass with a
# dark tint under it, so white text stays readable over a bright page) or "solid" (dark and opaque).
LOOK_CSS = {"smoke": b".panel { background-color: rgba(16, 17, 20, 0.40); }",
            "solid": b"""
.panel { background-color: rgba(30, 31, 34, 0.985); border-color: rgba(255, 255, 255, 0.12); }
.you { background-color: rgba(255, 255, 255, 0.16); }
.jarvis { background-color: rgba(255, 255, 255, 0.05); }
"""}
# The orb's glass for each look: centre and edge colours, sheen, rim when idle / active.
ORB_LOOK = {"glass": ((1, 1, 1, 0.08), (1, 1, 1, 0.02), 0.09, (0.26, 0.42)),  # clear glass, no tint
            "smoke": ((1, 1, 1, 0.08), (1, 1, 1, 0.02), 0.09, (0.26, 0.42)),  # the orb stays clear glass
            "solid": ((0.34, 0.34, 0.36, 0.95), (0.11, 0.11, 0.12, 0.90), 0.09, (0.22, 0.45))}

# Tray icons: white/grey rings, one per state, plus the two that say what's muted. A slash through the ring
# means you won't hear JARVIS (its voice muted, or the speaker muted); grey means it can't hear you (mic muted).
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
<circle cx="11" cy="11" r="8.5" fill="none" stroke="#{ring}" stroke-width="1.6" {dash}/>
<circle cx="11" cy="11" r="{core}" fill="#{fill}"/>{slash}</svg>"""
SLASH = ('<line x1="4.6" y1="17.4" x2="17.4" y2="4.6" stroke="#{ring}" stroke-width="2.2" '
         'stroke-linecap="round"/>')
DEAF = dict(ring="777777", fill="777777")  # the microphone is muted: JARVIS can't hear you at all
ICONS = {"idle": dict(ring="bbbbbb", core=2.6, fill="ffffff", dash=""),
         "listening": dict(ring="ffffff", core=4.2, fill="ffffff", dash=""),
         "thinking": dict(ring="ffffff", core=2.6, fill="bbbbbb", dash='stroke-dasharray="4 3"'),
         "claude": dict(ring="e08a6d", core=3.4, fill="e08a6d", dash='stroke-dasharray="2 2"'),
         "speaking": dict(ring="ffffff", core=3.6, fill="ffffff", dash=""),
         "off": dict(ring="777777", core=2.2, fill="777777", dash=""),
         "starting": dict(ring="999999", core=2.2, fill="999999", dash='stroke-dasharray="1 3"')}


def bar_mode(claude_for, active, chat, lingering, cards, compact, stay=False):
    """The voice bar: "full" (the dock is hidden), "compact" (the orb in the corner, the dock back) or "hidden".
    compact: it never leaves the corner; stay ("always on"): it goes back to the corner when idle instead of going."""
    if compact or claude_for > LONG_CLAUDE:
        return "compact"  # compact mode is ticked, or a long Claude run mustn't keep the dock away for minutes
    if active or chat or lingering:
        return "full"
    return "compact" if cards or stay else "hidden"


def _icon_name(state, silent, deaf):
    """The tray icon for a state and what's muted; _build_tray writes one SVG per combination."""
    return "jarvis-%s%s%s" % (state, "-silent" if silent else "", "-deaf" if deaf else "")


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


def eye_look(layers, r, t, beat):
    """The idle eyes' look for `layers` [(mood, strength 0..1, style)], as numbers that blend: moves and sizes add
    up, scales multiply, so any mix (one dance move fading into the next, a talk mood over the music, falling
    asleep) changes smoothly. The moods: from talk happy ^ ^ that bounce, sad drooping lids and a slow sigh, angry
    lids slanting in with a tremble, stressed small darting eyes and a sweat drop; from music, with headphones on,
    the dance and sway moves; from the computer busy squinting and scanning as if reading (straining sweats too), hot
    drooping and wobbling with a sweat drop, squeezed pressed thin and fidgety, sleepy half shut with z's; and asleep
    (mic or voice off): closed ‿ ‿ breathing slowly."""
    L = dict(dx=0.0, dy=0.0, ew=1.0, eh=1.0, gap=1.0, bright=1.0, inner=0.0, outer=0.0, up=0.0, down=0.0, tilt=0.0,
             phones=0.0, drop=0.0, zz=0.0, notes=0.0)
    lift = [0.0, 0.0]
    roam = 1 - min(1.0, sum(k for mood, k, _s in layers if mood == "watching"))  # watching the pointer: no darting
    for mood, k, style in layers:
        if not mood or k <= 0:
            continue
        if mood == "happy":
            L["dy"] -= r * 0.05 * abs(math.sin(t * 5)) * k
            L["up"] += k
        elif mood == "sad":  # a slow sigh: sinking a little, then back
            L["dy"] += r * (0.05 + 0.025 * (0.5 - 0.5 * math.cos(t * 1.4))) * k
            L["outer"] += 0.5 * k
            L["bright"] *= 1 - 0.3 * k
        elif mood == "angry":
            L["dx"] += r * 0.02 * math.sin(t * 45) * k
            L["inner"] += 0.55 * k
        elif mood == "stressed":
            L["dx"] += r * 0.06 * math.sin(t * 7) * math.sin(t * 2.3) * k * roam
            L["eh"] *= 1 - 0.3 * k
            L["drop"] = max(L["drop"], k)
        elif mood == "dance":
            hop = (1 - beat % 1) ** 2  # 1 on the beat, falling back till the next
            swing = math.sin(math.pi * beat)  # one way, then the other, a beat each
            L["phones"] = max(L["phones"], k)
            if style == "nod":  # dips down on the beat, like nodding along
                L["dy"] += r * 0.09 * hop * k
                L["eh"] *= 1 - 0.3 * hop * k
            elif style == "groove":  # happy ^ ^ that bounce and tilt side to side
                L["up"] += k
                L["tilt"] += 0.28 * swing * k
                L["dy"] -= r * 0.06 * hop * k
            elif style == "seesaw":  # one eye up while the other's down, swapping every beat
                lift[0] -= r * 0.09 * swing * k
                lift[1] += r * 0.09 * swing * k
            elif style == "pulse":  # eyes and glow swell on the beat
                L["ew"] *= 1 + 0.3 * hop * k
                L["eh"] *= 1 + 0.25 * hop * k
                L["bright"] *= 1 + 0.3 * hop * k
            else:  # "hop": up on the beat, a step aside every beat
                L["dy"] -= r * 0.08 * hop * k
                L["eh"] *= 1 - 0.15 * hop * k
                L["dx"] += r * 0.06 * swing * k
        elif mood == "sway":  # relaxed, never sad: slow music is calm, not gloomy
            slow = math.sin(math.pi * beat / 2)  # one way over two beats, then back
            L["phones"] = max(L["phones"], k)
            if style == "smile":  # soft ^ ^, rocking gently
                L["up"] += k
                L["tilt"] += 0.12 * slow * k
            else:  # "notes": closed ‿ ‿, lost in it, swaying a little, little notes drifting up
                L["down"] += k
                L["dx"] += r * 0.06 * slow * k
                L["notes"] = max(L["notes"], k)
        elif mood in ("busy", "straining"):  # squinting, eyes scanning slowly across as if reading
            L["eh"] *= 1 - 0.45 * k
            L["inner"] += 0.15 * k
            L["dx"] += r * 0.07 * math.sin(t * 1.3) * k * roam
            if mood == "straining":
                L["drop"] = max(L["drop"], k)
        elif mood == "hot":
            L["dy"] += r * 0.06 * k
            L["outer"] += 0.35 * k
            L["dx"] += r * 0.015 * math.sin(t * 9) * k
            L["eh"] *= 1 + 0.1 * math.sin(t * 3) * k
            L["drop"] = max(L["drop"], k)
        elif mood == "squeezed":
            L["ew"] *= 1 - 0.35 * k
            L["eh"] *= 1 + 0.08 * k
            L["gap"] *= 1 - 0.25 * k
            L["dx"] += r * 0.04 * math.sin(t * 8) * k
        elif mood == "sleepy":
            L["dy"] += r * 0.04 * k
            L["inner"] += 0.5 * k
            L["outer"] += 0.5 * k
            L["bright"] *= 1 - 0.2 * k
            L["zz"] = max(L["zz"], k)
        elif mood == "asleep":  # closed, dim, rising and falling with slow breaths
            L["down"] += k
            L["dy"] += r * (0.03 * math.sin(t * math.pi / 2) - 0.02) * k
            L["bright"] *= 1 - (0.45 - 0.1 * math.sin(t * math.pi / 2)) * k
    L["inner"], L["outer"] = min(L["inner"], 0.9), min(L["outer"], 0.9)
    L["up"], L["down"] = min(L["up"], 1.0), min(L["down"], 1.0)
    L["lift"] = lift
    return L


class UI:
    """All methods are safe to call from any thread."""

    def __init__(self, cfg, on_text, on_command, is_speaking):
        self.on_text = on_text          # typed text from the chat panel
        self.on_command = on_command    # menu actions, sent as slash commands / phrases
        self.is_speaking = is_speaking
        self.state = "starting"
        self._shown = None
        self._voice = 0.0  # the mic level, smoothed: jumps up with your voice, eases back down
        self._claude_since = None  # when the current Claude run began

        provider = Gtk.CssProvider()
        self.look = cfg.get("look") if cfg.get("look") in ORB_LOOK else "glass"
        provider.load_from_data(CSS + LOOK_CSS.get(self.look, b""))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._prefs = self._load_prefs()
        bar = self._prefs.get("bar") or ("compact" if self._prefs.get("compact") else None)  # "compact": older saves
        self.orb_mode = {"compact": True, "always": "always", "auto": "auto"}.get(bar, cfg.get("orb", "auto"))
        # orb_mode: "auto" | "always" (the corner when idle) | True (compact) | False
        self.orb_hide_after = float(cfg.get("orb_hide_after", 4))
        self.hide_after = float(cfg.get("hide_after", 60))  # the chat and each card alike
        self._quiet_since = None  # JARVIS idle and you not on the chat or card since then
        self._ticked = None  # the last quiet tick, for counting each card's own quiet time
        self._busy_at = 0.0  # when JARVIS was last busy, or the chat or the bar last in use
        self.mutes = {"voice": False, "mic": False, "speaker": False}  # kept up to date by set_mutes()
        self.timers = {}  # card id -> (what it's for, end, total s) of each running timer, counted down by the compact bar
        self.extras = {}  # what the idle compact bar may add after "Ready" (ui/extras.py keeps it fresh)
        from ui.bar import Bar  # needs the names above, so imported here
        self.bar = Bar(self, bool(self._prefs.get("understood", True)))
        self._build_panel()
        from ui.card import Deck
        self._chat_on = False
        self.card = Deck(self.bar.top, lambda: self._chat_on, self.panel.move, self.on_command,  # a choice = said it
                         lambda: self.bar.mode == "compact")
        self._build_tray()
        if cfg.get("chat", False):
            self._set_chat(True)
        GLib.timeout_add(200, self._refresh)

    # --- thread-safe API ---

    def set_state(self, state):
        GLib.idle_add(self._set_state, state)

    def set_mutes(self, voice=None, mic=None, speaker=None):
        """What's muted now: the tray icon and its check boxes follow."""
        GLib.idle_add(self._set_mutes, voice, mic, speaker)

    def set_timer(self, card_id, what, end=None):
        """A timer running till `end` (None: it's done or cancelled); `what` it's for, "" for a plain one."""
        if end:
            self.timers[card_id] = (what, end, end - time.time())
        else:
            self.timers.pop(card_id, None)

    def set_extras(self, extras):
        """From ui/extras.py's thread: one dict swapped in whole, so the bar never sees it half done."""
        self.extras = extras

    def add_chat(self, who, text):
        GLib.idle_add(self._add_chat, who, text)
        GLib.idle_add(self.bar.said, who, text)

    def show_understood(self, text):
        """What JARVIS took your words to mean, on the voice bar."""
        GLib.idle_add(self.bar.set_understood, text)

    def set_understood(self, on=None):
        """Show or hide that on the bar (None flips it); remembered across restarts."""
        GLib.idle_add(self._set_understood, on)

    def hint(self, text, seconds=8):
        """Why JARVIS won't hear you right now ("Music's on: say Jarvis first"), on the idle bar for a few seconds."""
        GLib.idle_add(self.bar.show_hint, text, seconds)

    def count_ignored(self):
        GLib.idle_add(self.bar.add_ignored)

    def follow_timer(self, seconds):
        """JARVIS listens without the name for this long: the bar counts it down."""
        GLib.idle_add(self.bar.follow, seconds)

    def show_chat(self, on=None):
        GLib.idle_add(self._set_chat, on)

    def show_heard(self, text):
        """What you just said, in the typing box ("" clears it); never over words you're typing yourself."""
        GLib.idle_add(self._show_heard, text)
        GLib.idle_add(self.bar.heard, text)

    def _show_heard(self, text):
        if self.entry.get_text() in ("", getattr(self, "_heard_text", "")):
            self.entry.set_text(text)
            self.entry.set_position(-1)
        self._heard_text = text
        return False

    def show_orb(self, on=None):
        GLib.idle_add(self._set_orb, on)

    def show_card(self, card=None):
        """Open a card ({"title", "text" markdown, "image" path}) beside any already up; one with the id of a card
        on screen updates it in place. None shows the last one again."""
        def show():
            self._quiet_since = None  # the chat's full time again; the card's own clock restarts in Card.show
            return self.card.show(card)
        GLib.idle_add(show)

    def show_cards(self, cards):
        """Several cards at once, side by side."""
        def show():
            self._quiet_since = None
            return self.card.show_many(cards)
        GLib.idle_add(show)

    def close_card(self, card_id=None, every=False):
        """Close the newest card, the one with `card_id`, or every card (pinned copies stay)."""
        GLib.idle_add(lambda: self.card.close(card_id, every))

    def card_visible(self):
        return self.card.visible

    def card_open(self, card_id):
        """Is the card with this id on screen (newest or not)?"""
        return card_id in self.card.ids()

    def current_card(self):
        """The newest card on screen (or the last shown), or None."""
        return self.card.current()

    def card_step(self, delta):
        """-1: the previous card from the history, +1: the next one."""
        GLib.idle_add(self.card.step, delta)

    def pin_card(self):
        GLib.idle_add(self.card.pin)

    def unpin_cards(self):
        GLib.idle_add(self.card.unpin_all)

    def run(self):
        Gtk.main()
        self.bar.show_dock(wait=True)

    def quit(self):
        GLib.idle_add(Gtk.main_quit)

    # --- orb ---

    def _paint_orb(self, cr, w, h):
        cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 3
        t = time.time()
        s = self._shown[0] if self._shown else "idle"  # _shown is (state, silent, deaf)
        tau = 2 * math.pi

        # A glass ball, tinted like the chat panel, with a soft sheen across the top.
        centre, edge, shine, _rim = ORB_LOOK[getattr(self, "look", "glass")]
        grad = cairo.RadialGradient(cx, cy - r * 0.35, 0, cx, cy, r)
        grad.add_color_stop_rgba(0, *centre)
        grad.add_color_stop_rgba(1, *edge)
        cr.arc(cx, cy, r, 0, tau)
        cr.set_source(grad)
        cr.fill()
        sheen = cairo.RadialGradient(cx, cy - r * 0.62, 0, cx, cy - r * 0.62, r * 0.62)
        sheen.add_color_stop_rgba(0, 1, 1, 1, shine)
        sheen.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.arc(cx, cy, r - 1, 0, tau)
        cr.set_source(sheen)
        cr.fill()

        # A new state cross-fades in over the old one, growing in a little, so nothing jumps.
        if s != getattr(self, "_orb_to", s):
            self._orb_from, self._orb_at = self._orb_to, t
        self._orb_to = s
        p = min(1.0, (t - getattr(self, "_orb_at", 0.0)) / ORB_FADE)
        if p < 1:
            for state, alpha, scale in ((self._orb_from, 1 - p, 1 + 0.06 * p), (s, p, 0.88 + 0.12 * p)):
                cr.push_group()
                self._orb_content(cr, state, cx, cy, r, t, scale)
                cr.pop_group_to_source()
                cr.paint_with_alpha(alpha)
        else:
            self._orb_content(cr, s, cx, cy, r, t)

    def _orb_content(self, cr, s, cx, cy, r, t, scale=1.0):
        """The rim and what's inside the orb for state `s`; the inside drawn `scale` times its size."""
        tau = 2 * math.pi
        rim = ORB_LOOK[getattr(self, "look", "glass")][3]
        glow = 0.5 + 0.5 * math.sin(t * 2.2)
        cr.set_line_width(1.3)
        if s == "claude":
            cr.set_source_rgba(*CLAUDE_RGB, 0.45 + 0.45 * glow)
        else:
            cr.set_source_rgba(1, 1, 1, rim[0] if s in ("idle", "off") else rim[1])
        cr.arc(cx, cy, r - 0.65, 0, tau)
        cr.stroke()
        cr.save()
        cr.translate(cx, cy)
        cr.scale(scale, scale)
        cr.translate(-cx, -cy)

        if s == "listening":
            # Like Google's: four white bars in a row that jump with your voice and idle as bobbing dots.
            heard = mic_level()
            self._voice += (heard - self._voice) * (0.6 if heard > self._voice else 0.15)
            v, bw, gap = self._voice, r * 0.15, r * 0.09
            x0 = cx - (4 * bw + 3 * gap) / 2 + bw / 2
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(bw)
            for k in range(4):
                wobble = 0.5 + 0.5 * math.sin(t * 11 + k * 1.9)
                tall = r * (0.05 * math.sin(t * 4 + k * 0.8) + v * (0.35 + 0.45 * wobble) * (1.0 if k in (1, 2) else 0.75))
                x = x0 + k * (bw + gap)
                cr.set_source_rgba(1, 1, 1, 0.95)
                cr.move_to(x, cy - max(tall, 0) / 2 - 0.01)
                cr.line_to(x, cy + max(tall, 0) / 2 + 0.01)
                cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
        elif s == "thinking":
            # A comet: a bright head with a fading tail that stretches and shrinks as it spins.
            a, span, n = t * 5.0, 2.0 + 0.6 * math.sin(t * 1.7), 16
            for i in range(n):
                f = (i + 1) / n
                cr.set_source_rgba(1, 1, 1, 0.95 * f ** 1.4)
                cr.set_line_width(1.0 + 1.8 * f)
                cr.arc(cx, cy, r * 0.62, a - span * (1 - i / n), a - span * (1 - f))
                cr.stroke()
            cr.arc(cx + r * 0.62 * math.cos(a), cy + r * 0.62 * math.sin(a), 1.6, 0, tau)
            cr.fill()
            self._core(cr, cx, cy, r * 0.14, 0.5 + 0.1 * math.sin(t * 3))
        elif s == "claude":
            # Claude is answering: a warm halo that breathes and a slowly turning, shimmering star.
            halo = cairo.RadialGradient(cx, cy, r * 0.5, cx, cy, r)
            halo.add_color_stop_rgba(0, *CLAUDE_RGB, 0)
            halo.add_color_stop_rgba(1, *CLAUDE_RGB, 0.18 + 0.30 * glow)
            cr.arc(cx, cy, r - 1, 0, tau)
            cr.set_source(halo)
            cr.fill()
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(2.4)
            for k in range(8):
                ang = t * 0.8 + k * tau / 8
                reach = r * (0.30 + 0.10 * math.sin(t * 3 + k * 0.9))
                cr.set_source_rgba(0.96, 0.66, 0.54, 0.65 + 0.3 * math.sin(t * 3 + k * 0.9))
                cr.move_to(cx + r * 0.09 * math.cos(ang), cy + r * 0.09 * math.sin(ang))
                cr.line_to(cx + reach * math.cos(ang), cy + reach * math.sin(ang))
                cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
            self._core(cr, cx, cy, r * 0.08, 0.8)
        elif s == "starting":
            # Eight dots lighting up in turn, like a loading spinner.
            head = t * 9
            for k in range(8):
                lit = 1 - ((head - k) % 8) / 8
                ang = k * tau / 8 - math.pi / 2
                cr.set_source_rgba(1, 1, 1, 0.12 + 0.75 * lit ** 2)
                cr.arc(cx + r * 0.56 * math.cos(ang), cy + r * 0.56 * math.sin(ang), 1.4 + 1.3 * lit, 0, tau)
                cr.fill()
            self._core(cr, cx, cy, r * 0.12, 0.35)
        elif s == "speaking":
            # A sound wave wrapped into a circle, wobbling around the pulsing core.
            amp = r * (0.03 + 0.035 * abs(math.sin(t * 7)))
            for base, alpha, phase in ((0.50, 0.75, 0.0), (0.42, 0.35, 1.7)):
                for j in range(65):
                    th = tau * j / 64
                    rr = r * base + amp * (0.7 * math.sin(3 * th + t * 5 + phase) + 0.3 * math.sin(4 * th - t * 7))
                    (cr.line_to if j else cr.move_to)(cx + rr * math.cos(th), cy + rr * math.sin(th))
                cr.close_path()
                cr.set_source_rgba(1, 1, 1, alpha)
                cr.set_line_width(1.5)
                cr.stroke()
            self._core(cr, cx, cy, r * (0.18 + 0.07 * abs(math.sin(t * 9)) + 0.03 * math.sin(t * 23)), 0.95)
        elif s == "off":
            # JARVIS's voice off: the eyes are shut, asleep, breathing.
            self._pet_eyes(cr, cx, cy, r, 1.0, 1.0, t=t, layers=[("asleep", 1.0, None)])
        else:
            # Idle: a pet's eyes that breathe, blink and now and then glance aside; moods and sleep are looks they
            # blend between (bar.layers).
            from ui.bar import _breath, _eyes  # ui.bar imports this module
            gx, gy = self.bar.gaze
            self._pet_eyes(cr, cx + gx * r * 0.13, cy + gy * r * 0.10, r, _eyes(t)[0], 0.78 + 0.22 * _breath(t),
                           t=t, beat=self.bar.beat, layers=self.bar.layers)
        cr.restore()

    @staticmethod
    def _pet_eyes(cr, cx, cy, r, awake, bright, mood=None, k=0.0, t=0.0, beat=0.0, style=None, layers=None):
        """Two glowing eyes in the look `eye_look` makes of `layers` [(mood, strength, style)] (or of one mood): any
        mix of looks is drawn, so one fades into the next with no jump."""
        from ui.blocks import _round_rect
        L = eye_look(layers if layers is not None else [(mood, k, style)], r, t, beat)
        ew, gap, bright = r * 0.21 * L["ew"], r * 0.26 * L["gap"], bright * L["bright"]
        eh = max(r * 0.06, r * 0.40 * awake) * L["eh"]  # shut, it's a flat line
        drop_x, drop_y, base_x, base_y = cx + r * 0.55, cy - r * 0.42, cx, cy
        cx, cy = cx + L["dx"], cy + L["dy"]
        inner, outer, up, down = L["inner"], L["outer"], L["up"], L["down"]
        shape = max(0.0, 1 - up - down)  # the pills fade, and flatten, as ^ ^ or ‿ ‿ come in: eyes closing into them
        eh *= 1 - 0.75 * min(1.0, up + down)
        if L["tilt"]:
            cr.save()
            cr.translate(cx, cy)
            cr.rotate(L["tilt"])
            cr.translate(-cx, -cy)
        mid = cy
        if L["phones"] > 0.01:  # headphones: a band over the top, a cup on each side
            hy = mid + r * 0.04
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(r * 0.09)
            cr.set_source_rgba(1, 1, 1, 0.7 * L["phones"])
            cr.arc(cx, hy, r * 0.68, math.pi * 1.1, math.pi * 1.9)
            cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
            for side in (-1, 1):  # chunkier and rounder than the eyes, so they read as cups, not more eyes
                _round_rect(cr, cx + side * r * 0.68 - r * 0.12, hy - r * 0.15, r * 0.24, r * 0.32, r * 0.11)
                cr.set_source_rgba(1, 1, 1, 0.55 * L["phones"])
                cr.fill()
        for side in (-1, 1):
            x, cy = cx + side * gap, mid + L["lift"][(side + 1) // 2]
            glow = cairo.RadialGradient(x, cy, 0, x, cy, r * 0.42)
            glow.add_color_stop_rgba(0, 1, 1, 1, 0.28 * bright)
            glow.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.arc(x, cy, r * 0.42, 0, 2 * math.pi)
            cr.set_source(glow)
            cr.fill()
            if shape > 0.01:
                cr.save()
                if inner or outer:  # the lid: keep what's below a slanted line, inner end towards the nose
                    top = cy - eh / 2
                    cr.move_to(x - side * ew, top + inner * eh)
                    cr.line_to(x + side * ew, top + outer * eh)
                    cr.line_to(x + side * ew, cy + eh)
                    cr.line_to(x - side * ew, cy + eh)
                    cr.close_path()
                    cr.clip()
                _round_rect(cr, x - ew / 2, cy - eh / 2, ew, eh, min(ew, eh) / 2)
                cr.set_source_rgba(1, 1, 1, 0.95 * bright * shape)
                cr.fill()
                cr.restore()
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(r * 0.21 * 0.6)
            for weight, start, y in ((up, 1.12, cy + r * 0.06), (down, 0.12, cy - r * 0.05)):  # ^ ^ and ‿ ‿
                if weight > 0.01:
                    cr.set_source_rgba(1, 1, 1, 0.95 * bright * min(1.0, weight))
                    cr.arc(x + side * r * 0.03, y, r * 0.21 * 0.68, math.pi * start, math.pi * (start + 0.76))
                    cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
        if L["tilt"]:
            cr.restore()
        if L["notes"] > 0.01:  # little notes drifting up
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            for i in range(2):
                p = (beat / 4 + i * 0.5) % 1
                cr.set_font_size(r * (0.32 + 0.1 * p))
                cr.set_source_rgba(1, 1, 1, 0.85 * L["notes"] * math.sin(math.pi * p))
                cr.move_to(base_x + (1 - 2 * i) * r * (0.3 + 0.15 * p) - r * 0.08, base_y - r * (0.3 + 0.35 * p))
                cr.show_text("♪")
            cr.new_path()
        if L["zz"] > 0.01:  # z's floating up and away
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            for i in range(2):
                p = (t / 2.5 + i * 0.5) % 1
                cr.set_font_size(r * (0.16 + 0.12 * p))
                cr.set_source_rgba(1, 1, 1, 0.8 * L["zz"] * (1 - p))
                cr.move_to(base_x + r * (0.32 + 0.2 * p), base_y - r * (0.22 + 0.4 * p))
                cr.show_text("z")
            cr.new_path()
        if L["drop"] > 0.01:  # a sweat drop sliding down the side
            p = t % 1.6 / 1.6
            x, y, s = drop_x, drop_y + r * 0.25 * p, r * 0.09
            cr.move_to(x, y - s * 1.6)
            cr.curve_to(x + s, y - s * 0.4, x + s, y + s, x, y + s)
            cr.curve_to(x - s, y + s, x - s, y - s * 0.4, x, y - s * 1.6)
            cr.set_source_rgba(1, 1, 1, 0.75 * L["drop"] * (1 - 0.6 * p))
            cr.fill()

    @staticmethod
    def _core(cr, cx, cy, radius, alpha):
        g = cairo.RadialGradient(cx, cy, 0, cx, cy, radius * 2.2)
        g.add_color_stop_rgba(0, 1, 1, 1, alpha)
        g.add_color_stop_rgba(0.45, 1, 1, 1, alpha * 0.55)
        g.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.arc(cx, cy, radius * 2.2, 0, 2 * math.pi)
        cr.set_source(g)
        cr.fill()

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
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("panel")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_border_width(14)
        box.pack_start(inner, True, True, 0)

        header = Gtk.Box(spacing=8)
        header.get_style_context().add_class("header")
        self.dot = Gtk.Label(label="\u25cf")
        self.dot.get_style_context().add_class("dot")
        name = Gtk.Label(label="JARVIS")
        name.get_style_context().add_class("title")
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.get_style_context().add_class("status")
        header.pack_start(self.dot, False, False, 0)
        header.pack_start(name, False, False, 0)
        header.pack_start(self.title, True, True, 0)
        inner.pack_start(header, False, False, 0)

        self.msgs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.msgs.set_margin_end(6)  # room for the scrollbar
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.msgs)
        adj = self._adj = scroll.get_vadjustment()
        self._at_bottom = True  # follow new messages unless scrolled up to read
        self._last_kind = None
        adj.connect("value-changed", lambda a: setattr(
            self, "_at_bottom", a.get_value() >= a.get_upper() - a.get_page_size() - 40))
        adj.connect("changed", lambda a: self._at_bottom and a.set_value(a.get_upper() - a.get_page_size()))
        inner.pack_start(scroll, True, True, 0)

        self.entry = Gtk.Entry(placeholder_text="Type to JARVIS...")
        self.entry.connect("activate", self._entry_send)
        self.entry.connect("changed", lambda _e: setattr(self, "_quiet_since", None))  # typing counts
        inner.pack_start(self.entry, False, False, 0)
        w.add(box)
        w.connect("key-press-event", self._panel_key)
        w.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        w.connect("button-press-event", self._take_focus)
        w.connect("map-event", lambda *_: GLib.idle_add(self._take_focus))

    def _panel_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            return self._set_chat(False)
        if ev.keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and self.entry.has_focus():
            return self._recall(-1 if ev.keyval == Gdk.KEY_Up else 1)
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and ev.keyval in (Gdk.KEY_c, Gdk.KEY_C) and not self.entry.get_selection_bounds():
            # The entry keeps the focus, so Ctrl+C would copy nothing: copy what's selected in the chat,
            # or with Shift (or nothing selected) the whole chat.
            picked = [lbl.get_text()[min(b):max(b)] for lbl in self._chat_labels()
                      for b in [lbl.get_selection_bounds()] if b and b[0] != b[1]]
            text = "\n".join(picked) if picked and not ev.state & Gdk.ModifierType.SHIFT_MASK else self._chat_text()
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
            return True
        return False

    def _recall(self, step):
        """Up/Down in the typing box walk through what you sent before, like a terminal; Down past the newest clears it."""
        sent = getattr(self, "_sent", [])
        if not sent:
            return True
        at = getattr(self, "_sent_at", None)
        at = (len(sent) if at is None else at) + step
        if at >= len(sent):
            self._sent_at = None
            self.entry.set_text("")
            return True
        self._sent_at = max(0, at)
        self.entry.set_text(sent[self._sent_at])
        self.entry.set_position(-1)
        return True

    def _chat_labels(self):
        found = []

        def walk(widget):
            if isinstance(widget, Gtk.Label) and hasattr(widget, "chat_who"):
                found.append(widget)
            elif isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    walk(child)
        walk(self.msgs)
        return found

    def _chat_text(self):
        names = {"you": "You", "heard": "You", "jarvis": "JARVIS", "claude": "Claude"}
        return "\n".join("%s: %s" % (names[l.chat_who], l.get_text()) if l.chat_who in names else l.get_text()
                         for l in self._chat_labels())

    def _label_menu(self, label, menu):
        """Right-click a message: besides Copy, copy the whole conversation."""
        item = Gtk.MenuItem(label="Copy whole chat")
        item.connect("activate", lambda _i: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self._chat_text(), -1))
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(item)
        menu.show_all()

    def _take_focus(self, _w=None, event=None):
        """A dock window never gets keyboard focus from the window manager by itself: after you've clicked
        another window, clicking the chat (or opening it) has to ask for it, or typing and editing do nothing."""
        win = self.panel.get_window()
        if win:
            win.focus(event.time if event else Gdk.CURRENT_TIME)
        if not self.entry.has_focus():
            self.entry.grab_focus_without_selecting()
        return False

    def _set_chat(self, on):
        on = (not self.panel.get_visible()) if on is None else on
        self._chat_on = on
        self.card._place()  # the chat and the cards share one centred row
        if on:
            self._quiet_since = None  # full time on screen
            self.panel.show_all()
            self.panel.present()
            self.entry.grab_focus()
            self.bar.ignored = 0  # you're looking at them now
        else:
            self.panel.hide()
        self.bar.poke()  # its arrow points the other way now
        if hasattr(self, "chat_item"):
            self.chat_item.handler_block_by_func(self._menu_chat)
            self.chat_item.set_active(on)
            self.chat_item.handler_unblock_by_func(self._menu_chat)
        return False

    def _set_orb(self, on):
        """Show/hide the orb by hand; this switches it out of auto mode until restart."""
        self.orb_mode = bool((not self.bar.win.get_visible()) if on is None else on)
        self._refresh()
        return False

    def long_claude(self):
        """How many seconds Claude has been at it, once that's long enough to give the dock back; else 0."""
        spent = time.time() - self._claude_since if self._claude_since else 0
        return spent if spent > LONG_CLAUDE else 0

    def _set_understood(self, on):
        on = (not self.bar.show_understood) if on is None else bool(on)
        self.bar.show_understood = on
        self.bar.poke()
        self._save_pref("understood", on)
        if self.understood_item.get_active() != on:
            self.understood_item.handler_block_by_func(self._menu_understood)
            self.understood_item.set_active(on)
            self.understood_item.handler_unblock_by_func(self._menu_understood)
        return False

    def _touching(self):
        """The pointer is on the chat or the card, or there's half-typed text in the chat."""
        if self.panel.get_visible() and self.entry.get_text():
            return True
        _screen, px, py = Gdk.Display.get_default().get_default_seat().get_pointer().get_position()
        for w in [self.panel] + self.card.windows():
            gw = w.get_window()
            if w.get_visible() and gw:
                _ok, x, y = gw.get_origin()
                if x <= px < x + gw.get_width() and y <= py < y + gw.get_height():
                    return True
        return False

    def _auto_hide(self, active):
        """While JARVIS is busy or you're on the chat or card, nothing hides and every clock stops. The chat hides
        after hide_after quiet seconds, and so does each card, counted from when it opened (or last updated), so
        the earliest goes first. The bar stays full-size while JARVIS is busy, the chat is open or you're on it, and
        orb_hide_after seconds more; then it shrinks to the orb in the corner while a card is up, or goes. Pinned
        copies stay."""
        chat, card, now = self.panel.get_visible(), self.card.visible, time.time()
        hovered = bool(self.bar.hover)
        if active or chat or hovered and self.bar.mode == "full":  # hovering the corner bar mustn't pull it out; a click does
            self._busy_at = now
        if active or hovered or self._touching() or not (chat or card):
            self._quiet_since = self._ticked = None
        else:
            self._quiet_since = self._quiet_since or now
            step, self._ticked = now - (self._ticked or now), now
            if card:
                self.card.age(step, self.hide_after)
                card = self.card.visible
            if chat and now - self._quiet_since >= self.hide_after:
                self._set_chat(False)
                chat = False
        mode = bar_mode(self.long_claude(), active, chat, now - self._busy_at < self.orb_hide_after, card,
                        self.orb_mode is True, self.orb_mode == "always")
        self.bar.set_mode("hidden" if self.orb_mode is False else mode)

    def _entry_send(self, entry):
        text = entry.get_text().strip()
        entry.set_text("")
        self._sent_at = None
        if text:
            self._sent = [t for t in getattr(self, "_sent", []) if t != text][-49:] + [text]
            self.on_text(text)

    def _add_chat(self, who, text):
        kind = who if who in ("you", "jarvis", "claude", "heard") else "sys"
        label = Gtk.Label(label=text, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          selectable=True, can_focus=False)
        label.chat_who = kind
        label.connect("populate-popup", self._label_menu)
        if kind in ("heard", "sys"):
            label.set_max_width_chars(60)
            label.get_style_context().add_class(kind)
            if kind == "heard":  # what you said, so it sits on your side
                label.set_xalign(1)
                label.set_halign(Gtk.Align.END)
            label.set_margin_start(4)
            label.set_margin_end(4)
            row = label
        else:
            label.set_max_width_chars(32)
            bubble = Gtk.Box()
            bubble.get_style_context().add_class("bubble")
            bubble.get_style_context().add_class(kind)
            bubble.pack_start(label, False, False, 0)
            bubble.set_halign(Gtk.Align.END if kind == "you" else Gtk.Align.START)
            row = bubble
            if kind == "claude" and self._last_kind != "claude":  # name it once per run of Claude's lines
                row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                sender = Gtk.Label(label="\u273b CLAUDE", xalign=0)
                sender.get_style_context().add_class("sender")
                sender.set_margin_start(6)
                row.pack_start(sender, False, False, 0)
                row.pack_start(bubble, False, False, 0)
        if kind == "you":
            self._at_bottom = True
        if kind != "heard":
            self._last_kind = kind
        self.msgs.pack_start(row, False, False, 0)
        row.show_all()
        old = self.msgs.get_children()
        for child in old[:max(0, len(old) - CHAT_KEEP)]:
            child.destroy()
        return False

    # --- tray icon ---

    def _build_tray(self):
        icon_dir = os.path.join(RUN_DIR, "icons")
        os.makedirs(icon_dir, exist_ok=True)
        for name, v in ICONS.items():
            for silent in (False, True):
                for deaf in (False, True):
                    look = dict(v, **(DEAF if deaf else {}))
                    look["slash"] = SLASH.format(ring=look["ring"]) if silent else ""
                    with open(os.path.join(icon_dir, _icon_name(name, silent, deaf) + ".svg"), "w") as f:
                        f.write(ICON_SVG.format(**look))
        ind = self.tray = AppIndicator3.Indicator.new("jarvis", "jarvis-idle",
                                                      AppIndicator3.IndicatorCategory.APPLICATION_STATUS)
        ind.set_icon_theme_path(icon_dir)
        ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        ind.set_title("JARVIS")

        m = self.menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="JARVIS: Starting…")
        self.status_item.set_sensitive(False)
        m.append(self.status_item)
        m.append(Gtk.SeparatorMenuItem())
        self.chat_item = Gtk.CheckMenuItem(label="Show chat")
        self.chat_item.connect("toggled", self._menu_chat)
        m.append(self.chat_item)
        bar_menu, group = Gtk.Menu(), None
        for key, label in (("auto", "Auto: shows while you talk"), ("always", "Always on: back to the corner when idle"),
                           ("compact", "Compact: always in the corner")):
            item = Gtk.RadioMenuItem(label=label, group=group)
            group = group or item
            item.set_active(self.orb_mode == {"compact": True}.get(key, key))
            item.connect("toggled", self._menu_orb, key)
            bar_menu.append(item)
        bar_item = Gtk.MenuItem(label="JARVIS bar")
        bar_item.set_submenu(bar_menu)
        m.append(bar_item)
        self.understood_item = Gtk.CheckMenuItem(label="Show what JARVIS understood")
        self.understood_item.set_active(self.bar.show_understood)
        self.understood_item.connect("toggled", self._menu_understood)
        m.append(self.understood_item)
        m.append(Gtk.SeparatorMenuItem())
        # Three separate mutes. Unmuting the microphone can only happen here: once it's muted JARVIS is deaf,
        # so no spoken command could ever reach it. Each box sends /mute <what> on|off, which never asks.
        self.mute_items = {}
        for key, label in (("voice", "Mute JARVIS's voice"), ("speaker", "Mute speaker"),
                           ("mic", "Stop listening")):
            item = Gtk.CheckMenuItem(label=label)
            item.connect("toggled", self._menu_mute)
            self.mute_items[key] = item
            m.append(item)
        m.append(Gtk.SeparatorMenuItem())
        sys_item = Gtk.CheckMenuItem(label="Work without Claude")
        sys_item.connect("toggled", lambda i: self.on_command("/sys" if i.get_active() else "/agent"))
        m.append(sys_item)
        for label, command in (("Open Claude in Terminal", "open claude"),
                               ("Open Claude in VS Code", "open claude in vs code"),
                               ("Go to JARVIS workspace", "/desk")):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, c=command: self.on_command(c))
            m.append(item)
        m.append(Gtk.SeparatorMenuItem())
        restart_item = Gtk.MenuItem(label="Restart JARVIS")
        restart_item.connect("activate", lambda _i: self.on_command("restart yourself"))
        m.append(restart_item)
        quit_item = Gtk.MenuItem(label="Quit JARVIS")
        quit_item.connect("activate", lambda _i: self.on_command("/quit"))
        m.append(quit_item)
        m.show_all()
        ind.set_menu(m)

    def _menu_orb(self, item, key):
        if not item.get_active():
            return  # the choice being left; the new one sends its own toggled
        self.orb_mode = {"compact": True}.get(key, key)
        self._prefs.pop("compact", None)
        self._save_pref("bar", key)
        self._refresh()

    def _menu_chat(self, item):
        self._set_chat(item.get_active())

    def _menu_understood(self, item):
        self._set_understood(item.get_active())

    def _menu_mute(self, item):
        what = next(k for k, i in self.mute_items.items() if i is item)
        self.mutes[what] = item.get_active()
        self._refresh()
        self.on_command("/mute %s %s" % ({"speaker": "sound"}.get(what, what),
                                         "on" if item.get_active() else "off"))

    def _set_mutes(self, voice, mic, speaker):
        for key, value in (("voice", voice), ("mic", mic), ("speaker", speaker)):
            if value is not None:
                self.mutes[key] = bool(value)
        for key, item in self.mute_items.items():
            if item.get_active() != self.mutes[key]:
                item.handler_block_by_func(self._menu_mute)
                item.set_active(self.mutes[key])
                item.handler_unblock_by_func(self._menu_mute)
        self._refresh()
        return False

    # --- state ---

    def _set_state(self, state):
        self.state = state
        self._claude_since = (self._claude_since or time.time()) if state == "claude" else None
        self._refresh()
        return False

    def _refresh(self):
        shown = "speaking" if self.state == "idle" and self.is_speaking() else self.state
        self._auto_hide(shown in ANIMATED and shown != "starting")
        silent, deaf = self.mutes["voice"] or self.mutes["speaker"], self.mutes["mic"]
        key = (shown, silent, deaf)
        if key != self._shown:
            self._shown = key
            muted = ", ".join(MUTED[k] for k in ("voice", "speaker", "mic") if self.mutes[k])
            label = LABELS.get(shown, shown) + (" (%s)" % muted if muted else "")
            self.tray.set_icon_full(_icon_name(shown, silent, deaf), label)
            self.status_item.set_label("JARVIS: %s" % label)
            self.title.set_text(LABELS.get(shown, shown))
            dot = self.dot.get_style_context()
            dot.add_class("live") if shown in ANIMATED else dot.remove_class("live")
            self.bar.poke()
        return True

    @staticmethod
    def _load_prefs():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_pref(self, key, value):
        self._prefs[key] = value
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self._prefs, f)
        except OSError:
            pass
