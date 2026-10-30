"""The "show" card: a floating panel, styled like the chat, for things worth seeing rather than hearing
(a table of numbers, a list, links, a picture, choices). It stays until closed or replaced by the next card.

A card is {"title", "text" (markdown), "image" (path), "choices" [...], "id", "refresh" {...}}:
- text: "# heading", "- bullet" (indent to nest), "1. step", "- [ ]"/"- [x]" checklists, "| table |" rows,
  ``` blocks, a ```bars block ("Label: 55%" per line), [[55%]] meters, "> quote", "---", **bold**, *italic*,
  `code`, [links](https://...).
- choices: buttons ("Open it", or {"label", "do"}); clicking one says its phrase to JARVIS.
- id: a card with the same id as the one on screen updates it in place (progress, live values).
Built from plain GTK widgets and a little Cairo, so it costs nothing while hidden.
"""
import json
import math
import os
import re

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from ui.window import ORB, PANEL_W, _clear, _make_transparent  # noqa: E402

CARD_W, CARD_MAX_H = 460, 530  # a full card is about as tall as the chat panel (PANEL_H)
HISTORY = 10
POS_FILE = os.path.expanduser("~/.local/share/jarvis/card.json")

CSS = b"""
.card label { color: #e6e6e6; }
.card .heading { color: #f4f4f4; font-weight: bold; font-size: 11.5pt; }
.card .heading1 { color: #ffffff; font-weight: bold; font-size: 13.5pt; }
.card .quote { color: #b4b4b4; font-style: italic; border-left: 3px solid rgba(255, 255, 255, 0.28);
               padding: 2px 0 2px 10px; }
.card separator { background-color: rgba(255, 255, 255, 0.10); min-height: 1px; margin: 4px 0; }
.card .cell-head { color: rgba(255, 255, 255, 0.5); font-size: 9pt; font-weight: bold; }
.card .cell { padding: 2px 0; }
.card .mono { font-family: monospace; font-size: 9.5pt; color: #d6d6d6; background-color: rgba(255, 255, 255, 0.05);
              border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 8px; padding: 8px 10px; }
.card button.close { background: transparent; border: none; box-shadow: none; color: rgba(255, 255, 255, 0.5);
                     padding: 0 6px; min-height: 0; min-width: 0; border-radius: 8px; font-size: 13pt; }
.card button.close label { font-size: 14pt; }
.card button.close:hover { color: #ffffff; background: rgba(255, 255, 255, 0.10); }
.card button.choice { background: rgba(255, 255, 255, 0.07); color: #f2f2f2; border-radius: 10px;
                      border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: none; padding: 5px 12px; }
.card button.choice:hover { background: rgba(255, 255, 255, 0.16); border-color: rgba(255, 255, 255, 0.3); }
.card button.choice label { color: #f2f2f2; }
.card label link:link, .card label link:visited { color: #ffffff; }
.card label link:hover { color: #bbbbbb; }
"""

_LINK = re.compile(r"\[([^\]]+)\]\(((?:https?|file)://[^)\s]+)\)")
_METER = re.compile(r"\[\[(\d{1,3})%\]\]")


def meter(percent, cells=10):
    """[[55%]] -> a text bar like ▰▰▰▰▰▱▱▱▱▱ 55%."""
    full = round(max(0, min(100, percent)) / 100 * cells)
    return "▰" * full + "▱" * (cells - full) + " %d%%" % percent


def _inline(text):
    """Escape for Pango, then turn **bold**, `code`, *italic*, [[n%]] and [text](url) into markup."""
    s = GLib.markup_escape_text(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<tt>\1</tt>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*]+?)\*(?![\w*])", r"<i>\1</i>", s)  # *italic*, after **bold** is gone
    s = _METER.sub(lambda m: meter(int(m.group(1))), s)
    return _LINK.sub(r'<a href="\2">\1</a>', s)


