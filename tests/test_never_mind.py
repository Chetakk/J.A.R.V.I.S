"""Never mind (core/persona.py NEVER_MIND): "nevermind nothing leave it" and its friends drop the request, without
Claude; a sentence that only starts like one ("leave it open") is still a request."""
import pytest

NEVER_MIND = [
    "never mind", "nevermind, it's fine", "forget it", "forget about it", "just forget it", "forget i said anything",
    "forget i asked", "ignore that", "ignore me", "disregard that", "leave it", "just leave it", "leave it be",
    "leave it alone", "let it be", "let it go", "skip it", "skip that", "drop it", "don't bother", "cancel",
    "cancel that", "cancel it", "scratch that", "abort", "no wait, cancel", "undo that request", "call it off",
    "hold off", "nothing", "nothing, sorry", "it's nothing", "no, nothing", "not needed", "no need",
    "no need anymore", "it's okay, nothing", "never mind, i got it", "i'll do it myself", "i changed my mind",
    "changed my mind", "actually no", "actually, never mind", "on second thought, no", "not now", "maybe later",
    "rehne do", "chhodo", "kuch nahi", "nevemind nothing leave it", "for now let it be nothing just ignore it",
]
REPLIES = ("Very well.", "As you wish", "Alright, never mind then.", "Of course", "No problem.", "Consider it forgotten.")


@pytest.mark.parametrize("said", NEVER_MIND)
def test_never_mind_drops_it(jv, said):
    jv.do(said)
    assert any(line.startswith(REPLIES) for line in jv.said) and not jv.claude.calls and not jv.shell.calls


@pytest.mark.parametrize("said", ["leave it open", "nothing works", "let it go song", "abort the download"])
def test_a_request_that_starts_like_one_is_not_dropped(jv, said):
    jv.claude.reply("Sure.")
    jv.do(said)
    assert not any(line.startswith(REPLIES) for line in jv.said)


def test_cancel_that_still_undoes_what_was_just_done(jv):
    jv.do("volume up")
    jv.clear()
    jv.do("cancel that")
    assert any("set-sink-volume @DEFAULT_SINK@ -10%" in c for c in jv.shell.calls)


def test_ask_claude_goes_to_claude_not_a_command(jv):
    jv.claude.reply("Sure.")
    jv.do("ask claude what i mean")
    assert jv.claude.calls and not jv.questions and not jv.shell.calls


def test_never_mind_to_x_or_y_drops_it_without_claude(jv):
    jv.j.intents.candidates = lambda text: [("open claude", 0.7), ("open new chat on claude", 0.69)]
    jv.j.intents.match = lambda text: (None, 0)
    jv.do("blorp the claude thing", "nevermind leave it")
    assert jv.questions == ["Open claude, or open new chat on claude?"]
    assert not jv.claude.calls and any(line.startswith(REPLIES) for line in jv.said)


@pytest.mark.parametrize("said", ["can you ask claude what i mean", "please ask claude, is that right"])
def test_ask_claude_after_a_lead_in_goes_to_claude(jv, said):
    jv.claude.reply("Sure.")
    jv.do(said)
    assert jv.claude.calls and not jv.questions and not jv.shell.calls
