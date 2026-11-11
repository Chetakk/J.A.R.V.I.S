"""The chat panel and the cards' new parts, built as real GTK widgets but never shown (no window opens)."""
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ui import card as C  # noqa: E402
from ui import window as W  # noqa: E402


# The harness unsets DISPLAY (tests/conftest.py), so these skip there; run them by hand with
# `pytest --noconftest tests/test_chat_panel.py` in the desktop session (they still never show a window).
widgets = pytest.mark.skipif(not Gtk.init_check(None)[0], reason="no display to build GTK widgets on")


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "HISTORY_FILE", str(tmp_path / "chat.json"))
    ui = W.UI.__new__(W.UI)
    ui._build_panel()
    return ui


def rows(ui):
    return ui.msgs.get_children()


def test_times_in_the_chat():
    now = time.mktime((2026, 11, 11, 15, 0, 0, 0, 0, -1))
    assert W.stamp(now - 3600, now) == "14:00"
    assert W.stamp(now - 86400, now) == "Yesterday 15:00"
    assert W.stamp(now - 3 * 86400, now) == "Sun 8 Nov 15:00"


@widgets
def test_jarvis_and_claude_lines_are_formatted(panel):
    assert W._markup("**Done** in `2 s`, [the doc](https://example.com/d)") == \
        '<b>Done</b> in <tt>2 s</tt>, <a href="https://example.com/d">the doc</a>'
    assert W._markup("**a *b** c*") is None  # tags that cross: shown as typed
    panel._add_chat("claude", "**Moved** it")
    label = rows(panel)[-1].get_children()[-1].get_children()[0]
    assert label.get_text() == "Moved it" and label.get_use_markup()
    panel._add_chat("you", "**not** formatted")
    assert rows(panel)[-1].get_children()[0].get_text() == "**not** formatted"


@widgets
def test_heard_lines_in_a_row_fold_into_one(panel):
    for said in ("heard 'Hey.' -> no name", "heard 'ok' -> no name", "heard 'so' -> no name"):
        panel._add_chat("heard", said)
    fold = rows(panel)[-1]
    assert isinstance(fold, Gtk.Expander) and fold.get_label_widget().get_text() == "3 heard · heard 'so' -> no name"
    assert len(fold.get_child().get_children()) == 3
    assert panel._chat_text().count("heard") == 3  # "copy whole chat" still has every line
    panel._add_chat("jarvis", "Yes?")
    panel._add_chat("heard", "heard 'hm' -> no name")
    assert isinstance(rows(panel)[-1], Gtk.Label)  # a new run starts on its own


@widgets
def test_a_gap_puts_the_time_in(panel):
    t = time.time()
    panel._add_chat("you", "one", t)
    panel._add_chat("jarvis", "two", t + 60)
    panel._add_chat("you", "three", t + 60 + W.TIME_GAP + 1)
    marks = [r for r in rows(panel) if isinstance(r, Gtk.Label) and r.get_style_context().has_class("time")]
    assert len(marks) == 2  # the first line, and after the gap


@widgets
def test_typing_dots_stay_at_the_bottom_until_done(panel):
    panel._set_typing("thinking")
    panel._add_chat("jarvis", "Working on it")
    assert rows(panel)[-1] is panel._typing_row
    panel._set_typing("claude")
    assert panel._typing_row.get_style_context().has_class("claude")
    panel._set_typing("idle")
    assert panel._typing_row is None and panel._typing_timer is None


@widgets
def test_up_recalls_what_you_said_too(panel):
    panel._add_chat("you", "open youtube")  # said, not typed
    panel._add_chat("you", "what time is it")
    assert panel._recall(-1) and panel.entry.get_text() == "what time is it"
    panel._recall(-1)
    assert panel.entry.get_text() == "open youtube"


@widgets
def test_one_selection_at_a_time(panel, monkeypatch):
    panel._add_chat("jarvis", "first message")
    panel._add_chat("jarvis", "second message")
    a, b = panel._chat_labels()
    a.select_region(0, 5)
    b.select_region(7, 14)  # selecting in the second clears the first
    assert W._selection(a) is None and W._selection(b) == (7, 14)
    copied = []
    monkeypatch.setattr(Gtk.Clipboard, "get", lambda _sel: type("Clip", (), {"set_text": lambda s, t, n: copied.append(t)})())
    key = type("Key", (), {"keyval": Gdk.KEY_c, "state": Gdk.ModifierType.CONTROL_MASK})()
    assert panel._panel_key(None, key) and copied == ["message"]  # exactly what was selected


@widgets
def test_history_kept_for_a_restart(panel, tmp_path, monkeypatch):
    panel._add_chat("you", "show my mail")
    panel._add_chat("heard", "heard 'hm' -> no name")  # not saved
    panel._add_chat("jarvis", "Nine new.")
    panel._add_chat("you", "my bank pin")
    panel._add_chat("jarvis", "Saved it.", None, False)  # private: never saved
    panel._forget_request()  # ...and the request it answered dropped too
    panel._save_history()
    again = W.UI.__new__(W.UI)
    again._build_panel()
    assert again._chat_text() == "You: show my mail\nJARVIS: Nine new."
    assert again._sent == ["show my mail"]
    assert oct((tmp_path / "chat.json").stat().st_mode & 0o777) == "0o600"


def test_cards_that_dont_fit_fold_oldest_first():
    room = (0, 1920, 1000, 25)
    chat, full, tab = [(390, 640)], [(460, 520)] * 5, [(C.FOLD_W, 80)] * 5
    assert C.keep_open(chat, full[:3], tab[:3], *room) == 3  # chat + three cards fill one row
    assert C.keep_open(chat, full, tab, *room) == 3  # five: the two oldest become tabs, in the row above
    assert C.keep_open([], [(460, 300)] * 6, tab[:1] * 6, *room) == 6  # short ones stack in two rows
    assert C.fits([(460, 300)] * 8, *room) and not C.fits([(460, 520)] * 8, *room)


@widgets
def test_card_rows_are_numbered_and_named_by_source():
    text = "```rows\nRS | Rahul | Re: budget | 9:12\nAK | Anita | Photos | 8:40\n```"
    box = [w for w in C.build(text, numbered=True) if hasattr(w, "rows")][0]
    assert [h.row for h in box.rows] == [0, 1]
    assert [h.get_child().get_children()[0].get_text() for h in box.rows] == ["1", "2"]
    assert not [w for w in C.build(text) if getattr(w, "rows", None)]  # no items: plain rows, no numbers
    assert C.source({"id": "drive-share-3"}) == ("folder-remote-symbolic", "Drive")
    assert C.source({"id": "zzz"}) == (None, None)
    assert [C.ago(s) for s in (5, 130, 7300)] == ["now", "2 min ago", "2 h ago"]


@widgets
def test_clicking_a_card_row_says_its_phrase():
    said, card = [], C.Card.__new__(C.Card)
    card.on_choice = said.append
    box = [w for w in C.build("```rows\nRS | Rahul | Re: budget | 9:12\nAK | Anita | Photos | 8:40\n```", True)
           if hasattr(w, "rows")][0]
    for hit, phrase in zip(box.rows, ("read email a", "read email b")):
        card._clickable(hit, phrase)
    press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    press.button.button = 1
    box.rows[1].emit("button-press-event", press)
    assert said == ["read email b"]
