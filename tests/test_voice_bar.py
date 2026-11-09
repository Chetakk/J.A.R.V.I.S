"""The voice bar: when it shows, what's clickable, and what JARVIS tells it."""
import pytest


def test_the_bar_is_full_only_while_it_is_in_use_and_a_long_claude_run_gives_the_dock_back():
    from ui.window import LONG_CLAUDE, bar_mode

    assert bar_mode(0, True, False, False, False, False) == "full"  # talking
    assert bar_mode(0, False, True, False, False, False) == "full"  # the chat is open
    assert bar_mode(0, False, False, True, False, False) == "full"  # the few seconds after
    assert bar_mode(0, False, False, False, True, False) == "compact"  # only a card left: the dock's back
    assert bar_mode(0, False, False, False, False, True) == "compact"  # compact mode, idle
    assert bar_mode(0, True, True, False, False, True) == "compact"  # compact mode, even while talking
    assert bar_mode(0, False, False, False, False, False) == "hidden"
    assert bar_mode(LONG_CLAUDE + 1, True, True, False, False, False) == "compact"


def test_the_comet_goes_clockwise_round_the_rim():
    from ui.bar import rim_point

    assert rim_point(0, 0, 0, 30, 10) == (0, 0)
    assert rim_point(0.25, 0, 0, 30, 10) == (20, 0)
    assert rim_point(0.5, 0, 0, 30, 10) == (30, 10)
    assert rim_point(0.9375, 0, 0, 30, 10) == (0, 5)


def test_a_zoomed_wallpaper_is_cropped_evenly_on_its_longer_side():
    from ui.bar import cover

    assert cover(3840, 1080, 1920, 1080) == (3840, 1080, 960, 0)  # wider than the screen
    assert cover(1000, 1000, 1920, 1080) == (1920, 1920, 0, 420)  # squarer: scaled up to fill it


def test_windows_share_a_row_centred_on_the_screen():
    from ui.card import arrange

    def spots(*widths):
        return [x for x, _y in arrange([(w, 100) for w in widths], 0, 1920, 1000, 25)]

    # Windows overlap by their glow margins (2 * GLOW = 24 px), so the glass in them is 10 px apart.
    assert spots(460) == [730]  # one: the exact middle
    assert spots(460, 460) == [953, 507]  # two: mirrored either side of it
    assert spots(460, 460, 460) == [1176, 730, 284]  # three: right, middle, left
    assert spots(390, 460) == [988, 542]  # the chat counts as one of them, at the right


def test_tall_windows_sit_on_the_same_line_and_extra_rows_stack_then_overlap():
    from ui.card import arrange

    assert [y for _x, y in arrange([(390, 640), (460, 200)], 0, 1920, 1000, 25)] == [360, 800]
    many = arrange([(460, 400)] * 9, 0, 1920, 1000, 25)  # four to a row: rows at 600 and 214, the last one won't fit
    assert [y for _x, y in many[:8]] == [600] * 4 + [214] * 4
    assert many[8] == (758, 572)


def test_compact_says_what_is_happening():
    from types import SimpleNamespace
    from ui.bar import Bar

    bar = Bar.__new__(Bar)
    bar.ui, bar.words, bar.reply = SimpleNamespace(long_claude=lambda: 0), "open youtube", "Sure."
    assert [bar._compact_text(s) for s in ("idle", "listening", "thinking", "speaking", "claude")] == [
        "Ready", "Listening…", "open youtube", "Sure.", "Asking Claude…"]
    bar.words = ""
    assert bar._compact_text("thinking") == "Thinking…"
    bar.ui = SimpleNamespace(long_claude=lambda: 75.5)
    assert bar._compact_text("claude") == "Claude is working  1:15"


def test_a_reply_waits_until_your_words_have_been_up_a_moment():
    from ui.bar import WORDS_HOLD, Bar

    bar = Bar.__new__(Bar)
    bar.reply, bar._words_at = "Sure.", 100.0
    assert not bar._reply_up(100.0 + WORDS_HOLD - 0.1)
    assert bar._reply_up(100.0 + WORDS_HOLD)
    bar.reply = ""
    assert not bar._reply_up(500.0)


def test_only_the_full_bar_has_buttons_and_the_ignored_count_shows_when_there_is_one():
    from ui.bar import Bar

    bar = Bar.__new__(Bar)
    bar.mode, bar.ignored = "compact", 3
    assert bar._zones(560) == {}
    bar.mode, bar.ignored = "full", 0
    assert set(bar._zones(560)) == {"chat", "understood"}
    bar.ignored = 2
    zones = bar._zones(560)
    assert set(zones) == {"chat", "ignored", "understood"}
    assert zones["understood"][1] <= zones["ignored"][0] and zones["ignored"][1] <= zones["chat"][0]


@pytest.mark.parametrize("outcome,counted", [("ignored (my own voice)", True), ("skipped 3 s of speech", True),
                                             ("couldn't make out words", True), ("command", False)])
def test_only_what_jarvis_passed_over_is_counted(jv, outcome, counted):
    jv.j.show_hearing = True
    jv.j.heard("hello there", outcome)
    assert (("count_ignored", ()) in jv.ui.calls) is counted


def test_hide_what_you_understood_switches_it_off_and_on(jv):
    jv.do("hide what you understood")
    assert ("set_understood", (False,)) in jv.ui.calls
    jv.do("show what you understood")
    assert ("set_understood", (True,)) in jv.ui.calls


def test_understood_shows_the_phrase_but_not_one_with_a_blank(jv):
    jv.j.understood("open youtube")
    jv.j.understood("set volume to {n}")
    assert [c for c in jv.ui.calls if c[0] == "show_understood"] == [("show_understood", ("open youtube",))]
