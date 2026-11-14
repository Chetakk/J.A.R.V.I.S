"""Drawn blocks for cards: a fenced block whose kind is one of KINDS becomes a picture instead of text.

    ```ring        CPU: 34%  /  Memory: 5.1/7.4 GB  /  Disk: 92% bad         (a row of rings)
    ```countdown   end: <unix time>  total: <seconds>  [label: pasta]          (empties by itself; pulses at the end)
    ```spark       CPU: 12 30 25 41 | %                                        (a trend line per row, last value marked)
    ```stack       Disk: used 84 bad, free 28 | GB                             (one split bar per row)
    ```donut       Projects: 40  /  Downloads: 12  /  unit: GB                 (parts of a whole, with a legend)
    ```steps       done: Open VS Code | opened  /  now: Start the server  /  todo: Open it   (failed:, skipped:)
    ```timeline    9:30 Call the bank  /  10:00-10:15 Stand-up  /  free: 10:15-11:30 1 h   (today's axis, "now" moves)
    ```heat        2026-10-31: 3.5  /  2026-11-01: 1                           (a GitHub-style grid of days)
    ```path        Computer: ok  /  Wi-Fi: ok Home  /  Router: bad no reply    (linked boxes; check pulses)
    ```screens     HDMI-0: 1920x1080 primary                                   (monitors drawn to scale)
    ```layout      = coding  /  code: left  /  chrome: right                   (mini screens with window boxes)
    ```weather     now: 2 27 29  /  Sun 1: 61 19 26 70                         (WMO code, low, high, rain %)
    ```map         12.97, 77.59: Bengaluru                                     (a pin on an offline outline map)
    ```clock       Tokyo: +0900  /  Here                                       (analog faces that tick)
    ```gauge       Download: 42 / 100 Mbps                                     (a needle on a half circle)
    ```progress    Song: 63/215 playing                                        (moves by itself while playing;
                   Meeting: 3500/3600 playing over: the time keeps counting past the end)
    ```music       title: X  /  artist: Y  /  album: Z  /  art: cover.jpg  /  time: 63/215 playing   (now playing)
    ```log / ```git                                                            (text, errors red / changes coloured)

A trailing ok / warn / bad on a ring, stack part or path node colours it. In any text, {ok} {warn} {bad} {off}
{check} are coloured dots and {file:pdf} a file-type badge (see markup()). Status colours are the only colour on a
card; the rest stays white on glass. Animated blocks redraw only while their card is on screen (Card._tick).
"""
import datetime
import json
import math
import os
import re
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

FACE_AT, FACE_IN = 0.0, 0.3  # when a card with the orb last opened, and how long its orb takes to grow in
ORB = None  # the bar's orb painter (cr, w, h, at=(x, y) on screen), set by ui/window.py: a card's ```hero draws the same live orb
W = 432  # a card's drawing width (CARD_W less its padding)
# ok = frosted white (free / done), warn = crimson #CC0033 (needs you), bad = midnight indigo #0B0F19
# (cancelled / overdue), check = electric cyan #00F2FE (now / new). A 4th value is the colour's own opacity.
STATUS = {"ok": (1.0, 1.0, 1.0, 0.15), "warn": (0.80, 0.0, 0.20), "bad": (0.043, 0.059, 0.098),
          "off": (0.55, 0.55, 0.55), "check": (0.0, 0.949, 0.996)}
SUN = (0.96, 0.74, 0.33)  # the weather sun and lightning keep their yellow: pictures, not a status
HEX = {k: "#%02x%02x%02x" % tuple(round(c * 255) for c in v[:3]) for k, v in STATUS.items()}
FG = {k: 'foreground="%s"' % HEX[k] + (' fgalpha="%d%%"' % round(v[3] * 100) if len(v) > 3 else "")
      for k, v in STATUS.items()}  # Pango markup for text in that colour
# in text, the two colours that vanish on dark glass: a white ring for free / done, a ringed dot on an indigo chip for
# cancelled / overdue, and log lines in them white (an error one bold, with a wavy underline)
DOTS = {k: '<span %s>●</span>' % FG[k] for k in STATUS}
DOTS.update(ok='<span foreground="#ffffff" fgalpha="80%">○</span>',
            bad='<span foreground="#ffffff" fgalpha="70%" background="#0b0f19">◉</span>')
LINE = dict(FG, ok='foreground="#ffffff" weight="bold"',
            bad='foreground="#ffffff" weight="bold" underline="error" underline_color="#ffffff"')
SHADES = (0.92, 0.62, 0.42, 0.30, 0.22, 0.16)  # white at these strengths, for parts of a whole
FAINT = ("free", "left", "other", "idle", "rest", "unused", "available", "remaining")
FILES = {"pdf": "bad", "doc": "check", "docx": "check", "odt": "check", "txt": "off", "md": "off",
         "png": "ok", "jpg": "ok", "jpeg": "ok", "gif": "ok", "svg": "ok", "webp": "ok",
         "mp4": "warn", "mkv": "warn", "webm": "warn", "mov": "warn", "mp3": "warn", "wav": "warn", "flac": "warn",
         "zip": "off", "tar": "off", "gz": "off", "deb": "off", "iso": "off", "7z": "off", "xz": "off",
         "py": "check", "js": "check", "ts": "check", "json": "check", "yaml": "check", "sh": "check",
         "xlsx": "ok", "csv": "ok", "pptx": "warn", "sheet": "ok", "slides": "warn", "form": "check",
         "gdoc": "check"}
_WORLD = None
_EASE = {}  # (kind, label) -> (from, to, started): rings and gauges glide to a new value


def markup(s):
    """{ok} … {check} dots and {file:pdf} badges in already-escaped Pango markup."""
    s = re.sub(r"\{(ok|warn|bad|off|check)\}", lambda m: DOTS[m.group(1)], s)
    return re.sub(r"\{file:([A-Za-z0-9]{1,6})\}", lambda m: _badge(FILES.get(m.group(1).lower(), "off"), m.group(1)), s)


def _badge(state, ext):
    """A file-type badge: the extension on its status colour; white letters where dark ones wouldn't read."""
    v = STATUS[state]
    alpha = ' bgalpha="%d%%"' % round(v[3] * 100) if len(v) > 3 else ""
    ink = "#15171a" if sum(v[:3]) / 3 > 0.5 and len(v) == 3 else "#ffffff"
    return '<span background="%s"%s foreground="%s" size="small" weight="bold"> %s </span>' % (
        HEX[state], alpha, ink, ext.upper())


def strip(s):
    """The same tokens taken out, for reading aloud."""
    return re.sub(r"\{(?:ok|warn|bad|off|check|file:[A-Za-z0-9]{1,6})\}\s*", "", s)


def file_badge(name):
    """"{file:pdf} " for invoice.pdf, "" for a name without a known extension."""
    ext = os.path.splitext(name)[1][1:].lower()
    return "{file:%s} " % ext if ext in FILES else ""


# --- making a block ---

def make(kind, lines):
    """A widget for a fenced block of `kind`, or None (shown as plain monospace text then)."""
    lines = [l.rstrip() for l in lines if l.strip()]
    if kind in ("log", "git"):
        return _coloured(kind, lines)
    maker = KINDS.get(kind)
    if not maker or not lines:
        return None
    try:
        spec = maker(lines)
    except (ValueError, IndexError, KeyError, ZeroDivisionError):
        return None
    if not spec:
        return None
    height, draw, every, *hit = spec
    area = Gtk.DrawingArea()
    area.set_size_request(W, int(height))
    area.every = every  # seconds between redraws while on screen (None: still)
    if hit:  # hit(x, y, width) -> the phrase a click there says, or None (the music card's heart)
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        area.hit = lambda x, y: hit[0](x, y, area.get_allocated_width() or W)
    area.connect("draw", lambda widget, cr: _safe(draw, widget, cr))
    return area


