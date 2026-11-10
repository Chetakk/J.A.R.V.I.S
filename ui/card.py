"""The "show" card: a floating panel, styled like the chat, for things worth seeing rather than hearing
(a table of numbers, a list, links, a picture, choices). It stays until closed, replaced by the next card,
or left alone for a while (ui.hide_after, counted for each card from when it opened).

A card is {"title", "text" (markdown), "image" (path), "choices" [...], "id", "refresh" {...}}:
- text: "# heading", "- bullet" (indent to nest), "1. step", "- [ ]"/"- [x]" checklists, "| table |" rows,
  ``` blocks, a ```bars block ("Label: 55%" per line), [[55%]] meters, "> quote", "---", **bold**, *italic*,
  `code`, [links](https://...); drawn blocks (```ring, ```steps, ```weather, ```map... see ui/blocks.py) and
  {ok}/{warn}/{bad} dots, {file:pdf} badges.
- choices: buttons ("Open it", or {"label", "do", "icon"}); clicking one says its phrase to JARVIS. With an
  "icon" (a GTK icon name) the button shows only that icon.
- id: a card with the same id as one on screen updates it in place (progress, live values).
- stay: true keeps it up until you close it (the music card while a song plays); its clock starts on the
  update that sets it false.
Several cards can be up at once (Deck), up to MAX_OPEN, in a row centred above the voice bar with the chat. Built from plain GTK widgets and a little Cairo, so it costs nothing while hidden.
"""
import math
import re
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from ui import blocks  # noqa: E402
from ui.window import GLOW, PANEL_H, PANEL_W, _clear, _make_transparent  # noqa: E402

CARD_W, CARD_MAX_H = 460, 530  # a full card is about as tall as the chat panel (PANEL_H)
HISTORY = 10
MAX_OPEN = 10  # cards on screen at once; opening one more closes the oldest
GAP = 10 - 2 * GLOW  # windows overlap by their glow margins, so the glass itself is 10 px apart
BUTTONS = 6  # a card shows this many choices as buttons; the rest you pick by number

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


