"""The "show" card: a floating panel, styled like the chat, for things worth seeing rather than hearing
(a table of numbers, a list, links, a picture, choices). It stays until closed, replaced by the next card,
or left alone for a while (ui.hide_after, counted for each card from when it opened).

A card is {"title", "text" (markdown), "image" (path), "choices" [...], "id", "refresh" {...}}:
- text: "# heading", "- bullet" (indent to nest), "1. step", "- [ ]"/"- [x]" checklists, "| table |" rows,
  ``` blocks, a ```bars block ("Label: 55%" per line), a ```rows block ("RS | Rahul Sharma {check} | Re: budget
  | 9:12 [| icon buttons]" per line: initials, a glyph or file:pdf, then who, what, when; without the first cell the initials are
  made from who), a ```people block (a name per line, "+3" for the rest: initials in circles), a ```stats block
  ("9 | emails" per line: big numbers side by side), a ```hero block (the live orb over a heading, a line and
  stats tiles), [[55%]] meters, "> quote", "---", **bold**, *italic*,
  `code`, [links](https://...); drawn blocks (```ring, ```steps, ```weather, ```map... see ui/blocks.py) and
  {ok}/{warn}/{bad} dots, {file:pdf} badges.
- tick / untick: phrases a clicked checkbox says ("tick todo {n}", numbered from 1); without them a click only
  ticks it on the card.
- items: a phrase per row of the card's list ("read email 18c2…"), for "read / open / what's the second one":
  a row, where a bare "the second one" is the second button.
- columns: more markdown texts, side by side under "text" (the card grows a COL_W wider for each past the first).
- face: true: it draws the orb (a ```hero), so the bar leaves its own out while it's up (Bar._face).
- place: "top" puts it at the top middle of the screen, out of the row above the bar (the dashboard: wide).
- rail: markdown for a RAIL_W column left of the columns, set off by a line: the dashboard's orb, tiles and status
  lines.
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

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from system.browser import on_link  # noqa: E402
from ui import blocks  # noqa: E402
from ui.window import GLOW, PANEL_H, PANEL_W, _clear, _make_transparent  # noqa: E402

CARD_W, CARD_MAX_H = 460, 530  # a full card is about as tall as the chat panel (PANEL_H)
HISTORY = 10
MAX_OPEN = 10  # cards on screen at once; opening one more closes the oldest
GAP = 10 - 2 * GLOW  # windows overlap by their glow margins, so the glass itself is 10 px apart
TOP_GAP = 8  # a "place": "top" card's gap below the top of the screen (its glow margin adds to it)
COL_W = 432 + 24  # a column of a card with "columns": a drawn block's width (blocks.W) and the gap before it
HERO_ORB = 120  # the orb's size in a ```hero block
RAIL_W, RAIL_GAP = 300, 20  # a card's "rail": its width, and the gap each side of the line beside it
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
.card .avatar { background-color: rgba(255, 255, 255, 0.12); border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 999px; min-width: 28px; min-height: 28px; color: #f4f4f4; font-size: 8.5pt;
                font-weight: bold; }
.card .people .avatar { min-width: 26px; min-height: 26px; margin-right: -5px; }
.card .who { color: #f2f2f2; font-weight: bold; }
.card .stat { background-color: rgba(255, 255, 255, 0.045); border: 1px solid rgba(255, 255, 255, 0.07);
              border-radius: 11px; padding: 7px 10px; }
.card .stat-value { color: #ffffff; font-size: 16pt; font-weight: bold; }
.card .stat-label { color: rgba(255, 255, 255, 0.45); font-size: 8.5pt; }
.card .what { color: rgba(255, 255, 255, 0.62); font-size: 9.5pt; }
.card .when { color: rgba(255, 255, 255, 0.42); font-size: 9pt; }
.card label link:link, .card label link:visited { color: #ffffff; }
.card label link:hover { color: #bbbbbb; }
.card .num { color: rgba(255, 255, 255, 0.4); font-size: 8.5pt; min-width: 12px; }
.card button.rowbtn { background: transparent; border: none; box-shadow: none; color: rgba(255, 255, 255, 0.6);
                      padding: 3px; min-height: 0; min-width: 0; border-radius: 7px; }
.card button.rowbtn:hover { color: #ffffff; background: rgba(255, 255, 255, 0.12); }
.card .pickrow { border-radius: 9px; padding: 2px 4px; }
.card .pickrow.hover { background-color: rgba(255, 255, 255, 0.08); }
.card .source { color: rgba(255, 255, 255, 0.55); }
.card .age { color: rgba(255, 255, 255, 0.4); font-size: 9pt; }
.card button.pin { padding: 0 5px; }
.card button.pin image { color: rgba(255, 255, 255, 0.5); }
.card button.pin:hover image { color: #ffffff; }
"""
# Where a card comes from, by the start of its id ("gmail", "drive-share-3"): the icon in its header, and the title
# when it has none.
SOURCES = {"gmail": ("mail-unread-symbolic", "Gmail"), "mail": ("mail-unread-symbolic", "Gmail"),
           "drive": ("folder-remote-symbolic", "Drive"), "calendar": ("x-office-calendar-symbolic", "Calendar"),
           "cal": ("x-office-calendar-symbolic", "Calendar"), "meeting": ("x-office-calendar-symbolic", "Calendar"),
           "day": ("x-office-calendar-symbolic", "Your day"), "week": ("x-office-calendar-symbolic", "Your week"),
           "chat": ("chat-symbolic", "Chat"), "people": ("system-users-symbolic", "People"),
           "playing": ("audio-x-generic-symbolic", "Music"), "spotify": ("audio-x-generic-symbolic", "Spotify"),
           "weather": ("weather-few-clouds-symbolic", "Weather"), "timer": ("alarm-symbolic", "Timer"),
           "alarm": ("alarm-symbolic", "Alarm"), "clock": ("alarm-symbolic", "Clock"),
           "wifi": ("network-wireless-symbolic", "Wi-Fi"), "speed": ("network-wireless-symbolic", "Speed test"),
           "disk": ("drive-harddisk-symbolic", "Disk"), "usage": ("utilities-system-monitor-symbolic", "System"),
           "sensors": ("utilities-system-monitor-symbolic", "System"),
           "memory": ("utilities-system-monitor-symbolic", "System"), "secret": ("dialog-password-symbolic", "Secret"),
           "project": ("emblem-documents-symbolic", "Project"), "task": ("emblem-documents-symbolic", "Task")}
