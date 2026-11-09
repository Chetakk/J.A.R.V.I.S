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
    bar.mode, bar.ignored, bar.words, bar.understood = "full", 0, "", ""
    assert set(bar._zones(560)) == {"chat"}  # nothing said yet: no → to show what was understood
    bar.words = "open youtube"
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


def test_the_full_bar_fits_what_it_says_and_grows_as_you_talk_up_to_full_width():
    from types import SimpleNamespace
    import cairo
    from ui.bar import FULL_W, Bar

    bar = Bar.__new__(Bar)  # no window and no talking to the shell
    bar.ui, bar.mode, bar.show_understood, bar.ignored = SimpleNamespace(_shown=("listening",)), "full", True, 0
    bar.ui.mutes = {"mic": False}
    bar.words = bar.understood = bar.reply = ""
    bar._words_at, bar._measure = 0.0, cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    listening = bar._fit()
    bar.words = "open youtube"
    talking = bar._fit()
    bar.ignored = 2
    assert listening < talking < bar._fit() < FULL_W  # the ignored count gets its room too
    bar.words = "play the one about the robots that fall in love on the moon please " * 3
    assert bar._fit() == FULL_W


def test_the_follow_up_line_draws_and_lights_the_bar_while_time_is_left():
    from types import SimpleNamespace
    import cairo
    from ui.bar import FULL_W, HEIGHT, Bar

    bar = Bar.__new__(Bar)
    bar.ui, bar._follow_total = SimpleNamespace(_voice=0.5), 6.0

    def lit(t):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, FULL_W, HEIGHT)
        bar._paint_follow(cairo.Context(surface), FULL_W, t)
        return any(surface.get_data())

    bar._follow_end = 106.0
    assert lit(101.0) and lit(105.9)  # whole and almost out (the fast beat)
    assert not lit(106.0)  # time's up: nothing drawn


def test_with_the_compact_bar_the_row_sits_at_the_right_with_the_chat_nearest_it():
    from ui.card import arrange

    def spots(*widths):
        return [x for x, _y in arrange([(w, 100) for w in widths], 0, 1920, 1000, 25, right=True)]

    assert spots(460) == [1460]  # flush with the right edge, like the bar
    assert spots(390, 460) == [1530, 1084]  # the chat at the far right, the card left of it
    many = arrange([(460, 400)] * 9, 0, 1920, 1000, 25, right=True)
    assert many[8] == (1920 - 460 - 28, 572)  # overflow stacks from the right too


def test_cards_and_the_chat_glide_to_their_new_spot_and_a_dragged_card_is_let_go():
    from types import SimpleNamespace
    from ui.card import Deck

    moves = []
    deck = Deck(lambda: 0, lambda: True, lambda x, y: moves.append(("chat", x, y)), None)
    card = type("Card", (), {})()  # hashable, like a real Card
    card.user_xy, card._dragging = None, False
    card.win = SimpleNamespace(get_visible=lambda: True, move=lambda x, y: moves.append(("card", x, y)))
    deck._slide("chat", None, (500, 300))
    assert moves == [("chat", 500, 300)] and not deck._glide  # nothing to slide from: put there at once
    deck._glide = {"chat": [500, 300, 1500, 300], card: [0, 0, 1000, 0]}
    deck._glide_step()
    assert moves[1:] == [("chat", 820, 300), ("card", 320, 0)]  # a third of the way, like the bar
    while deck._glide_step():
        card._dragging = True  # you grab the card: the chat goes on, the card is yours
    assert moves[-1] == ("chat", 1500, 300) and ("card", 1000, 0) not in moves


def test_idle_the_bar_says_how_to_wake_jarvis_or_why_it_cant_hear_and_breathes():
    from types import SimpleNamespace
    from ui.bar import Bar, _breath

    bar = Bar.__new__(Bar)
    bar.ui = SimpleNamespace(mutes={"mic": False})
    assert [bar._waiting(s) for s in ("idle", "listening", "off", "thinking")] == [
        "Say “Jarvis”", "Listening…", "Voice off", ""]
    bar.ui.mutes["mic"] = True
    assert bar._waiting("idle") == bar._waiting("off") == "Mic off"
    assert _breath(0) == 0 and abs(_breath(2) - 1) < 1e-9 and abs(_breath(4)) < 1e-9  # one breath every 4 s


