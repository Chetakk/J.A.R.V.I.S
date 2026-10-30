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


@pytest.mark.parametrize("text, answer", [
    ("what is two plus two", "4."),
    ("twelve times seven", "84."),
    ("one hundred and twenty plus five", "125."),
    ("three point five times two", "7."),
    ("half of 90", "45."),
    ("one third of 90", "30."),
    ("two thirds of 90", "60."),
    ("double 45", "90."),
    ("20 percent off 500", "400."),
    ("increase 200 by 10 percent", "220."),
    ("reduce 80 by 25 percent", "60."),
    ("500 plus 18 percent gst", "590."),
    ("15 percent tip on 80", "12 tip, so 92 in total."),
    ("what percent is 30 of 120", "25 percent."),
    ("30 is what percent of 120", "25 percent."),
    ("cube root of 27", "3."),
    ("10 factorial", "3,628,800."),
    ("average of 4, 6 and 8", "6."),
    ("sum of 1 2 and 3", "6."),
    ("sin 30", "0.5."),
    ("log of 1000", "3."),
])
def test_everyday_maths(jv, text, answer):
    jv.do(text)
    assert jv.said == [answer]
    assert not jv.claude.calls


def test_mixed_units_and_spoken_numbers(jv):
    jv.do("5 feet 10 inches in cm")
    assert jv.said == ["5 feet 10 inches is 177.8 cm."]
    jv.do("five miles in km")
    assert jv.said[-1] == "5 miles is 8.047 km."


@pytest.mark.parametrize("text", ["tan 90", "100 dollars in rupees", "what is a plus b"])
def test_leaves_what_it_cannot_do(jv, text):
    jv.do(text)
    assert not any(s.endswith(".") and s[:1].isdigit() for s in jv.said)