def _safe(draw, widget, cr):
    try:
        draw(cr, widget.get_allocated_width() or W)
    except Exception:  # a bad value draws nothing rather than breaking the card
        pass
    return False


def _coloured(kind, lines):
    out = []
    for line in lines:
        esc = GLib.markup_escape_text(line)
        state = None
        if kind == "log":
            if re.search(r"error|exception|traceback|fatal|failed|refused|denied|eaddrinuse|not found", line, re.I):
                state = "bad"
            elif re.search(r"warn|deprecat|retry", line, re.I):
                state = "warn"
        else:
            code = line[:2]
            state = {"??": "ok", "A ": "ok", " A": "ok", " M": "warn", "M ": "warn", "MM": "warn", " D": "bad",
                     "D ": "bad", "R ": "check", "UU": "bad"}.get(code)
            if line.startswith("##"):
                esc = '<span alpha="60%%">%s</span>' % esc
        out.append('<span %s>%s</span>' % (LINE[state], esc) if state else esc)
    lab = Gtk.Label(xalign=0, selectable=True)
    lab.set_markup("\n".join(out))
    lab.get_style_context().add_class("mono")
    return lab


# --- drawing helpers ---

def _rgb(cr, rgb, a=1.0):
    cr.set_source_rgba(rgb[0], rgb[1], rgb[2], a * (rgb[3] if len(rgb) > 3 else 1))


def _edged(rgb):
    """Frosted white and midnight indigo all but vanish on dark glass: their marks get a thin white edge."""
    return rgb is not None and (tuple(rgb) == tuple(STATUS["ok"]) or tuple(rgb) == tuple(STATUS["bad"]))


def _mark(cr, rgb, a=1.0, fill=False):
    """Stroke (or fill) the current path in a status colour, or white (rgb None) at `a`. Frosted white and indigo
    marks get a white edge just outside them: drawn in a group with the mark's own shape cleared, so the edge
    doesn't show through the see-through frost."""
    if not _edged(rgb):
        _rgb(cr, rgb, a) if rgb else _white(cr, a)
        cr.fill() if fill else cr.stroke()
        return
    path, lw = cr.copy_path(), cr.get_line_width()
    cr.push_group()
    cr.append_path(path)
    _white(cr, 0.6)
    cr.set_line_width(lw + 2.4 if not fill else 2.4)
    cr.stroke_preserve()
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.set_line_width(lw)
    cr.fill_preserve() if fill else cr.stroke_preserve()
    cr.new_path()
    cr.pop_group_to_source()
    cr.paint()
    cr.append_path(path)
    cr.set_line_width(lw)
    _rgb(cr, rgb, a)
    cr.fill() if fill else cr.stroke()


def _white(cr, a=1.0):
    cr.set_source_rgba(1, 1, 1, a)