FOLD_W = 260  # a card that doesn't fit shrinks to a title-only tab this wide (glow included)


def source(card):
    """(icon name, name) for where the card comes from, or (None, None)."""
    return SOURCES.get(re.split(r"[-_]", str((card or {}).get("id") or ""))[0], (None, None))


def ago(seconds):
    """How long ago a card came up, for its header: "now", "4 min ago", "2 h ago"."""
    if seconds < 60:
        return "now"
    return "%d min ago" % (seconds // 60) if seconds < 3600 else "%d h ago" % (seconds // 3600)

_BOX = re.compile(r"^(\s*[-*] )\[[ xX]\]", re.M)  # - [ ] / - [x] checklist boxes
_LINK = re.compile(r"\[([^\]]+)\]\(((?:https?|file)://[^)\s]+)\)")
_METER = re.compile(r"\[\[(\d{1,3})%\]\]")


def arrange(sizes, left, width, bottom, top, right=False):
    """Where windows of these (w, h) sizes go: rows centred on the screen, sitting on `bottom`. One is in the exact
    middle, two either side of it, three left, middle and right. sizes[0] goes at the right of the bottom row. Rows
    that no longer fit overlap instead, a little apart. `right`: the rows sit at the screen's right edge instead,
    above the compact bar."""
    spots = _stack(sizes, left, width, bottom, right)
    full = 0
    for i, (w, h) in enumerate(sizes):
        if spots[i][1] < top:
            full += 1
            x = left + width - w - 28 * full if right else left + (width - w) // 2 + 28 * full
            spots[i] = (x, max(top, bottom - h - 28 * full))
    return spots


def fits(sizes, left, width, bottom, top, right=False):
    """Do all these windows find a place in arrange's rows, none of them overlapping?"""
    return all(y >= top for _x, y in _stack(sizes, left, width, bottom, right))


def keep_open(chat, full, folded, left, width, bottom, top, right=False):
    """How many of the cards (newest first, `full` open and `folded` as tabs) stay open beside the chat: the oldest
    fold to tabs until everything fits without covering another."""
    keep = len(full)
    while keep and not fits(chat + full[:keep] + folded[keep:], left, width, bottom, top, right):
        keep -= 1
    return keep


def _stack(sizes, left, width, bottom, right):
    """arrange's rows, as many as it takes, however high they go."""
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


def whole(card):
    """The card's markdown with its rail and columns after it: what it says, top to bottom then left to right."""
    card = card or {}
    return "\n\n".join([card.get("text") or "", card.get("rail") or ""] + list(card.get("columns") or []))


def width(card):
    """How wide the card is: CARD_W, a COL_W more for each column past the first, and its rail."""
    card = card or {}
    return CARD_W + COL_W * max(0, len(card.get("columns") or []) - 1) + (
        RAIL_W + 2 * RAIL_GAP + 1 if card.get("rail") else 0)


def links(card):
    """The card's links in order, for "open the second link"."""
    return [url for _text, url in _LINK.findall(whole(card))]


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
    for line in whole(card).splitlines():
        line = line.strip()
        if line.startswith("```"):
            if kind is None:
                kind, block = line[3:].strip().lower(), []
            else:
                said = blocks.spoken(kind, block) if kind in blocks.KINDS else _spoken_rows(block) if \
                    kind == "rows" else _spoken_people(block) if kind == "people" else \
                    [", ".join(" ".join(c.strip() for c in l.split("|")) for l in block if l.strip())] if \
                    kind in ("stats", "hero") else \
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
        lab.connect("activate-link", on_link)
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


def initials(name):
    """"Rahul Sharma" -> "RS", "upe-stations" -> "U"."""
    words = re.findall(r"[A-Za-z0-9]+", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", blocks.strip(name or "")))
    return "".join(w[0] for w in words[:2]).upper() if len(words) > 1 else (words[0][:1].upper() if words else "?")


def _cells(line):
    """(icon, who, what, when, actions) of a ```rows line."""
    cells = [c.strip() for c in line.split("|")]
    if len(cells) < 4:
        cells = [""] + cells
    return (cells + ["", "", "", "", ""])[:5]


def _actions(cell):
    """"media-playback-start-symbolic Run it -> run jarvis; ..." -> [(icon name, label, phrase)]."""
    out = []
    for part in cell.split(";"):
        head, _, phrase = part.partition("->")
        icon, _, label = head.strip().partition(" ")
        if icon and phrase.strip():
            out.append((icon, label.strip() or phrase.strip(), phrase.strip()))
    return out


def _avatar(icon, who):
    """Initials in a circle, a glyph ("#", "✉") in one, or a file-type badge for "file:pdf"."""
    if icon.lower().startswith("file:"):
        lab = Gtk.Label()
        lab.set_markup(blocks.markup("{%s}" % icon.lower()))
    else:
        lab = Gtk.Label(label=icon or initials(who))
        lab.get_style_context().add_class("avatar")
    lab.set_valign(Gtk.Align.CENTER)
    lab.set_halign(Gtk.Align.CENTER)
    return lab


def _rows(lines, first=None):
    """A ```rows block: per line [icon |] who | what | when [| actions], as a list of people, mail, chats or files.
    actions: small icon buttons before "when", "icon-name Label -> phrase" each, split by ";" (the dashboard's
    ▶ run / ■ stop / editor / browser on a project); a click says the phrase (Card._hook).
    first: the number of its first row on a card whose rows can be picked ("items"): each row then shows its number
    and is an EventBox with .row (counted from 0 across the card) for Card.show to make clickable."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    box.rows, box.actions = [], []
    for r, line in enumerate(l for l in lines if l.strip()):
        icon, who, what, when, acts = _cells(line)
        row = Gtk.Box(spacing=10)
        if first is not None:
            num = Gtk.Label(label=str(first + r + 1))
            num.get_style_context().add_class("num")
            num.set_valign(Gtk.Align.CENTER)
            row.pack_start(num, False, False, 0)
        row.pack_start(_avatar(icon, who), False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        for part, cls in ((who, "who"), (what, "what")):
            if part:
                lab = Gtk.Label(xalign=0)
                lab.set_markup(_inline(part))
                lab.connect("activate-link", on_link)
                lab.set_ellipsize(Pango.EllipsizeMode.END)
                lab.set_max_width_chars(44)
                lab.get_style_context().add_class(cls)
                text.pack_start(lab, False, False, 0)
        text.set_valign(Gtk.Align.CENTER)
        row.pack_start(text, True, True, 0)
        if when:
            lab = _label(_inline(when), "when")
            lab.set_selectable(first is None)  # a selectable label would eat the row's click
            lab.set_xalign(1)
            lab.set_valign(Gtk.Align.CENTER)
            row.pack_end(lab, False, False, 0)
        for icon_name, label, phrase in reversed(_actions(acts)):
            button = Gtk.Button.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            button.get_style_context().add_class("rowbtn")
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_tooltip_text(label)
            button.set_valign(Gtk.Align.CENTER)
            row.pack_end(button, False, False, 0)
            box.actions.append((button, phrase))
        if first is not None:
            hit = Gtk.EventBox()
            hit.get_style_context().add_class("pickrow")
            hit.add(row)
            hit.row = first + r
            box.rows.append(hit)
            row = hit
        box.pack_start(row, False, False, 0)
    return box


def _people(lines):
    """A ```people block: a name per line (and "+3"), drawn as overlapping initials with the names as tooltips."""
    box = Gtk.Box(spacing=0)
    box.get_style_context().add_class("people")
    for name in (l.strip() for l in lines if l.strip()):
        if re.fullmatch(r"\+\d+", name):
            more = _label(name, "when")
            more.set_margin_start(12)
            box.pack_start(more, False, False, 0)
            continue
        lab = _avatar("", name)
        lab.set_tooltip_text(name)
        box.pack_start(lab, False, False, 0)
    return box


def _stats(lines):
    """A ```stats block: "9 | emails" per line, as big numbers in tiles side by side (up to four)."""
    box = Gtk.Box(spacing=8, homogeneous=True)
    for line in [l for l in lines if l.strip()][:4]:
        value, _, label = (c.strip() for c in line.partition("|"))
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        tile.get_style_context().add_class("stat")
        for text, cls in ((value, "stat-value"), (label, "stat-label")):
            lab = Gtk.Label(label=text, xalign=0)
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            lab.get_style_context().add_class(cls)
            tile.pack_start(lab, False, False, 0)
        box.pack_start(tile, True, True, 0)
    return box


def _hero(lines):
    """A ```hero block, made for a card's rail: the bar's live orb (blocks.ORB) on top, a heading and a line under it
    (the lines without "|"), then ```stats tiles two by two (the "9 | running" lines)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    orb = Gtk.DrawingArea()
    orb.set_size_request(HERO_ORB, HERO_ORB)
    orb.set_halign(Gtk.Align.CENTER)

    def draw(_w, cr):
        # its own surface: a frame that fails half-way can't leave the card's context in a pushed group
        own = cairo.ImageSurface(cairo.FORMAT_ARGB32, HERO_ORB, HERO_ORB)
        k = max(0.0, min(1.0, (time.time() - blocks.FACE_AT) / blocks.FACE_IN))
        k = k * k * (3 - 2 * k)  # eased, as the bar's face fades out
        try:
            win, box = _w.get_window(), _w.get_allocation()  # where it is on screen, for its eyes to look from
            x, y = win.get_origin()[1:]
            blocks.ORB(cairo.Context(own), HERO_ORB, HERO_ORB, at=(x + box.x + HERO_ORB / 2, y + box.y + HERO_ORB / 2))
        except Exception:  # no orb (no bar) or a bad frame: an empty corner rather than a broken card
            return False
        cr.translate(HERO_ORB / 2, HERO_ORB / 2)
        cr.scale(0.6 + 0.4 * k, 0.6 + 0.4 * k)  # grows in from 60% as it fades in
        cr.set_source_surface(own, -HERO_ORB / 2, -HERO_ORB / 2)
        cr.paint_with_alpha(k)
        return False
    orb.connect("draw", draw)
    box.pack_start(orb, False, False, 4)
    for n, line in enumerate(l for l in lines if l.strip() and "|" not in l):
        lab = _label(_inline(line.strip()), "heading1" if n == 0 else "what")
        lab.set_selectable(False)  # a selectable first label opens selected
        lab.set_xalign(0.5)
        box.pack_start(lab, False, False, 0)
    stats = [l for l in lines if "|" in l]
    for pair in (stats[:2], stats[2:4]):
        if pair:
            tiles = _stats(pair)
            tiles.set_margin_top(4)
            box.pack_start(tiles, False, False, 0)
    if blocks.ORB:
        box.every = 0.05
    return box


def _spoken_rows(lines):
    out = []
    for line in lines:
        _icon, who, what, when, _acts = (blocks.strip(_LINK.sub(r"\1", c)).strip() for c in _cells(line))
        if who or what:
            out.append(", ".join(p for p in (who, what, when) if p))
    return out


def _spoken_people(lines):
    names = [l.strip() for l in lines if l.strip() and not re.fullmatch(r"\+\d+", l.strip())]
    more = next((int(l.strip()[1:]) for l in lines if re.fullmatch(r"\+\d+", l.strip())), 0)
    if not names:
        return []
    said = ", ".join(names[:-1]) + (" and " if len(names) > 1 and not more else ", " if more else "") + names[-1]
    return ["With %s%s" % (said, " and %d more" % more if more else "")]


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


def build(text, numbered=False):
    """Turn the card's markdown into a list of widgets. numbered: the card has "items", so its ```rows rows are
    numbered and can be clicked."""
    widgets, lines, i, boxes, rows = [], (text or "").splitlines(), 0, 0, 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("```"):
            kind, block, i = line[3:].strip().lower(), [], i + 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            if kind == "rows":
                chart = _rows(block, rows if numbered else None)
                rows += len([l for l in block if l.strip()])
            else:
                chart = _bars(block) if kind == "bars" else \
                _people(block) if kind == "people" else _stats(block) if kind == "stats" else \
                _hero(block) if kind == "hero" else blocks.make(kind, block)
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
            label = _label(indent + mark + "  " + _inline(item[box.end():] if box else item))
            if box:  # a click ticks it (Card._box)
                label.set_selectable(False)
                row = Gtk.EventBox()
                row.add(label)
                row.box, row.ticked, boxes = boxes, bool(box.group(1).strip()), boxes + 1
                label = row
            widgets.append(label)
        elif line.strip():
            widgets.append(_label(_inline(line)))
        i += 1
    return widgets


class Card:
    """One card window. GTK-thread only; ui.UI wraps the Deck with thread-safe calls. `last` and `visible` are safe
    to read from any thread."""

    def __init__(self, on_choice, pinned=False, deck=None, on_close=None):
        self.touched = 0.0  # when it opened or was last used: the card touched last is the one "it" is about

        def say(phrase):  # a button, row or tick clicked: its phrase ("open the first link") is about this card
            self.touched = time.monotonic()
            on_choice(phrase)
        self.on_choice = say
        self.pinned = pinned  # a pinned copy: closing it destroys it; it never moves on its own
        self.deck = deck  # the Deck that places it (None for a pinned copy)
        self.on_close = on_close  # a pinned copy: tells the Deck it's gone, so nothing updates it any more
        self.last = None
        self.visible = False
        self.user_xy = None  # where you dragged it, if you did
        self.quiet = 0.0  # seconds left alone on screen since it last opened or updated
        self._dragging = False
        self._moving = []  # drawn blocks that animate, redrawn by _tick while this card is up
        self._ticking = False
        self.folded = False  # shrunk to a title-only tab: there was no room for it (Deck._place)
        self.shown_at = time.time()  # when it opened or last changed, for "4 min ago"
        self.full_size, self.fold_size = (CARD_W, CARD_MAX_H), (FOLD_W, 90)  # last measured, open and folded
        _load_css()
        w = self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        _make_transparent(w)
        w.connect("draw", self._draw_glass)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_type_hint(Gdk.WindowTypeHint.DOCK)
        w.stick()
        w.connect("size-allocate", self._sized)
        w.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        w.connect("button-press-event", self._press)  # anywhere a button, row or text didn't take the click
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.get_style_context().add_class("panel")
        outer.get_style_context().add_class("card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_border_width(14)
        outer.pack_start(inner, True, True, 0)

        head = Gtk.Box(spacing=6)
        head.get_style_context().add_class("header")
        grip = Gtk.EventBox()  # drag the card by its title (or anywhere free); click a folded card's title to open it
        grip.connect("button-press-event", self._press)
        line = Gtk.Box(spacing=7)
        self.icon = Gtk.Image()
        self.icon.get_style_context().add_class("source")
        line.pack_start(self.icon, False, False, 0)
        self.title = Gtk.Label(xalign=0, ellipsize=3)
        self.title.get_style_context().add_class("title")
        line.pack_start(self.title, True, True, 0)
        self.age = Gtk.Label()
        self.age.get_style_context().add_class("age")
        line.pack_end(self.age, False, False, 0)
        grip.add(line)
        head.pack_start(grip, True, True, 0)
        close = Gtk.Button(label="×")
        close.get_style_context().add_class("close")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda _b: self.close())
        head.pack_end(close, False, False, 0)
        self.pin_button = Gtk.Button.new_from_icon_name("view-pin-symbolic", Gtk.IconSize.MENU)
        for cls in ("close", "pin"):
            self.pin_button.get_style_context().add_class(cls)
        self.pin_button.set_relief(Gtk.ReliefStyle.NONE)
        self.pin_button.set_tooltip_text("Pin: keep it on screen")
        self.pin_button.connect("clicked", lambda _b: self.deck and self.deck.pin(self))
        head.pack_end(self.pin_button, False, False, 0)
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

    @staticmethod
    def _draw_glass(widget, cr):
        """Clear the window for the glass. GTK drops the glass's top sheen when only part of the card repaints (the
        pin or × on hover, "now" turning "1 min ago"), leaving a dark box there, so a part repaint asks for a whole one."""
        _clear(cr)
        x0, y0, x1, y1 = cr.clip_extents()
        a = widget.get_allocation()
        if x0 > 0 or y0 > 0 or x1 < a.width or y1 < a.height:
            widget.queue_draw()
        return False

    def _hit(self, widget, ev):
        """A click on a drawn block: say the phrase it has there (the music card's heart)."""
        phrase = widget.hit(ev.x, ev.y) if ev.button == 1 else None
        if phrase:
            self.on_choice(phrase)
        return bool(phrase)

    def _box(self, n, ticked):
        """Tick (or untick) the card's n-th checkbox; a card with "tick"/"untick" phrases ("tick todo {n}") also
        says it to JARVIS, numbered from 1."""
        card = self.last or {}
        seen = [0]

        def flip(m):
            seen[0] += 1
            return m.group(1) + ("[ ]" if ticked else "[x]") if seen[0] - 1 == n else m.group(0)

        self.show(dict(card, text=_BOX.sub(flip, card.get("text") or "")))
        phrase = card.get("untick" if ticked else "tick")
        if phrase:
            self.on_choice(phrase.format(n=n + 1))
        return True

    def show(self, card):
        """Put `card` in this window (replacing what it showed)."""
        if card.get("face") and not self.pinned and not (self.visible and (self.last or {}).get("face")):
            blocks.FACE_AT = time.time()  # it opens (not a refresh): its orb grows in as the bar's fades
        self.last = card
        self.quiet = 0.0
        self.shown_at = time.time()
        for child in self.body.get_children() + self.buttons.get_children():
            child.destroy()
        icon, name = source(card)
        self.icon.set_from_icon_name(icon, Gtk.IconSize.MENU) if icon else self.icon.clear()
        self.title.set_text(card.get("title") or name or "JARVIS")
        self.title.set_tooltip_text(card.get("title") or None)  # the whole title, when it's cut short
        self.age.set_text(ago(0))
        items = card.get("items") or []
        image = card.get("image")
        if image:
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(image, CARD_W - 28, CARD_MAX_H // 2, True)
                self.body.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
            except GLib.Error:
                pass
        self._moving = []
        for widget in build(card.get("text"), numbered=bool(items)):
            self.body.pack_start(widget, False, False, 0)
            self._hook(widget, items)
        if card.get("columns") or card.get("rail"):
            row = Gtk.Box(spacing=COL_W - blocks.W, homogeneous=True)
            for text in card.get("columns") or []:
                row.pack_start(self._column(text, 8), True, True, 0)
            if card.get("rail"):
                cols, row = row, Gtk.Box(spacing=RAIL_GAP)
                side = self._column(card["rail"], 12)
                side.set_size_request(RAIL_W, -1)
                row.pack_start(side, False, False, 0)
                row.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
                row.pack_start(cols, True, True, 0)
            self.body.pack_start(row, False, False, 0)
        wide = min(BUTTONS, len(choices(card))) if card.get("columns") or card.get("rail") else 0  # a wide card: buttons in one row,
        self.buttons.set_halign(Gtk.Align.START if wide else Gtk.Align.FILL)  # each as long as it says
        self.buttons.set_min_children_per_line(max(1, wide))
        self.buttons.set_max_children_per_line(max(3, wide))
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
        self.icon.set_visible(bool(icon))
        self.pin_button.set_visible(not self.pinned)
        self._fold_widgets()
        # Wrapped labels don't pass their height up through a ScrolledWindow, so measure it: grow to fit,
        # scroll beyond CARD_MAX_H.
        _, natural = self.body.get_preferred_height_for_width(width(card) - 34)
        self.scroll.set_min_content_height(min(natural, CARD_MAX_H))
        self.win.resize(FOLD_W if self.folded else width(card), 1)  # shrink to fit the new content
        self.visible = True
        self._place()
        if self._moving and not self._ticking:
            self._ticking = True
            GLib.timeout_add(50, self._tick)
        return False

    def _column(self, text, spacing):
        """A column of a card's "columns" or its "rail": `text` built, its widgets working."""
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        for widget in build(text):
            col.pack_start(widget, False, False, 0)
            self._hook(widget, [])
        return col

    def _hook(self, widget, items):
        """Make a built widget work: clickable rows and their icon buttons, a drawn block's hit spots, checkboxes,
        animation."""
        for button, phrase in getattr(widget, "actions", []):
            button.connect("clicked", lambda _b, p=phrase: self.on_choice(p))
        for hit in getattr(widget, "rows", []):
            if hit.row < len(items):
                self._clickable(hit, items[hit.row])
        if getattr(widget, "hit", None):
            widget.connect("button-press-event", self._hit)
        if hasattr(widget, "box"):
            widget.connect("button-press-event", lambda w, ev: ev.button == 1 and self._box(w.box, w.ticked))
            widget.connect("realize", lambda w: w.get_window().set_cursor(
                Gdk.Cursor.new_from_name(w.get_display(), "pointer")))
        if getattr(widget, "every", None):
            widget.due = 0.0
            self._moving.append(widget)

    def _clickable(self, hit, phrase):
        """A row of the card's list: a click says its phrase ("read email 18c2…"), as "read the second one" would."""
        hit.connect("button-press-event", lambda _w, ev: ev.button == 1 and (self.on_choice(phrase) or True))
        hit.connect("enter-notify-event", lambda w, _ev: w.get_style_context().add_class("hover"))
        hit.connect("leave-notify-event", lambda w, _ev: w.get_style_context().remove_class("hover"))
        hit.connect("realize", lambda w: w.get_window().set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer")))

    def _fold_widgets(self):
        """Show the body, buttons and pin, or only the title line of a folded card."""
        self.scroll.set_visible(not self.folded)
        self.buttons.set_visible(not self.folded and bool(choices(self.last)))
        self.buttons.queue_resize()  # shown again after a card without choices, it kept measuring 0 high: cut off
        self.pin_button.set_visible(not self.folded and not self.pinned)
        self.title.set_tooltip_text("Click to open it" if self.folded else (self.last or {}).get("title") or None)
        self.win.set_size_request(FOLD_W if self.folded else width(self.last), -1)

    def fold(self, on):
        """Shrink to a title-only tab (no room for it on screen), or open it back up."""
        if self.folded == on:
            return
        self.folded = on
        self._fold_widgets()
        self.win.resize(FOLD_W if on else width(self.last), 1)

    def _sized(self, win, alloc):
        """Remember its real size, open or folded, for Deck._place to fit the row with."""
        if self.folded:
            self.fold_size = (alloc.width, alloc.height)
        else:
            self.full_size = (alloc.width, alloc.height)
        self._place()

    def refresh_age(self):
        self.age.set_text(ago(time.time() - self.shown_at))

    def _tick(self):
        """Redraw the animated blocks that are due; stops once the card is hidden or has none."""
        if not self.visible or not self._moving:
            self._ticking = False
            return False
        now = time.time()
        due = [area for area in self._moving if now >= area.due]
        for area in due:
            area.due = now + area.every
        if due:  # the whole card: a part repaint loses the glass's sheen and flickers a dark box (_draw_glass)
            self.win.queue_draw()
        return True

    def close(self):
        if self.pinned:
            self.win.destroy()
        else:
            self.win.hide()
        self.visible = False
        if self.on_close:
            self.on_close(self)
        if self.deck:
            self.deck._closed(self)
        return False

    def _press(self, widget, ev):
        if ev.button != 1:
            return False
        if self.folded and self.deck:
            self.deck.unfold(self)
            return True
        x, y = self.win.get_position()
        self._grab = (ev.x_root - x, ev.y_root - y, ev.x_root, ev.y_root)
        self._dragging = True
        self.win.add_tick_callback(lambda *_: self._follow())  # once per screen frame: smooth, no timer drift
        return True

    def _follow(self):
        """Keep under the pointer while the button is held (the window manager won't drag a DOCK window); once
        let go, a card that really moved stays where you put it, and the rest close up."""
        pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
        _w, px, py, mask = Gdk.get_default_root_window().get_device_position(pointer)
        ox, oy, sx, sy = self._grab
        if self.user_xy or abs(px - sx) > 4 or abs(py - sy) > 4:  # a click that wobbles a little isn't a drag
            self.user_xy = (int(px - ox), int(py - oy))
            self.win.move(*self.user_xy)
        if mask & Gdk.ModifierType.BUTTON1_MASK:
            return True
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
        GLib.timeout_add_seconds(20, self._ages)

    def _ages(self):
        for w in self.open + self.pins:
            w.refresh_age()
        return True

    @property
    def visible(self):
        return bool(self.open)

    def _newest(self):
        """The window opened or used last (a click, or a button named by voice), or None."""
        return max(list(self.open), key=lambda w: w.touched, default=None)

    def current(self):
        """The card opened or used last, else the last one shown."""
        w = self._newest()
        return w.last if w else self.last

    def cards(self):
        """The cards on screen, the current one first."""
        return [w.last for w in sorted(list(self.open), key=lambda w: -w.touched)]

    def use(self, card):
        """`card` is the one being acted on (a button named by voice): what its phrase says about "the card" is
        about it. Any thread."""
        for w in list(self.open):
            if w.last is card:
                w.touched = time.monotonic()

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
        if not same and card.get("id"):
            pinned = next((w for w in self.pins if (w.last or {}).get("id") == card.get("id")), None)
            if pinned:
                return pinned.show(card)  # a pinned copy follows its card (the song) in place; the others stay
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
        win.touched = time.monotonic()
        win.fold(False)
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
            self._newest().show(card)
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
            targets = [w for w in [self._newest()] if w]  # the current one: opened or used last
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

    def unfold(self, win):
        """Open a folded card back up, as the newest: the oldest open one folds if there's no room."""
        if win in self.open:
            self.open.remove(win)
            self.open.append(win)
            self.last = win.last
        win.touched = time.monotonic()
        win.quiet = 0.0
        win.fold(False)
        self._place()
        return False

    def pin(self, win=None):
        """Keep a copy of the newest card (or this one) on screen; it stays when the others are closed or hidden."""
        if not self.open:
            return False
        win = win if win in self.open else self.open[-1]
        copy = Card(self.on_choice, pinned=True, on_close=lambda c: c in self.pins and self.pins.remove(c))
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

    def face_up(self):
        """Is a card that draws the orb ("face": true, the dashboard) on screen? The bar then shows none."""
        return any((w.last or {}).get("face") for w in self.open + self.pins if w.visible)

    def _place(self):
        """The chat and the cards in their row, centred or by the compact bar; a card you dragged stays where it is."""
        if self._placing:
            return
        self._placing = True
        try:
            auto = [w for w in reversed(self.open) if not w.user_xy and not w._dragging and w.win.get_visible()]
            top = [w for w in auto if (w.last or {}).get("place") == "top"]
            auto = [w for w in auto if w not in top]
            for w in top:
                w.fold(False)  # it has the top to itself: never a tab
            for w in self.open:
                if w.user_xy and not w._dragging:
                    w.fold(False)  # one you moved yourself is never folded
                    Card._place(w)
            geo = Gdk.Display.get_default().get_monitor(0).get_workarea()
            chat = [(PANEL_W, PANEL_H)] if self.chat_visible() else []
            room = (geo.x, geo.width, self.bottom() + GLOW, geo.y, self.compact())

            # Real widths: a card with a drawn block comes out a few pixels wider, and the gaps must still be equal.
            full = [(max(CARD_W, w.full_size[0]), w.full_size[1]) for w in auto]
            folded = [w.fold_size for w in auto]
            keep = keep_open(chat, full, folded, *room)
            for i, w in enumerate(auto):
                w.fold(i >= keep)
            spots = arrange(chat + full[:keep] + folded[keep:], *room)
            spot = spots.pop(0) if chat else None
            if spot and spot != self._chat_spot:
                self._slide("chat", self._chat_spot, spot)
            if not spot:
                self._glide.pop("chat", None)
            self._chat_spot = spot
            spots += [(geo.x + (geo.width - w.full_size[0]) // 2, geo.y + TOP_GAP) for w in top]
            for w, (x, y) in zip(auto + top, spots):
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
