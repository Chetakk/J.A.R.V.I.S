"""Maths and units (skills/maths.py): worked out locally, never guessed; what it can't do goes on to Claude."""
import pytest


@pytest.mark.parametrize("text, answer", [
    ("what's 18% of 2400", "432."),
    ("12 times 7", "84."),
    ("what is 100 divided by 8", "12.5."),
    ("square root of 81", "9."),
    ("2 to the power of 10", "1,024."),
    ("calculate 1234567 times 3", "3,703,701."),
])
def test_works_out_sums(jv, text, answer):
    jv.do(text)
    assert jv.said == [answer]
    assert not jv.claude.calls and not jv.shell.calls


@pytest.mark.parametrize("text, line", [
    ("50 miles in km", "50 miles is 80.47 km."),
    ("how many feet in 3 metres", "3 metres is 9.843 feet."),
    ("100 fahrenheit in celsius", "100 fahrenheit is 37.78 celsius."),
    ("convert 2 gb to mb", "2 gb is 2,000 mb."),
])
def test_converts_units(jv, text, line):
    jv.do(text)
    assert jv.said == [line]
    assert not jv.claude.calls


# --- failure cases: never a wrong number ---

def test_dividing_by_zero_is_handed_on_not_answered(jv):
    jv.do("what's 5 divided by 0")
    assert not jv.says("infinity") and not jv.says("undefined")
    assert jv.claude.asked("5 divided by 0")


def test_units_that_dont_go_together_are_not_converted(jv):
    jv.do("5 kg in miles")
    assert not jv.says("5 kg is")
    assert jv.claude.asked("5 kg in miles")


def test_currency_goes_to_claude(jv):
    jv.do("100 dollars in rupees")
    assert jv.claude.asked("100 dollars in rupees")
    assert not jv.says("100 dollars is")


def test_huge_powers_are_not_computed_locally(jv):
    jv.do("what's 9 to the power of 99999")  # would hang JARVIS working it out
    assert jv.claude.asked("power of 99999")