def test_the_idle_eyes_blink_double_blink_and_glance_and_say_when_they_need_fast_frames():
    from ui.bar import BLINK, _eyes

    assert _eyes(5 * BLINK + 1.0) == (1.0, 0.0, False)  # open, looking ahead, still: the slow frame rate will do
    assert _eyes(5 * BLINK + 0.08)[0] < 1e-9  # shut halfway through a blink
    assert _eyes(3 * BLINK + 0.40)[0] < 1e-9  # every third blink is a double one
    assert _eyes(4 * BLINK + 0.40)[0] == 1.0
    assert abs(_eyes(13 + 8.0)[1]) == 1.0 and _eyes(13 + 8.0)[2]  # glancing aside


@pytest.mark.parametrize("who,text,mood", [
    ("you", "Thanks Jarvis", "happy"), ("you", "good job", "happy"), ("you", "you're so stupid", "angry"),
    ("you", "I'm really sad today", "sad"), ("jarvis", "Sorry, that didn't work.", "sad"),
    ("jarvis", "I couldn't find a place called Narnia.", "sad"), ("jarvis", "You're welcome.", "happy"),
    ("you", "open youtube", None), ("jarvis", "Opening YouTube.", None), ("jarvis", "thanks", None)])
def test_what_is_said_gives_the_eyes_a_mood(who, text, mood):
    from ui.bar import mood_for

    assert mood_for(who, text) == mood


def test_moods_show_once_idle_fade_out_and_a_second_failure_is_stress():
    from types import SimpleNamespace
    from ui.bar import MOOD_SECS, Bar

    bar = Bar.__new__(Bar)
    bar.ui, bar.mode, bar.mood, bar._mood_seen, bar._sorry = SimpleNamespace(_shown=("speaking",)), "full", None, None, []
    bar._feel("jarvis", "Sorry, that didn't work.")
    assert bar.mood == "sad" and bar._mood_now(100.0) == (None, 0.0)  # waits while JARVIS is still talking
    bar._feel("jarvis", "Sorry, that didn't work either.")
    assert bar.mood == "stressed"
    bar._feel("jarvis", "Opening it.")  # JARVIS's plain words leave the mood alone
    bar.ui._shown = ("idle",)
    assert bar._mood_now(100.0) == ("stressed", 0.0) and bar._mood_now(101.0) == ("stressed", 1.0)
    assert bar._mood_now(100.0 + MOOD_SECS + 0.1) == (None, 0.0) and bar.mood is None
    bar._feel("you", "thanks")
    bar._feel("you", "open youtube")  # what you say next sets it afresh
    assert bar.mood is None


def test_the_eyes_look_towards_the_pointer_less_so_when_it_is_close():
    from ui.bar import look_at

    assert look_at(1000, 500, 500, 500) == (1.0, 0.0)  # far right
    x, y = look_at(400, 400, 500, 500)  # up-left
    assert x == y and -1 < x < 0
    assert look_at(510, 500, 500, 500) == (10 / 250, 0.0)  # near: a small turn


@pytest.mark.parametrize("mood", [None, "happy", "sad", "angry", "stressed"])
def test_each_mood_draws_its_eyes(mood):
    import cairo
    from ui.window import UI

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 56, 56)
    UI._pet_eyes(cairo.Context(surface), 28, 28, 25, 1.0, 1.0, mood, 1.0, 3.0)
    assert any(surface.get_data())


def test_the_eyes_spring_to_the_pointer_smoothly_the_same_at_any_frame_rate_and_never_overshoot():
    from ui.bar import spring

    def run(fps, seconds=0.6):
        pos, vel, path = [0.0], [0.0], []
        for _ in range(int(seconds * fps)):
            pos, vel = spring(pos, vel, [1.0], 1 / fps)
            path.append(pos[0])
        return path

    fast, slow = run(60), run(10)
    assert all(0 <= p <= 1 for p in fast + slow)  # no overshoot
    assert fast[0] < 0.05  # starts gently, no snap
    assert abs(fast[-1] - slow[-1]) < 1e-9 and fast[-1] > 0.98  # same place at the same time, whatever the fps
    steps = [b - a for a, b in zip(fast, fast[1:])]
    assert steps.index(max(steps)) > 0  # speeds up, then slows down