def _text(cr, x, y, s, size=9.5, a=0.9, bold=False, align="left", width=None, rgb=None, mono=False):
    """Text with its top-left (or top-centre / top-right) at x, y; ellipsized to `width`. Returns (w, h)."""
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription("%s %s %g" % ("Monospace" if mono else "Sans", "Bold" if bold else "", size))
    layout.set_font_description(font)
    layout.set_text(str(s), -1)
    if width:
        layout.set_width(int(width * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    w, h = layout.get_pixel_size()
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    if rgb and not _edged(rgb):
        _rgb(cr, rgb, a)
    else:
        _white(cr, a)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)
    cr.new_path()  # no current point left behind: the next shape mustn't start with a line from here
    return w, h


def _round_rect(cr, x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _pill(cr, x, y, w, h):
    _round_rect(cr, x, y, w, h, h / 2)


def _num(s):
    return float(s.replace(",", ""))


def _fmt(v):
    return ("%.1f" % v).rstrip("0").rstrip(".") if abs(v) < 100 else "%d" % round(v)


def _state(words):
    """A trailing ok/warn/bad/check/off word: (state, the rest)."""
    parts = words.strip().rsplit(" ", 1)
    if parts[-1].lower() in STATUS:
        return parts[-1].lower(), (parts[0] if len(parts) > 1 else "")
    return None, words.strip()


def _split(line):
    """"Label: rest" -> (label, rest)."""
    label, _, rest = line.partition(":")
    return label.strip(), rest.strip()


def _eased(key, target, secs=0.6):
    """The value to draw now for `key` gliding to `target`, and whether it's still moving."""
    now = time.time()
    old = _EASE.get(key)
    if not old:
        _EASE[key] = (target, target, now)
        return target, False
    start, end, t0 = old
    if end != target:  # a new value: glide from where it is now
        current = start + (end - start) * _ease_out(min(1, (now - t0) / secs))
        _EASE[key] = (current, target, now)
        return current, True
    p = min(1, (now - t0) / secs)
    return start + (end - start) * _ease_out(p), p < 1


def _ease_out(p):
    return 1 - (1 - p) ** 3


def _clock(seconds):
    seconds = max(0, int(math.ceil(seconds)))
    h, rest = divmod(seconds, 3600)
    return ("%d:%02d:%02d" % (h, rest // 60, rest % 60)) if h else "%d:%02d" % divmod(rest, 60)


# --- the blocks: each returns (height, draw(cr, width), seconds between redraws or None) ---

def _ring(lines):
    rings = []
    for line in lines[:8]:
        label, rest = _split(line)
        state, rest = _state(rest)
        m = re.match(r"(-?[\d.,]+)\s*(?:/\s*([\d.,]+))?\s*(%|\S+)?", rest)
        value, top, unit = _num(m.group(1)), m.group(2), m.group(3) or ""
        frac = value / _num(top) if top else value / 100.0
        shown = "%s%s" % (_fmt(value), "%" if unit == "%" or not top and not unit else " " + unit if unit else "")
        rings.append((label, max(0.0, min(1.0, frac)), shown, state))
    per = min(4, len(rings))
    rows = math.ceil(len(rings) / per)
    d = min(96, (W - 16 * (per - 1)) / per)
    moving = {}

    def draw(cr, width):
        cell = width / per
        for i, (label, frac, shown, state) in enumerate(rings):
            cx = cell * (i % per) + cell / 2
            cy = (i // per) * (d + 30) + d / 2 + 2
            r = d / 2 - 6
            f, moving[i] = _eased(("ring", label), frac)
            cr.new_path()
            cr.set_line_width(8)
            cr.set_line_cap(1)  # round
            _white(cr, 0.10)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.stroke()
            if f > 0.003:
                cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * f)
                _mark(cr, STATUS[state] if state else None, 1 if state else 0.9)
            _text(cr, cx, cy - 10, shown, size=11 if len(shown) < 6 else 9, bold=True, align="center")
            _text(cr, cx, cy + d / 2 + 4, label, size=9, a=0.6, align="center", width=cell - 6)
    return rows * (d + 30), draw, 0.05


def _countdown(lines):
    spec = dict(_split(l) for l in lines)
    end, total = float(spec["end"]), max(1.0, float(spec.get("total") or 1))
    label = spec.get("label", "")

    def draw(cr, width):
        left = end - time.time()
        cx, cy, r = width / 2, 78, 64
        cr.new_path()
        cr.set_line_width(10)
        cr.set_line_cap(1)
        _white(cr, 0.10)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        if left <= 0:
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            _mark(cr, STATUS["ok"])
            _text(cr, cx, cy - 16, "Done ✓", size=18, bold=True, align="center", rgb=STATUS["ok"])
        else:
            frac = left / total
            pulse = 0.55 + 0.45 * abs(math.sin(time.time() * math.pi)) if left <= 10 else 1.0
            _rgb(cr, STATUS["warn"], pulse) if left <= 10 else _white(cr, 0.92)
            cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * frac)
            cr.stroke()
            _text(cr, cx, cy - 18, _clock(left), size=20, bold=True, align="center")
        if label:
            _text(cr, cx, cy + 14, label, size=9, a=0.6, align="center", width=2 * r - 20)
    return 160, draw, 0.25


def _spark(lines):
    rows = []
    for line in lines[:6]:
        label, rest = _split(line)
        values, _, unit = rest.partition("|")
        nums = [_num(v) for v in values.split()]
        if nums:
            rows.append((label, nums, unit.strip()))

    def draw(cr, width):
        for i, (label, nums, unit) in enumerate(rows):
            y0 = i * 40
            _text(cr, 0, y0 + 10, label, size=9.5, a=0.8, width=96)
            x0, x1, top, bottom = 104, width - 64, y0 + 6, y0 + 34
            lo, hi = min(nums + [0]), max(nums) or 1
            span = (hi - lo) or 1
            pts = [(x0 + (x1 - x0) * (j / max(1, len(nums) - 1)), bottom - (v - lo) / span * (bottom - top))
                   for j, v in enumerate(nums)]
            cr.move_to(pts[0][0], bottom)
            for x, y in pts:
                cr.line_to(x, y)
            cr.line_to(pts[-1][0], bottom)
            cr.close_path()
            _white(cr, 0.08)
            cr.fill()
            cr.set_line_width(1.6)
            cr.move_to(*pts[0])
            for x, y in pts[1:]:
                cr.line_to(x, y)
            _white(cr, 0.85)
            cr.stroke()
            cr.arc(pts[-1][0], pts[-1][1], 3, 0, 2 * math.pi)
            cr.fill()
            _text(cr, width, y0 + 10, "%s%s" % (_fmt(nums[-1]), (" " + unit) if unit and unit != "%" else unit),
                  size=9.5, bold=True, align="right")
    return 40 * len(rows), draw, None


def _parts(text):
    """"used 84 bad, free 28" -> [(name, value, state)]."""
    out = []
    for chunk in text.split(","):
        state, chunk = _state(chunk)
        m = re.match(r"(.*?)\s*(-?[\d.,]+)\s*$", chunk.strip())
        if m:
            out.append((m.group(1).strip() or "part", _num(m.group(2)), state))
    return out


def _shade(i, name, state):
    if state:
        return STATUS[state], 0.9
    if name.lower() in FAINT:
        return (1, 1, 1), 0.12
    return (1, 1, 1), SHADES[i % len(SHADES)]


def _stack(lines):
    rows = []
    for line in lines[:5]:
        label, rest = _split(line)
        rest, _, unit = rest.partition("|")
        parts = _parts(rest)
        if parts:
            rows.append((label, parts, unit.strip()))

    def draw(cr, width):
        for i, (label, parts, unit) in enumerate(rows):
            y0 = i * 58
            total = sum(v for _n, v, _s in parts) or 1
            _text(cr, 0, y0, label, size=9.5, bold=True, a=0.9, width=width - 110)
            _text(cr, width, y0, "%s %s" % (_fmt(total), unit), size=9, a=0.6, align="right")
            x, bar_y = 0, y0 + 20
            _white(cr, 0.08)
            _pill(cr, 0, bar_y, width, 12)
            cr.fill()
            cr.save()
            _pill(cr, 0, bar_y, width, 12)
            cr.clip()
            for j, (name, v, state) in enumerate(parts):
                w = width * v / total
                rgb, a = _shade(j, name, state)
                cr.rectangle(x, bar_y, w, 12)
                _mark(cr, rgb, a, fill=True)
                x += w
            cr.restore()
            lx = 0
            for j, (name, v, state) in enumerate(parts):
                rgb, a = _shade(j, name, state)
                cr.rectangle(lx, bar_y + 22, 8, 8)
                _mark(cr, rgb, a if _edged(rgb) else max(a, 0.3), fill=True)
                w, _h = _text(cr, lx + 12, bar_y + 18, "%s %s" % (name, _fmt(v)), size=8.5, a=0.7)
                lx += w + 26
    return 58 * len(rows), draw, None


def _donut(lines):
    unit, parts = "", []
    for line in lines[:8]:
        label, rest = _split(line)
        if label.lower() == "unit":
            unit = rest
            continue
        state, rest = _state(rest)
        parts.append((label, _num(rest.split()[0]), state))
    total = sum(v for _l, v, _s in parts) or 1

    def draw(cr, width):
        cx, cy, r = 64, 64, 54
        a0 = -math.pi / 2
        cr.new_path()
        cr.set_line_width(20)
        for j, (label, v, state) in enumerate(parts):
            a1 = a0 + 2 * math.pi * v / total
            rgb, a = _shade(j, label, state)
            cr.arc(cx, cy, r - 10, a0, max(a0, a1 - 0.02))
            _mark(cr, rgb, a)
            a0 = a1
        _text(cr, cx, cy - 16, _fmt(total), size=13, bold=True, align="center")
        _text(cr, cx, cy + 2, unit, size=8.5, a=0.6, align="center")
        for j, (label, v, state) in enumerate(parts):
            y = 6 + j * 19
            rgb, a = _shade(j, label, state)
            cr.rectangle(140, y + 4, 9, 9)
            _mark(cr, rgb, a if _edged(rgb) else max(a, 0.3), fill=True)
            _text(cr, 156, y, label, size=9.5, a=0.85, width=width - 156 - 92)
            _text(cr, width, y, "%s %s · %d%%" % (_fmt(v), unit, round(100 * v / total)), size=9, a=0.6,
                  align="right")
    return max(132, 19 * len(parts) + 12), draw, None


def _steps(lines):
    steps = []
    for line in lines[:16]:
        state, rest = _split(line)
        text, _, detail = rest.partition("|")
        steps.append((state.lower(), text.strip(), detail.strip()))
    heights = [26 + (16 if d else 0) for _s, _t, d in steps]
    live = any(s == "now" for s, _t, _d in steps)

    def draw(cr, width):
        y = 0
        centres = []
        for (state, text, detail), h in zip(steps, heights):
            centres.append(y + 10)
            y += h
        cr.set_line_width(2)
        for i in range(len(centres) - 1):
            _white(cr, 0.35 if steps[i][0] == "done" else 0.12)
            cr.move_to(9, centres[i] + 7)
            cr.line_to(9, centres[i + 1] - 7)
            cr.stroke()
        y = 0
        for (state, text, detail), h, c in zip(steps, heights, centres):
            if state == "done":
                cr.arc(9, c, 6, 0, 2 * math.pi)
                _mark(cr, STATUS["ok"], fill=True)
                _white(cr, 0.95) if _edged(STATUS["ok"]) else cr.set_source_rgba(0.08, 0.09, 0.1, 1)
                cr.set_line_width(1.8)
                cr.move_to(6, c)
                cr.line_to(8.4, c + 2.6)
                cr.line_to(12.2, c - 2.6)
                cr.stroke()
            elif state == "now":
                pulse = 0.45 + 0.55 * abs(math.sin(time.time() * 2.2))
                _rgb(cr, STATUS["check"], pulse * 0.35)
                cr.arc(9, c, 9, 0, 2 * math.pi)
                cr.fill()
                _rgb(cr, STATUS["check"])
                cr.arc(9, c, 5, 0, 2 * math.pi)
                cr.fill()
            elif state in ("failed", "bad"):
                cr.arc(9, c, 6, 0, 2 * math.pi)
                _mark(cr, STATUS["bad"], fill=True)
                _white(cr, 0.95) if _edged(STATUS["bad"]) else cr.set_source_rgba(0.08, 0.09, 0.1, 1)
                cr.set_line_width(1.8)
                for dx in (-1, 1):
                    cr.move_to(9 - 2.6, c - 2.6 * dx)
                    cr.line_to(9 + 2.6, c + 2.6 * dx)
                cr.stroke()
            else:
                _white(cr, 0.35 if state != "skipped" else 0.18)
                cr.set_line_width(1.6)
                cr.arc(9, c, 5.5, 0, 2 * math.pi)
                cr.stroke()
            a = 0.95 if state == "now" else 0.5 if state in ("todo", "skipped") else 0.85
            _text(cr, 26, y + 2, text, size=10, a=a, bold=state == "now", width=width - 30)
            if detail:
                _text(cr, 26, y + 20, detail, size=8.5, a=0.5, width=width - 30)
            y += h
    return sum(heights), draw, 0.1 if live else None


def _hours(s):
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s.strip(), re.I)
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h + mi / 60.0, m.end()


def _timeline(lines):
    """"9:30 Call the bank" (a moment), "10:00-10:15 Stand-up" (a meeting, drawn as long as it runs) and
    "free: 10:15-11:30 1 h" (a free stretch, a dashed frosted band) on today's axis, with a moving "now" line
    ("now: off" for a day that isn't today)."""
    events, free = [], []
    show_now = not any(re.match(r"now:\s*off", l, re.I) for l in lines)
    lines = [l for l in lines if not re.match(r"now:", l, re.I)]
    for line in lines[:14]:
        is_free = line.lower().startswith("free:")
        line = line[5:].strip() if is_free else line
        at, end = _hours(line)
        rest = line[end:]
        until = None
        m = re.match(r"\s*[-–]\s*(?=\d)", rest)
        if m:
            until, end2 = _hours(rest[m.end():])
            rest = rest[m.end() + end2:]
        if is_free and until is not None:
            free.append((at, until, rest.strip(" -·")))
        elif not is_free:
            events.append((at, until, rest.strip(" -·")))
    now_h = lambda: (lambda t: t.tm_hour + t.tm_min / 60.0)(time.localtime())  # noqa: E731
    marks = [e[0] for e in events] + [e[1] for e in events if e[1] is not None] + [f[0] for f in free] + \
        [f[1] for f in free] + ([now_h()] if show_now else [])
    lo, hi = math.floor(min(marks) - 0.5), math.ceil(max(marks) + 0.5)
    if hi - lo < 4:
        hi = lo + 4
    lo, hi = max(0, lo), min(24, hi)

    def x_of(h, width):
        return 8 + (width - 16) * (h - lo) / (hi - lo)

    rows = []  # label rows, so labels don't run into each other
    placed = []
    for at, until, text in sorted(events, key=lambda e: e[0]):
        x = x_of(at, W)
        wl = min(150, 7 * len(text) + 40)
        left = x - wl if W - x - 6 < min(wl, 90) else x  # near the right edge the label sits left of its line
        row = next((i for i, end in enumerate(rows) if end < left), None)
        if row is None:
            rows.append(0)
            row = len(rows) - 1
        rows[row] = left + wl
        placed.append((at, until, text, row, wl))

    def hm(h):
        hh = int(h) % 24
        return "%d:%02d" % ((hh - 1) % 12 + 1, round((h % 1) * 60))

    def draw(cr, width):
        axis = 26
        cr.set_line_width(1)  # labels start at axis + 22, leaving room for "now" just under the axis
        _white(cr, 0.18)
        cr.move_to(8, axis)
        cr.line_to(width - 8, axis)
        cr.stroke()
        step = 1 if hi - lo <= 8 else 2 if hi - lo <= 14 else 3
        for h in range(int(lo), int(hi) + 1, step):
            x = x_of(h, width)
            _white(cr, 0.25)
            cr.move_to(x, axis - 3)
            cr.line_to(x, axis + 3)
            cr.stroke()
            _text(cr, min(max(x, 12), width - 12), 4, "%d%s" % ((h - 1) % 12 + 1, "am" if h < 12 or h == 24 else "pm"),
                  size=8, a=0.5, align="center")
        for f0, f1, text in free:
            x0, x1 = x_of(f0, width) + 1, x_of(f1, width) - 1
            _rgb(cr, STATUS["ok"])  # a free stretch: thinner than a meeting, dashed, frosted
            _round_rect(cr, x0, axis - 3, max(4, x1 - x0), 6, 3)
            cr.fill_preserve()
            _white(cr, 0.45)
            cr.set_dash([3, 3])
            cr.stroke()
            cr.set_dash([])  # (its label, "1 h", is only read aloud: above the axis it hit the hour labels)
        now = now_h()
        for at, until, text, row, wl in placed:
            x = x_of(at, width)
            if until is None:
                _white(cr, 0.9)
                cr.arc(x, axis, 4, 0, 2 * math.pi)
                cr.fill()
            else:  # a meeting: a bar as long as it runs, faded once it's over
                _white(cr, 0.45 if until <= now else 0.9)
                _round_rect(cr, x, axis - 6, max(4, x_of(until, width) - x), 12, 4)
                cr.fill()
            if row >= 3:
                continue  # crowded: a mark on the axis, no label
            y = axis + 22 + row * 34
            _white(cr, 0.2)
            cr.move_to(x, axis + 6)
            cr.line_to(x, y)
            cr.stroke()
            room = width - x - 6
            right = room < min(wl, 90)  # too near the right edge: the label goes on the left of its line
            tx, align = (x - 4, "right") if right else (x + 4, "left")
            when = hm(at) + (" – " + hm(until) if until is not None else "")
            _text(cr, tx, y - 2, when, size=8, a=0.55, align=align)
            _text(cr, tx, y + 11, text, size=9, a=0.9, align=align, width=min(wl, x - 6 if right else room))
        nx = x_of(now, width)
        if show_now and 8 <= nx <= width - 8:
            _rgb(cr, STATUS["check"], 0.9)
            cr.set_line_width(1.5)
            cr.move_to(nx, axis - 9)
            cr.line_to(nx, axis + 9)
            cr.stroke()
            _text(cr, nx, axis + 9, "now", size=7.5, rgb=STATUS["check"], align="center")
    return 52 + 34 * min(3, max(1, len(rows))), draw, 30


def _heat(lines):
    days = {}
    for line in lines:
        label, rest = _split(line)
        days[datetime.date.fromisoformat(label)] = _num(rest.split()[0])
    last = max(days)
    first = min(days)
    if (last - first).days < 21:
        return _strip(days, first, last)
    first -= datetime.timedelta(days=first.weekday())  # weeks start on Monday
    weeks = (last - first).days // 7 + 1
    top = max(days.values()) or 1
    cell = min(14, (W - 24) / weeks - 3)

    def draw(cr, width):
        for i, name in enumerate("MTWTFSS"):
            if i % 2 == 0:
                _text(cr, 0, 16 + i * (cell + 3) - 1, name, size=7.5, a=0.45)
        month = None
        for w in range(weeks):
            for d in range(7):
                day = first + datetime.timedelta(days=w * 7 + d)
                if day > last:
                    continue
                x, y = 16 + w * (cell + 3), 16 + d * (cell + 3)
                v = days.get(day, 0)
                _white(cr, 0.07 if not v else 0.2 + 0.75 * v / top)
                _round_rect(cr, x, y, cell, cell, 2.5)
                cr.fill()
                if day.day <= 7 and day.month != month and d == 0:
                    month = day.month
                    _text(cr, x, 0, day.strftime("%b"), size=7.5, a=0.5)
    return 22 + 7 * (cell + 3), draw, None


def _strip(days, first, last):
    """Three weeks or less of ```heat: one row of day squares, brighter for busier days, the weekday under each and
    today's square ringed."""
    n = (last - first).days + 1
    top = max(days.values()) or 1
    gap = 4
    cell = min(26, (W - gap * (n - 1)) / n)
    today = datetime.date.today()

    def draw(cr, width):
        for i in range(n):
            day = first + datetime.timedelta(days=i)
            x, v = i * (cell + gap), days.get(day, 0)
            _white(cr, 0.07 if not v else 0.2 + 0.72 * v / top)
            _round_rect(cr, x, 0, cell, cell, 4)
            cr.fill()
            if day == today:
                _white(cr, 0.8)
                cr.set_line_width(1.2)
                _round_rect(cr, x - 1.5, -1.5, cell + 3, cell + 3, 5)
                cr.stroke()
            if n <= 14 or day.weekday() == 0:
                _text(cr, x + cell / 2, cell + 3, "MTWTFSS"[day.weekday()], size=7, a=0.45, align="center")
    return cell + 16, draw, None


def _path(lines):
    nodes = []
    for line in lines[:6]:
        label, rest = _split(line)
        state, _, detail = (rest + " ").partition(" ")
        if state.lower() not in STATUS:
            state, detail = "off", rest
        nodes.append((label, state.lower(), detail.strip()))
    live = any(s == "check" for _l, s, _d in nodes)

    def draw(cr, width):
        n = len(nodes)
        gap = 14
        bw = (width - gap * (n - 1)) / n
        for i, (label, state, detail) in enumerate(nodes):
            x = i * (bw + gap)
            if i:
                ok = nodes[i - 1][1] == "ok" and state == "ok"
                cr.set_line_width(2)
                cr.move_to(x - gap + 2, 26)
                cr.line_to(x - 2, 26)
                _mark(cr, STATUS["ok"], 0.6) if ok else _mark(cr, None, 0.15)
            _white(cr, 0.06)
            _round_rect(cr, x, 4, bw, 44, 9)
            cr.fill()
            a = 0.5 + 0.5 * abs(math.sin(time.time() * 3)) if state == "check" else 1
            if state == "bad":
                cr.set_line_width(1.2)
                _round_rect(cr, x, 4, bw, 44, 9)
                _mark(cr, STATUS["bad"])
            cr.new_path()
            cr.arc(x + 9, 16, 3.5, 0, 2 * math.pi)
            _mark(cr, STATUS[state], a, fill=True)
            _text(cr, x + 16, 8, label, size=8 if bw < 90 else 8.5, bold=True, a=0.9, width=bw - 18)
            _text(cr, x + 8, 27, detail or {"ok": "fine", "bad": "problem", "warn": "slow", "check": "checking…",
                                            "off": "—"}[state], size=8, a=0.6, width=bw - 12)
    return 54, draw, 0.1 if live else None


def _screens(lines):
    mons = []
    for line in lines[:4]:
        label, rest = _split(line)
        m = re.match(r"(\d+)\s*x\s*(\d+)(.*)", rest)
        mons.append((label, int(m.group(1)), int(m.group(2)), m.group(3).strip()))
    total_w = sum(w for _l, w, _h, _x in mons) + 200 * (len(mons) - 1)
    scale = min((W - 20) / total_w, 130 / max(h for _l, _w, h, _x in mons))

    def draw(cr, width):
        x = (width - total_w * scale) / 2
        for label, w, h, extra in mons:
            sw, sh = w * scale, h * scale
            _white(cr, 0.07)
            _round_rect(cr, x, 4, sw, sh, 6)
            cr.fill()
            _white(cr, 0.6 if "primary" in extra or "main" in extra else 0.3)
            cr.set_line_width(1.5)
            _round_rect(cr, x, 4, sw, sh, 6)
            cr.stroke()
            _white(cr, 0.25)
            cr.rectangle(x + sw / 2 - 14, 4 + sh, 28, 6)
            cr.fill()
            _text(cr, x + sw / 2, 4 + sh / 2 - 18, label, size=9, a=0.6, align="center", width=sw - 8)
            _text(cr, x + sw / 2, 4 + sh / 2 - 4, "%d × %d" % (w, h), size=12, bold=True, align="center")
            if extra:
                _text(cr, x + sw / 2, 4 + sh / 2 + 14, extra, size=8, a=0.5, align="center", width=sw - 8)
            x += sw + 200 * scale
    return 140 + 14, draw, None


PLACES = {"left": (0, 0, .5, 1), "right": (.5, 0, .5, 1), "top": (0, 0, 1, .5), "bottom": (0, .5, 1, .5),
          "full": (0, 0, 1, 1), "max": (0, 0, 1, 1), "maximized": (0, 0, 1, 1), "maximised": (0, 0, 1, 1),
          "top-left": (0, 0, .5, .5), "top-right": (.5, 0, .5, .5), "bottom-left": (0, .5, .5, .5),
          "bottom-right": (.5, .5, .5, .5), "center": (.2, .12, .6, .76), "centre": (.2, .12, .6, .76),
          "left-third": (0, 0, 1 / 3, 1), "middle-third": (1 / 3, 0, 1 / 3, 1), "right-third": (2 / 3, 0, 1 / 3, 1),
          "left-two-thirds": (0, 0, 2 / 3, 1), "right-two-thirds": (1 / 3, 0, 2 / 3, 1)}


def _layout(lines):
    screens, cur = [], None
    for line in lines[:24]:
        if line.startswith("="):
            cur = (line.strip("= ").strip(), [])
            screens.append(cur)
            continue
        if cur is None:
            cur = ("", [])
            screens.append(cur)
        app, place = _split(line)
        nums = re.findall(r"[\d.]+", place)
        box = tuple(float(n) for n in nums[:4]) if len(nums) >= 4 else \
            PLACES.get(place.lower().replace(" ", "-").replace("_", "-"), PLACES["full"])
        cur[1].append((app, box))
    per = 2 if len(screens) > 1 else 1
    sw = (W - 16 * (per - 1)) / per if per > 1 else 300
    sh = sw * 9 / 16
    rows = math.ceil(len(screens) / per)

    def draw(cr, width):
        for i, (name, wins) in enumerate(screens):
            x = (i % per) * (sw + 16) + (0 if per > 1 else (width - sw) / 2)
            y = (i // per) * (sh + 28)
            _white(cr, 0.06)
            _round_rect(cr, x, y, sw, sh, 6)
            cr.fill()
            for j, (app, (bx, by, bw, bh)) in enumerate(wins):
                rx, ry, rw, rh = x + 3 + bx * (sw - 6), y + 3 + by * (sh - 6), bw * (sw - 6) - 3, bh * (sh - 6) - 3
                _white(cr, SHADES[j % len(SHADES)] * 0.35)
                _round_rect(cr, rx, ry, rw, rh, 4)
                cr.fill()
                _text(cr, rx + rw / 2, ry + rh / 2 - 7, app, size=8.5, bold=True, align="center", width=rw - 6)
            if name:
                _text(cr, x + sw / 2, y + sh + 6, name, size=9, a=0.7, align="center", width=sw)
    return rows * (sh + 28), draw, None


WMO_RAIN = set(range(51, 68)) | {80, 81, 82}
WMO_SNOW = set(range(71, 78)) | {85, 86}


def _sky(code):
    code = int(code)
    if code == 0:
        return "sun"
    if code in (1, 2):
        return "partly"
    if code in (45, 48):
        return "fog"
    if code in WMO_RAIN:
        return "rain"
    if code in WMO_SNOW:
        return "snow"
    if code >= 95:
        return "storm"
    return "cloud"


def _icon(cr, kind, cx, cy, s):
    """A sky icon s pixels wide, centred on cx, cy."""
    def sun(x, y, r, rays=True):
        _rgb(cr, SUN)
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.fill()
        if rays:
            cr.set_line_width(max(1.2, s / 22))
            for k in range(8):
                a = k * math.pi / 4
                cr.move_to(x + math.cos(a) * r * 1.4, y + math.sin(a) * r * 1.4)
                cr.line_to(x + math.cos(a) * r * 1.85, y + math.sin(a) * r * 1.85)
            cr.stroke()

    def cloud(x, y, w, a=0.9):
        _white(cr, a)
        r = w / 4
        cr.arc(x - r, y + r * 0.3, r * 0.85, 0, 2 * math.pi)
        cr.fill()
        cr.arc(x + r * 0.2, y - r * 0.2, r * 1.15, 0, 2 * math.pi)
        cr.fill()
        cr.arc(x + r * 1.2, y + r * 0.35, r * 0.8, 0, 2 * math.pi)
        cr.fill()
        _round_rect(cr, x - r * 1.8, y + r * 0.2, r * 3.8, r * 0.95, r * 0.45)
        cr.fill()

    if kind == "sun":
        sun(cx, cy, s * 0.22)
    elif kind == "partly":
        sun(cx - s * 0.14, cy - s * 0.12, s * 0.17)
        cloud(cx + s * 0.06, cy + s * 0.08, s * 0.62)
    elif kind == "fog":
        _white(cr, 0.7)
        cr.set_line_width(max(1.5, s / 14))
        cr.set_line_cap(1)
        for k in range(3):
            y = cy - s * 0.18 + k * s * 0.18
            cr.move_to(cx - s * 0.32 + (k % 2) * s * 0.08, y)
            cr.line_to(cx + s * 0.32 - ((k + 1) % 2) * s * 0.08, y)
        cr.stroke()
    else:
        cloud(cx, cy - s * 0.08, s * 0.72, 0.85)
        cr.set_line_width(max(1.3, s / 18))
        cr.set_line_cap(1)
        if kind == "rain":
            _rgb(cr, STATUS["check"])
            for k in range(3):
                x = cx - s * 0.2 + k * s * 0.2
                cr.move_to(x, cy + s * 0.2)
                cr.line_to(x - s * 0.06, cy + s * 0.36)
            cr.stroke()
        elif kind == "snow":
            _white(cr, 0.95)
            for k in range(3):
                cr.arc(cx - s * 0.2 + k * s * 0.2, cy + s * 0.3, s * 0.04, 0, 2 * math.pi)
                cr.fill()
        elif kind == "storm":
            _rgb(cr, SUN)
            cr.move_to(cx + s * 0.04, cy + s * 0.1)
            cr.line_to(cx - s * 0.08, cy + s * 0.3)
            cr.line_to(cx + s * 0.02, cy + s * 0.3)
            cr.line_to(cx - s * 0.06, cy + s * 0.46)
            cr.stroke()


def _weather(lines):
    now, days = None, []
    for line in lines[:8]:
        label, rest = _split(line)
        nums = [_num(v) for v in re.findall(r"-?[\d.]+%?", rest.replace("%", ""))]
        if label.lower() == "now":
            now = nums  # code, temperature, feels like
        elif len(nums) >= 3:
            days.append((label, nums))
    top_h = 70 if now else 0
    if days:
        lo = min(n[1] for _l, n in days)
        hi = max(n[2] for _l, n in days)

    def draw(cr, width):
        if now:
            _icon(cr, _sky(now[0]), 34, 32, 58)
            _text(cr, 76, 6, "%d°" % round(now[1]), size=24, bold=True)
            if len(now) > 2:
                _text(cr, 78, 42, "feels like %d°" % round(now[2]), size=9, a=0.6)
        if not days:
            return
        col = width / len(days)
        span = (hi - lo) or 1
        for i, (label, n) in enumerate(days):
            cx = col * i + col / 2
            y0 = top_h
            _text(cr, cx, y0, label, size=8.5, a=0.65, align="center", width=col - 2)
            _icon(cr, _sky(n[0]), cx, y0 + 34, min(34, col - 8))
            bar_top, bar_bottom = y0 + 70, y0 + 140
            t_hi = bar_bottom - (n[2] - lo) / span * (bar_bottom - bar_top)
            t_lo = bar_bottom - (n[1] - lo) / span * (bar_bottom - bar_top)
            _white(cr, 0.08)
            _pill(cr, cx - 3, bar_top, 6, bar_bottom - bar_top)
            cr.fill()
            _white(cr, 0.8)
            _pill(cr, cx - 3, t_hi, 6, max(6, t_lo - t_hi))
            cr.fill()
            _text(cr, cx, t_hi - 15, "%d°" % round(n[2]), size=8.5, bold=True, align="center")
            _text(cr, cx, t_lo + 2, "%d°" % round(n[1]), size=8, a=0.55, align="center")
            if len(n) > 3:
                rain = max(0, min(100, n[3]))
                _rgb(cr, STATUS["check"], 0.15)
                _pill(cr, cx - col * 0.3, y0 + 164, col * 0.6, 5)
                cr.fill()
                if rain:
                    _rgb(cr, STATUS["check"], 0.9)
                    _pill(cr, cx - col * 0.3, y0 + 164, max(5, col * 0.6 * rain / 100), 5)
                    cr.fill()
                _text(cr, cx, y0 + 172, "%d%%" % rain, size=7.5, a=0.55, align="center")
    return top_h + (190 if days else 0), draw, None


INDIA = (66, 99, 5, 38)


def _world():
    global _WORLD
    if _WORLD is None:
        with open(os.path.join(os.path.dirname(__file__), "world.json")) as f:
            _WORLD = [[(r[i] / 10.0, r[i + 1] / 10.0) for i in range(0, len(r), 2)] for r in json.load(f)["rings"]]
    return _WORLD


def _map(lines):
    pins, view = [], None
    for line in lines[:8]:
        head, rest = _split(line)
        if head.lower() == "view":
            view = rest.lower()
            continue
        m = re.match(r"\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*:?\s*(.*)", line)
        if m:
            pins.append((float(m.group(1)), float(m.group(2)), m.group(3).strip()))
    if not pins and view != "world":
        return None
    if view == "india" or (view is None and all(INDIA[0] <= lon <= INDIA[1] and INDIA[2] <= lat <= INDIA[3]
                                                for lat, lon, _l in pins)):
        x0, x1, y0, y1 = INDIA
    elif view == "world" or len(pins) > 1 and (max(p[1] for p in pins) - min(p[1] for p in pins)) > 90:
        x0, x1, y0, y1 = -170, 190, -58, 80
    else:
        lons, lats = [p[1] for p in pins], [p[0] for p in pins]
        cx, cy = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
        half = max(25, (max(lons) - min(lons)) * 0.75, (max(lats) - min(lats)) * 1.2)
        x0, x1, y0, y1 = cx - half, cx + half, cy - half * 0.6, cy + half * 0.6
    height = min(230, max(130, W * (y1 - y0) / (x1 - x0)))

    def draw(cr, width):
        sx, sy = width / (x1 - x0), height / (y1 - y0)

        def p(lon, lat):
            return (lon - x0) * sx, (y1 - lat) * sy

        _round_rect(cr, 0, 0, width, height, 8)
        cr.clip()
        _white(cr, 0.03)
        cr.paint()
        for shift in (0, 360, -360):
            for ring in _world():
                if all(lon + shift < x0 - 30 or lon + shift > x1 + 30 for lon, _ in ring[::6]):
                    continue
                cr.move_to(*p(ring[0][0] + shift, ring[0][1]))
                for lon, lat in ring[1:]:
                    cr.line_to(*p(lon + shift, lat))
                cr.close_path()
        _white(cr, 0.13)
        cr.fill_preserve()
        _white(cr, 0.28)
        cr.set_line_width(0.8)
        cr.stroke()
        for lat, lon, label in pins:
            x, y = p(lon, lat)
            _rgb(cr, STATUS["bad"], 0.25)
            cr.arc(x, y, 9, 0, 2 * math.pi)
            cr.fill()
            cr.arc(x, y, 4.5, 0, 2 * math.pi)
            _mark(cr, STATUS["bad"], fill=True)
            if label:
                layout_w = min(160, 7 * len(label) + 12)
                lx = x + 10 if x + 10 + layout_w < width else x - 10 - layout_w
                cr.set_source_rgba(0.1, 0.11, 0.13, 0.85)
                _round_rect(cr, lx, y - 10, layout_w, 19, 6)
                cr.fill()
                _text(cr, lx + 6, y - 8, label, size=8.5, bold=True, width=layout_w - 8)
    return height, draw, None


def _offset(s):
    m = re.match(r"([+-])(\d{2}):?(\d{2})", s.strip())
    if not m:
        return None
    return (1 if m.group(1) == "+" else -1) * (int(m.group(2)) * 3600 + int(m.group(3)) * 60)


def _clock_faces(lines):
    faces = []
    for line in lines[:3]:
        label, rest = _split(line)
        faces.append((label, _offset(rest)))
    d = min(110, (W - 20 * (len(faces) - 1)) / len(faces))

    def draw(cr, width):
        cell = width / len(faces)
        here = time.localtime()
        for i, (label, off) in enumerate(faces):
            now = time.time()
            t = time.gmtime(now + off) if off is not None else time.localtime(now)
            cx, cy, r = cell * i + cell / 2, d / 2 + 2, d / 2 - 2
            cr.new_path()
            _white(cr, 0.06)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.fill()
            _white(cr, 0.3)
            cr.set_line_width(1.2)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.stroke()
            for k in range(12):
                a = k * math.pi / 6
                inner = r - (8 if k % 3 == 0 else 5)
                _white(cr, 0.6 if k % 3 == 0 else 0.3)
                cr.move_to(cx + math.sin(a) * inner, cy - math.cos(a) * inner)
                cr.line_to(cx + math.sin(a) * (r - 2), cy - math.cos(a) * (r - 2))
                cr.stroke()
            sec = t.tm_sec
            mins = t.tm_min + sec / 60.0
            hours = (t.tm_hour % 12) + mins / 60.0
            cr.set_line_cap(1)
            for value, length, lw, rgb in ((hours / 12, r * 0.5, 3.2, None), (mins / 60, r * 0.75, 2.2, None),
                                           (sec / 60, r * 0.82, 1.0, STATUS["bad"])):
                a = value * 2 * math.pi
                cr.set_line_width(lw)
                cr.move_to(cx, cy)
                cr.line_to(cx + math.sin(a) * length, cy - math.cos(a) * length)
                _mark(cr, rgb, 1 if rgb else 0.92)
            _white(cr, 0.9)
            cr.arc(cx, cy, 2.5, 0, 2 * math.pi)
            cr.fill()
            day = ""
            if off is not None:
                diff = (datetime.date(t.tm_year, t.tm_mon, t.tm_mday) -
                        datetime.date(here.tm_year, here.tm_mon, here.tm_mday)).days
                day = {1: " · tomorrow", -1: " · yesterday"}.get(diff, "")
            _text(cr, cx, d + 8, label, size=9.5, bold=True, align="center", width=cell - 6)
            _text(cr, cx, d + 24, "%d:%02d %s%s" % ((t.tm_hour - 1) % 12 + 1, t.tm_min,
                                                    "am" if t.tm_hour < 12 else "pm", day),
                  size=8.5, a=0.6, align="center", width=cell - 6)
    return d + 44, draw, 1


def _gauge(lines):
    gauges = []
    for line in lines[:2]:
        label, rest = _split(line)
        state, rest = _state(rest)
        m = re.match(r"(-?[\d.,]+)\s*(?:/\s*([\d.,]+))?\s*(.*)", rest)
        value, top, unit = _num(m.group(1)), _num(m.group(2)) if m.group(2) else 100.0, m.group(3).strip()
        gauges.append((label, value, top, unit, state))
    moving = {}

    def draw(cr, width):
        cell = width / len(gauges)
        for i, (label, value, top, unit, state) in enumerate(gauges):
            cx, cy, r = cell * i + cell / 2, 96, min(84, cell / 2 - 14)
            frac, moving[i] = _eased(("gauge", label), max(0.0, min(1.0, value / top)), 1.2)
            cr.new_path()
            cr.set_line_width(12)
            cr.set_line_cap(1)
            _white(cr, 0.10)
            cr.arc(cx, cy, r, math.pi, 2 * math.pi)
            cr.stroke()
            cr.arc(cx, cy, r, math.pi, math.pi + math.pi * frac)
            _mark(cr, STATUS[state] if state else None, 1 if state else 0.85)
            for k in range(11):
                a = math.pi + k * math.pi / 10
                _white(cr, 0.3)
                cr.set_line_width(1)
                cr.move_to(cx + math.cos(a) * (r - 16), cy + math.sin(a) * (r - 16))
                cr.line_to(cx + math.cos(a) * (r - 11), cy + math.sin(a) * (r - 11))
                cr.stroke()
            a = math.pi + math.pi * frac
            _white(cr, 0.95)
            cr.set_line_width(2.5)
            cr.move_to(cx, cy)
            cr.line_to(cx + math.cos(a) * (r - 20), cy + math.sin(a) * (r - 20))
            cr.stroke()
            cr.arc(cx, cy, 5, 0, 2 * math.pi)
            cr.fill()
            shown = _fmt(value * frac / max(1e-9, value / top)) if value else "0"
            _text(cr, cx, cy + 8, "%s %s" % (shown, unit), size=13, bold=True, align="center")
            _text(cr, cx, cy + 28, label, size=9, a=0.6, align="center", width=cell - 8)
    return 146, draw, 0.05


def _progress(lines):
    bars = []
    for line in lines[:3]:
        label, rest = _split(line)
        m = re.match(r"([\d.]+)\s*/\s*([\d.]+)\s*(.*)", rest)
        words = m.group(3).lower().split()
        bars.append((label, float(m.group(1)), float(m.group(2)), "playing" in words, "over" in words))
    shown_at = time.time()
    live = any(b[3] for b in bars)

    def draw(cr, width):
        for i, (label, pos, length, playing, over) in enumerate(bars):
            y = i * 44
            ran = pos + (time.time() - shown_at if playing else 0)
            now = min(length, ran)  # "over": the time keeps counting past the end (a meeting running late)
            _text(cr, 0, y, label, size=9.5, a=0.85, width=width - 90)
            _text(cr, width, y, "%s / %s" % (_clock(ran if over else now), _clock(length)), size=8.5, a=0.6,
                  align="right")
            _white(cr, 0.1)
            _pill(cr, 0, y + 22, width, 6)
            cr.fill()
            _white(cr, 0.9)
            _pill(cr, 0, y + 22, max(6, width * now / max(1, length)), 6)
            cr.fill()
            cr.arc(width * now / max(1, length), y + 25, 6 if playing else 4, 0, 2 * math.pi)
            cr.fill()
    return 44 * len(bars), draw, 0.5 if live else None


ART = 96  # the album-art square on a ```music block


def _music(lines):
    """title: / artist: / album: / art: <image path> / time: 63/215 playing / like: <phrase>. Album art (or a
    glass square with a note) beside the song, an equalizer that moves while it plays, a progress bar that moves by
    itself, and with like: a heart in a circle above the time left that says that phrase when clicked."""
    f = dict(_split(l) for l in lines)
    m = re.match(r"([\d.]+)\s*/\s*([\d.]+)\s*(\w*)", f.get("time", ""))
    pos, length, playing = (float(m.group(1)), float(m.group(2)), m.group(3).lower() == "playing") if m else \
        (0.0, 0.0, f.get("time", "").lower().endswith("playing"))
    pix = None
    if f.get("art"):
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file(f["art"])
            side = min(pix.get_width(), pix.get_height())  # a square from the middle, scaled to fit
            pix = pix.new_subpixbuf((pix.get_width() - side) // 2, (pix.get_height() - side) // 2, side, side) \
                .scale_simple(ART, ART, GdkPixbuf.InterpType.BILINEAR)
        except GLib.Error:
            pix = None
    shown_at = time.time()
    like, heart = f.get("like"), None
    if like:
        theme = Gtk.IconTheme.get_default()  # None without a screen (tests): the ♥ glyph is drawn then
        info = theme and theme.lookup_icon("emblem-favorite-symbolic", 14, 0)
        heart = info.load_symbolic(Gdk.RGBA(1, 1, 1, 0.9), None, None, None)[0] if info else None
    HX, HY, HR = -14, 56, 13  # the heart's circle: centre from the right edge, centre y, radius

    def draw(cr, width):
        _round_rect(cr, 0, 0, ART, ART, 12)
        if pix:
            cr.save()
            cr.clip_preserve()
            Gdk.cairo_set_source_pixbuf(cr, pix, 0, 0)
            cr.paint()
            cr.restore()
        else:
            _white(cr, 0.07)
            cr.fill_preserve()
            _text(cr, ART / 2, ART / 2 - 22, "♪", size=26, a=0.5, align="center")
            _round_rect(cr, 0, 0, ART, ART, 12)
        _white(cr, 0.16)
        cr.set_line_width(1)
        cr.stroke()
        x = ART + 16
        w = width - x
        _text(cr, x, 4, f.get("title") or "Something", size=12.5, a=0.96, bold=True, width=w - 30)
        room = w - 2 * HR - 8 if like else w  # keep the artist and album clear of the heart
        _text(cr, x, 28, f.get("artist", ""), size=9.5, a=0.72, width=room)
        _text(cr, x, 46, f.get("album", ""), size=8.5, a=0.45, width=room)
        if like:
            cr.arc(width + HX, HY, HR, 0, 2 * math.pi)
            _white(cr, 0.07)
            cr.fill_preserve()
            _white(cr, 0.16)
            cr.set_line_width(1)
            cr.stroke()
            if heart:
                Gdk.cairo_set_source_pixbuf(cr, heart, width + HX - heart.get_width() / 2,
                                            HY - heart.get_height() / 2)
                cr.paint()
            else:
                _text(cr, width + HX, HY - 9, "♥", size=10, a=0.9, align="center")
        t = time.time()
        for i in range(4):  # equalizer, top right: bars bounce while it plays, rest low when paused
            h = 4 + 10 * abs(math.sin(t * (3.1 + i * 0.9) + i * 1.7)) if playing else 3
            _white(cr, 0.8 if playing else 0.3)
            _pill(cr, width - 22 + i * 6, 18 - h, 3.5, h)
            cr.fill()
        if length:
            now = min(length, pos + (t - shown_at if playing else 0))
            p = now / length
            _white(cr, 0.1)
            _pill(cr, x, 76, w, 4)
            cr.fill()
            _white(cr, 0.92)
            _pill(cr, x, 76, max(4, w * p), 4)
            cr.fill()
            cr.arc(x + w * p, 78, 5.5 if playing else 4, 0, 2 * math.pi)
            cr.fill()
            _text(cr, x, 86, _clock(now), size=8, a=0.55)
            _text(cr, width, 86, "-" + _clock(length - now), size=8, a=0.55, align="right")
        else:
            _text(cr, x, 80, "Playing" if playing else "Paused", size=8.5, a=0.55)
    def hit(px, py, width):
        return like if like and math.hypot(px - (width + HX), py - HY) <= HR + 3 else None
    return ART + 4, draw, 0.08 if playing else None, hit


KINDS = {"ring": _ring, "countdown": _countdown, "spark": _spark, "stack": _stack, "donut": _donut,
         "steps": _steps, "timeline": _timeline, "heat": _heat, "path": _path, "screens": _screens,
         "layout": _layout, "weather": _weather, "map": _map, "clock": _clock_faces, "gauge": _gauge,
         "progress": _progress, "music": _music}
SILENT = {"countdown", "map", "clock", "progress", "music", "heat", "spark", "layout", "screens"}  # not read aloud


def spoken(kind, lines):
    """What a block says when the card is read aloud (plain sentences, or nothing for pictures)."""
    if kind in SILENT:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line or line.lower().startswith("unit:"):
            continue
        if kind == "steps":
            state, rest = _split(line)
            out.append("%s, %s" % (rest.partition("|")[0].strip(), {"done": "done", "now": "under way",
                                                                    "failed": "failed"}.get(state, "to do")))
        elif kind == "weather":
            label, rest = _split(line)
            nums = re.findall(r"-?[\d.]+", rest)
            if label.lower() != "now" and len(nums) >= 3:
                out.append("%s: %s to %s degrees" % (label, nums[1], nums[2]))
        elif kind == "path":
            label, rest = _split(line)
            out.append("%s: %s" % (label, rest))
        else:
            out.append(re.sub(r"\s*\|.*$", "", line))
    return out