def links(card):
    """The card's links in order, for "open the second link"."""
    return [url for _text, url in _LINK.findall((card or {}).get("text") or "")]


def choices(card):
    """The card's choices as (label, phrase) pairs, for buttons and "the second one"."""
    out = []
    for c in (card or {}).get("choices") or []:
        if isinstance(c, dict) and c.get("label"):
            out.append((str(c["label"]), str(c.get("do") or c["label"])))
        elif str(c).strip():
            out.append((str(c), str(c)))
    return out[:6]


def plain(card):
    """The card as plain sentences, to read aloud."""
    lines = []
    for line in ((card or {}).get("text") or "").splitlines():
        line = line.strip()
        if not line or line.startswith("```") or re.fullmatch(r"[|:\-\s]+|-{3,}", line):
            continue
        if line.startswith("|"):
            line = ", ".join(c.strip() for c in line.strip("|").split("|"))
        line = _METER.sub(r"\1 percent", line)
        line = _LINK.sub(r"\1", line)
        line = re.sub(r"^(?:#+|>|[-*]\s*(?:\[[ xX]\])?|\d+\.)\s*", "", line)
        lines.append(re.sub(r"[*`_]", "", line).rstrip(".") + ".")
    return " ".join(lines)


def _label(markup, cls=None, mono=False):
    lab = Gtk.Label(xalign=0, wrap=True, selectable=True)
    lab.set_line_wrap_mode(2)  # word, then char
    lab.set_max_width_chars(56)
    if mono:
        lab.set_text(markup)
        lab.set_line_wrap(False)
        lab.get_style_context().add_class("mono")
    else:
        lab.set_markup(markup)
    if cls:
        lab.get_style_context().add_class(cls)
    return lab


def _table(rows):
    grid = Gtk.Grid(column_spacing=16, row_spacing=4)
    head = len(rows) > 1
    for r, cells in enumerate(rows):
        for c, cell in enumerate(cells):
            grid.attach(_label(_inline(cell.upper() if head and r == 0 else cell),
                               "cell-head" if head and r == 0 else "cell"), c, r, 1, 1)
    return grid


