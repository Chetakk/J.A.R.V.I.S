"""A number said as a word means the same as its digits (core/numbers.py), everywhere JARVIS matches a request."""
from core import numbers


def test_words_and_digits_convert_both_ways():
    assert numbers.as_digits("go to workspace three") == "go to workspace 3"
    assert numbers.as_digits("volume twenty-five") == "volume 25"
    assert numbers.as_words("go to workspace 3") == "go to workspace three"
    assert numbers.as_words("speed 1.5 at 5:30") == "speed 1.5 at 5:30"


def test_one_as_a_thing_stays_a_word():
    for said in ("close this one", "the third one", "no one", "someone"):
        assert numbers.as_digits(said) == said


def test_a_number_word_reaches_the_command_its_digits_would(jv):
    jv.do("volume thirty")
    jv.do("volume 30")
    ran = jv.shell.ran(r"30")
    assert len(ran) == 2 and ran[0] == ran[1]


def test_a_choice_answered_with_a_digit(jv):
    jv.answers.append("2")
    assert jv.j.choose("Personal or work?", ["personal", "work"]) == "work"
    jv.answers.append("the 1st")
    assert jv.j.choose("Personal or work?", ["personal", "work"]) == "personal"