def arrange(sizes, left, width, bottom, top, right=False):
    """Where windows of these (w, h) sizes go: rows centred on the screen, sitting on `bottom`. One is in the exact
    middle, two either side of it, three left, middle and right. sizes[0] goes at the right of the bottom row. Rows
    that no longer fit overlap instead, a little apart. `right`: the rows sit at the screen's right edge instead,
    above the compact bar."""
    spots, start, row_bottom = [None] * len(sizes), 0, bottom
    while start < len(sizes):
        end, used = start + 1, sizes[start][0]
        while end < len(sizes) and used + GAP + sizes[end][0] <= width:
            used += GAP + sizes[end][0]
            end += 1
        x = left + (width - used if right else (width - used) // 2)
        for i in range(end - 1, start - 1, -1):
            spots[i] = (x, row_bottom - sizes[i][1])
            x += sizes[i][0] + GAP
        row_bottom -= max(h for _w, h in sizes[start:end]) + GAP
        start = end
    full = 0
    for i, (w, h) in enumerate(sizes):
        if spots[i][1] < top:
            full += 1
            x = left + width - w - 28 * full if right else left + (width - w) // 2 + 28 * full
            spots[i] = (x, max(top, bottom - h - 28 * full))
    return spots


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
    return blocks.markup(_LINK.sub(r'<a href="\2">\1</a>', s))


def links(card):
    """The card's links in order, for "open the second link"."""
    return [url for _text, url in _LINK.findall((card or {}).get("text") or "")]


def choices(card):
    """The card's choices as (label, phrase) pairs, for "the second one" or "number 12". Only the first
    BUTTONS show as buttons; a long list (a folder, your clipboard) keeps the rest for saying its number."""
    out = []
    for c in (card or {}).get("choices") or []:
        if isinstance(c, dict) and c.get("label"):
            out.append((str(c["label"]), str(c.get("do") or c["label"])))
        elif str(c).strip():
            out.append((str(c), str(c)))
    return out[:99]


def plain(card):
    """The card as plain sentences, to read aloud."""
    lines, kind, block = [], None, []
    for line in ((card or {}).get("text") or "").splitlines():
        line = line.strip()
        if line.startswith("```"):
            if kind is None:
                kind, block = line[3:].strip().lower(), []
            else:
                said = blocks.spoken(kind, block) if kind in blocks.KINDS else \
                    [] if kind in ("log", "git", "") else block
                lines += [l.rstrip(".") + "." for l in said]
                kind = None
            continue
        if kind is not None:
            block.append(line)
            continue
        line = blocks.strip(line)
        if not line or re.fullmatch(r"[|:\-\s]+|-{3,}", line):
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
            chart = _bars(block) if kind == "bars" else blocks.make(kind, block)
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
    """One card window. GTK-thread only; ui.UI wraps the Deck with thread-safe calls. `last` and `visible` are safe
    to read from any thread."""

    def __init__(self, on_choice, pinned=False, deck=None):
        self.on_choice = on_choice  # callback(phrase) when a choice button is clicked
        self.pinned = pinned  # a pinned copy: closing it destroys it; it never moves on its own
        self.deck = deck  # the Deck that places it (None for a pinned copy)
        self.last = None
        self.visible = False
        self.user_xy = None  # where you dragged it, if you did
        self.quiet = 0.0  # seconds left alone on screen since it last opened or updated
        self._dragging = False
        self._moving = []  # drawn blocks that animate, redrawn by _tick while this card is up
        self._ticking = False
        _load_css()
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

    def _hit(self, widget, ev):
        """A click on a drawn block: say the phrase it has there (the music card's heart)."""
        phrase = widget.hit(ev.x, ev.y) if ev.button == 1 else None
        if phrase:
            self.on_choice(phrase)
        return bool(phrase)

    def show(self, card):
        """Put `card` in this window (replacing what it showed)."""
        self.last = card
        self.quiet = 0.0
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
        self._moving = []
        for widget in build(card.get("text")):
            self.body.pack_start(widget, False, False, 0)
            if getattr(widget, "hit", None):
                widget.connect("button-press-event", self._hit)
            if getattr(widget, "every", None):
                widget.due = 0.0
                self._moving.append(widget)
        icons = {c["label"]: c["icon"] for c in card.get("choices") or [] if isinstance(c, dict) and c.get("icon")}
        for n, (label, phrase) in enumerate(choices(card), 1):
            button = Gtk.Button()
            if label in icons:  # an icon-only button (the music card's controls); the label stays for voice
                button.add(Gtk.Image.new_from_icon_name(icons[label], Gtk.IconSize.BUTTON))
                button.set_tooltip_text(label)
            else:
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
        if self._moving and not self._ticking:
            self._ticking = True
            GLib.timeout_add(50, self._tick)
        return False

    def _tick(self):
        """Redraw the animated blocks that are due; stops once the card is hidden or has none."""
        if not self.visible or not self._moving:
            self._ticking = False
            return False
        now = time.time()
        for area in self._moving:
            if now >= area.due:
                area.due = now + area.every
                area.queue_draw()
        return True

    def close(self):
        if self.pinned:
            self.win.destroy()
        else:
            self.win.hide()
        self.visible = False
        if self.deck:
            self.deck._closed(self)
        return False

    def _press(self, widget, ev):
        if ev.button == 1:
            self._dragging = True
            self.win.begin_move_drag(1, int(ev.x_root), int(ev.y_root), ev.time)
        return True

    def _moved(self, widget, ev):
        if self._dragging:  # you moved it: it stays where you put it
            self.user_xy = (ev.x, ev.y)
            GLib.timeout_add(800, self._dropped)
        return False

    def _dropped(self):
        if not self._dragging:
            return False
        self._dragging = False
        if self.deck:
            self.deck._place()
        return False

    def _place(self):
        if not self.win.get_visible() or self._dragging:
            return
        if self.deck and not self.user_xy:
            self.deck._place()
            return
        geo = Gdk.Display.get_default().get_monitor(0).get_workarea()
        h = self.win.get_allocated_height()
        x, y = self.user_xy or (geo.x, geo.y)
        self.win.move(int(x), int(min(max(geo.y, y), geo.y + geo.height - h)))


class Deck:
    """The cards on screen: up to MAX_OPEN at once, in a row centred above the voice bar with the chat (see arrange);
    the newest sits next to the chat, and a card you drag stays where you put it. A card with the id of one on screen updates that one in place, wherever it is.
    GTK-thread only; `last`, `visible`, `history`, current() and ids() are safe to read from any thread."""

    def __init__(self, bottom, chat_visible, move_chat, on_choice, compact=lambda: False):
        self.bottom, self.chat_visible, self.move_chat, self.on_choice = bottom, chat_visible, move_chat, on_choice
        self.compact = compact  # True: the row goes to the right, where the compact bar is
        self.open = []  # windows on screen, oldest first
        self.spare = []  # closed windows, reused for the next card
        self.pins = []
        self.history, self.pos = [], -1  # recent cards, and which one "previous card" / "next card" is on
        self.last = None  # the card shown most recently (for showing it again)
        self._placing = False
        self._chat_spot = None
        self._glide = {}  # what's sliding to its spot: a Card or "chat" -> [x, y, aim x, aim y]

    @property
    def visible(self):
        return bool(self.open)

    def current(self):
        """The newest card on screen, else the last one shown."""
        return self.open[-1].last if self.open else self.last

    def ids(self):
        return [(w.last or {}).get("id") for w in list(self.open)]

    def windows(self):
        return [w.win for w in self.open + self.pins]

    def show(self, card, record=True):
        """Open a card beside the others; None shows the last one again. A card with the id of one on screen
        updates that one in place."""
        card = card or self.last
        if not card:
            return False
        same = card.get("id") and next((w for w in self.open if (w.last or {}).get("id") == card.get("id")), None)
        if record:
            kept = same and self.history and self.history[self.pos].get("id") == card.get("id")
            if kept:
                self.history[self.pos] = card
            else:
                self.history = (self.history[:self.pos + 1] + [card])[-HISTORY:]
                self.pos = len(self.history) - 1
        if same:
            if same is self.open[-1]:
                self.last = card
            return same.show(card)
        if card is self.last and self.open and self.open[-1].last is card:
            return False  # already up
        win = self.spare.pop() if self.spare else Card(self.on_choice, deck=self)
        win.user_xy = None
        self.open.append(win)
        while len(self.open) > MAX_OPEN:
            self.open[0].close()
        self.last = card
        win.show(card)
        return False

    def show_many(self, cards):
        for card in cards:
            self.show(card)
        return False

    def step(self, delta):
        """Show the previous (-1) or next (+1) card from the history, in the newest card's window."""
        if not 0 <= self.pos + delta < len(self.history):
            return False
        self.pos += delta
        card = self.history[self.pos]
        if self.open:
            self.last = card
            self.open[-1].show(card)
        else:
            self.show(card, record=False)
        return False

    def close(self, card_id=None, every=False):
        """Close the newest card, the one with `card_id`, or every card (pinned copies stay)."""
        if every:
            targets = list(self.open)
        elif card_id:
            targets = [w for w in self.open if (w.last or {}).get("id") == card_id]
        else:
            targets = self.open[-1:]
        for w in targets:
            w.close()
        return False

    def age(self, step, after):
        """Count `step` more quiet seconds on every card and close each one left alone `after` seconds: each keeps
        its own time, so the one that came first goes first."""
        for w in list(self.open):
            w.quiet += step
            if w.quiet >= after and not w.last.get("stay"):
                w.close()
        return False

    def pin(self):
        """Keep a copy of the newest card on screen; it stays when the others are closed or hidden."""
        if not self.open:
            return False
        win = self.open[-1]
        copy = Card(self.on_choice, pinned=True)
        x, y = win.win.get_position()
        offset = 24 * (len(self.pins) + 1)
        copy.user_xy = (x - offset, max(0, y - offset))
        copy.show(win.last)
        self.pins.append(copy)
        win.close()
        return False

    def unpin_all(self):
        for copy in self.pins:
            copy.win.destroy()
        self.pins = []
        return False

    def _closed(self, win):
        if win in self.open:
            self.open.remove(win)
            self.spare.append(win)
            win.placed = False  # reused for a new card, it appears in its spot rather than sliding from the old one
            self._place()

    def _place(self):
        """The chat and the cards in their row, centred or by the compact bar; a card you dragged stays where it is."""
        if self._placing:
            return
        self._placing = True
        try:
            auto = [w for w in reversed(self.open) if not w.user_xy and not w._dragging and w.win.get_visible()]
            for w in self.open:
                if w.user_xy and not w._dragging:
                    Card._place(w)
            geo = Gdk.Display.get_default().get_monitor(0).get_workarea()
            chat = [(PANEL_W, PANEL_H)] if self.chat_visible() else []
            # Real widths: a card with a drawn block comes out a few pixels wider, and the gaps must still be equal.
            spots = arrange(chat + [(max(CARD_W, w.win.get_allocated_width()), w.win.get_allocated_height()) for w in auto],
                            geo.x, geo.width,
                            self.bottom() + GLOW, geo.y, self.compact())
            spot = spots.pop(0) if chat else None
            if spot and spot != self._chat_spot:
                self._slide("chat", self._chat_spot, spot)
            if not spot:
                self._glide.pop("chat", None)
            self._chat_spot = spot
            for w, (x, y) in zip(auto, spots):
                if w in self._glide or w.win.get_position() != (x, y):
                    self._slide(w, w.win.get_position() if getattr(w, "placed", False) else None, (x, y))
                w.placed = True
        finally:
            self._placing = False

    def _slide(self, key, start, aim):
        """Move a window to `aim`, gliding there with the bar; from nowhere (just opened) it's put there at once."""
        if key in self._glide:
            self._glide[key][2:] = aim  # already on its way: turn towards the new spot
        elif start is None:
            self._move(key, *aim)
            return
        else:
            self._glide[key] = [*start, *aim]
        if len(self._glide) == 1 and not getattr(self, "_gliding", False):
            self._gliding = True
            GLib.timeout_add(33, self._glide_step)  # the bar's frame rate and pace, so they move as one

    def _move(self, key, x, y):
        self.move_chat(x, y) if key == "chat" else key.win.move(x, y)

    def _glide_step(self):
        for key, g in list(self._glide.items()):
            if key != "chat" and (key.user_xy or key._dragging or not key.win.get_visible()):
                del self._glide[key]  # you took it, or it closed
                continue
            g[0], g[1] = g[0] + (g[2] - g[0]) * 0.32, g[1] + (g[3] - g[1]) * 0.32
            if abs(g[2] - g[0]) <= 1 and abs(g[3] - g[1]) <= 1:
                g[0], g[1] = g[2], g[3]
                del self._glide[key]
            self._move(key, round(g[0]), round(g[1]))
        self._gliding = bool(self._glide)
        return self._gliding


_css_loaded = False


def _load_css():
    global _css_loaded
    if not _css_loaded:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _css_loaded = True