def _bars(lines):
    """A ```bars block: one "Label: 55%" / "Label 12.5 GB" per line, drawn as white bars."""
    rows = []
    for line in lines:
        m = re.match(r"\s*(.+?)[:\s]\s*(-?\d+(?:\.\d+)?)\s*(%|[^\d\s]\S*)?\s*$", line)
        if m:
            rows.append((m.group(1).strip(), float(m.group(2)), m.group(3) or ""))
    if not rows:
        return None
    top = 100.0 if all(u == "%" for _l, _v, u in rows) else max(v for _l, v, _u in rows) or 1.0
    area = Gtk.DrawingArea()
    area.set_size_request(CARD_W - 28, 26 * len(rows))

    def bar(cr, x, y, w, h):
        r = min(h / 2, w / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, math.pi / 2)
        cr.arc(x + r, y + r, r, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()

    def draw(widget, cr):
        width = widget.get_allocated_width()
        for i, (label, value, unit) in enumerate(rows):
            y = i * 26
            cr.set_source_rgba(0.9, 0.9, 0.9, 1)
            cr.select_font_face("Sans")
            cr.set_font_size(12)
            cr.move_to(0, y + 17)
            cr.show_text(label[:16])
            x0, x1 = 116, width - 66
            cr.set_source_rgba(1, 1, 1, 0.10)
            bar(cr, x0, y + 7, x1 - x0, 12)
            fill = (x1 - x0) * max(0.0, min(1.0, value / top))
            if fill >= 1:
                cr.set_source_rgba(1, 1, 1, 0.85)
                bar(cr, x0, y + 7, fill, 12)
            cr.set_source_rgba(0.75, 0.75, 0.75, 1)
            cr.move_to(x1 + 10, y + 17)
            cr.show_text(("%g%s" % (value, unit if unit == "%" else " " + unit)).strip())
        return False

    area.connect("draw", draw)
    return area


def build(text):
    """Turn the card's markdown into a list of widgets."""
    widgets, lines, i = [], (text or "").splitlines(), 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("```"):
            kind, block, i = line[3:].strip().lower(), [], i + 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            chart = _bars(block) if kind == "bars" else None
            widgets.append(chart or _label("\n".join(block), mono=True))
        elif line.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):  # skip the |---| row
                    rows.append(cells)
                i += 1
            widgets.append(_table(rows))
            continue
        elif re.match(r"#{1,3} ", line):
            widgets.append(_label(_inline(line.lstrip("#").strip()), "heading1" if line.startswith("# ") else "heading"))
        elif re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            widgets.append(Gtk.Separator())
        elif line.startswith(">"):
            widgets.append(_label(_inline(line.lstrip("> ")), "quote"))
        elif re.match(r"\s*[-*] ", line):
            indent = "    " * min(3, (len(line) - len(line.lstrip())) // 2)
            item = re.sub(r"^\s*[-*] ", "", line)
            box = re.match(r"\[([ xX])\]\s*", item)  # - [ ] / - [x] checklists
            mark = ("☑" if box.group(1).strip() else "☐") if box else "•"
            widgets.append(_label(indent + mark + "  " + _inline(item[box.end():] if box else item)))
        elif line.strip():
            widgets.append(_label(_inline(line)))
        i += 1
    return widgets


class Card:
    """GTK-thread only; ui.UI wraps it with thread-safe calls. `last`, `visible` and `history` are safe to
    read from any thread."""

    def __init__(self, orb_xy, chat_visible, on_choice, pinned=False):
        self.orb_xy, self.chat_visible = orb_xy, chat_visible  # callables, for placing next to the orb/chat
        self.on_choice = on_choice  # callback(phrase) when a choice button is clicked
        self.pinned = pinned  # a pinned copy: closing it destroys it; it never moves on its own
        self.last = None
        self.visible = False
        self.history, self.pos = [], -1  # recent cards, and which one is on screen
        self.pins = []
        self.user_xy = None if pinned else _load_xy()  # where you dragged it, if you did
        self._dragging = False
        if not pinned:
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        w = self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        _make_transparent(w)
        w.connect("draw", lambda widget, cr: _clear(cr) or False)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_type_hint(Gdk.WindowTypeHint.DOCK)
        w.stick()
        w.connect("size-allocate", lambda *_: self._place())
        w.connect("configure-event", self._moved)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.get_style_context().add_class("panel")
        outer.get_style_context().add_class("card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_border_width(14)
        outer.pack_start(inner, True, True, 0)

        head = Gtk.Box(spacing=6)
        head.get_style_context().add_class("header")
        grip = Gtk.EventBox()  # drag the title to move the card
        grip.connect("button-press-event", self._press)
        self.title = Gtk.Label(xalign=0, ellipsize=3)
        self.title.get_style_context().add_class("title")
        grip.add(self.title)
        head.pack_start(grip, True, True, 0)
        close = Gtk.Button(label="×")
        close.get_style_context().add_class("close")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda _b: self.close())
        head.pack_end(close, False, False, 0)
        inner.pack_start(head, False, False, 0)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.body.set_margin_end(6)  # room for the scrollbar
        scroll = self.scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.body)
        inner.pack_start(scroll, True, True, 0)
        self.buttons = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6,
                                   max_children_per_line=3, homogeneous=False)
        inner.pack_start(self.buttons, False, False, 0)
        w.add(outer)
        w.set_size_request(CARD_W, -1)

    def show(self, card, record=True):
        """Show a card; None shows the last one again. A card with the on-screen card's id updates it in place."""
        card = card or self.last
        if not card:
            return False
        if record:
            same = self.last and card.get("id") and card.get("id") == self.last.get("id")
            if same and self.history:
                self.history[self.pos] = card
            else:
                self.history = (self.history[:self.pos + 1] + [card])[-HISTORY:]
                self.pos = len(self.history) - 1
        self.last = card
        for child in self.body.get_children() + self.buttons.get_children():
            child.destroy()
        self.title.set_text(card.get("title") or "Here you go")
        image = card.get("image")
        if image:
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(image, CARD_W - 28, CARD_MAX_H // 2, True)
                self.body.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
            except GLib.Error:
                pass
        for widget in build(card.get("text")):
            self.body.pack_start(widget, False, False, 0)
        for n, (label, phrase) in enumerate(choices(card), 1):
            button = Gtk.Button()
            text = Gtk.Label()
            text.set_markup('<span alpha="50%%">%d</span>  %s' % (n, GLib.markup_escape_text(label)))
            button.add(text)
            button.get_style_context().add_class("choice")
            button.connect("clicked", lambda _b, p=phrase: self.on_choice(p))
            self.buttons.add(button)
        self.win.show_all()
        self.buttons.set_visible(bool(choices(card)))
        # Wrapped labels don't pass their height up through a ScrolledWindow, so measure it: grow to fit,
        # scroll beyond CARD_MAX_H.
        _, natural = self.body.get_preferred_height_for_width(CARD_W - 34)
        self.scroll.set_min_content_height(min(natural, CARD_MAX_H))
        self.win.resize(CARD_W, 1)  # shrink to fit the new content
        self.visible = True
        self._place()
        return False

    def step(self, delta):
        """Show the previous (-1) or next (+1) card from the history."""
        if 0 <= self.pos + delta < len(self.history):
            self.pos += delta
            self.show(self.history[self.pos], record=False)
        return False

    def pin(self):
        """Keep a copy of this card on screen; new cards then use the main card as usual."""
        if not self.last:
            return False
        copy = Card(self.orb_xy, self.chat_visible, self.on_choice, pinned=True)
        x, y = self.win.get_position()
        offset = 24 * (len(self.pins) + 1)
        copy.user_xy = (x - offset, max(0, y - offset))
        copy.show(self.last, record=False)
        self.pins.append(copy)
        self.close()
        return False

    def unpin_all(self):
        for copy in self.pins:
            copy.win.destroy()
        self.pins = []
        return False

    def close(self):
        if self.pinned:
            self.win.destroy()
        else:
            self.win.hide()
        self.visible = False
        return False

    def _press(self, widget, ev):
        if ev.button == 1:
            self._dragging = True
            self.win.begin_move_drag(1, int(ev.x_root), int(ev.y_root), ev.time)
        return True

    def _moved(self, widget, ev):
        if self._dragging:  # you moved it: it stays where you put it from now on
            self.user_xy = (ev.x, ev.y)
            if not self.pinned:
                GLib.timeout_add(800, self._save_xy)
        return False

    def _save_xy(self):
        self._dragging = False
        try:
            with open(POS_FILE, "w") as f:
                json.dump({"x": self.user_xy[0], "y": self.user_xy[1]}, f)
        except OSError:
            pass
        return False

    def _place(self):
        if not self.win.get_visible() or self._dragging:
            return
        geo = Gdk.Display.get_default().get_monitor(0).get_workarea()
        h = self.win.get_allocated_height()
        if self.user_xy:
            x, y = self.user_xy
            self.win.move(int(x), int(min(max(geo.y, y), geo.y + geo.height - h)))
            return
        ox, oy = self.orb_xy()
        right = ox - (PANEL_W + 10 if self.chat_visible() else 0)  # beside the chat when it's open
        x = right - CARD_W - 10 if right - CARD_W - 10 >= geo.x else ox + ORB + 10
        y = min(max(geo.y, oy + ORB - h), geo.y + geo.height - h)
        self.win.move(x, y)


def _load_xy():
    try:
        with open(POS_FILE) as f:
            pos = json.load(f)
        return pos["x"], pos["y"]
    except (OSError, ValueError, KeyError):
        return None
