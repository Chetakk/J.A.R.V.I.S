"""What speech recognition writes instead of a name (settings `heard_as`): "EV, show chat" heard as "Asset show chat".

Taken off the start of what you say, as a name is; never anywhere else, and never a wake word.
"""
from core.registry import clean

NAMES = ["jarvis", "ev", "e v", "evie", "eevee"]
HEARD = ["asset", "zarevis"]


def test_a_misheard_name_at_the_start_is_taken_off():
    assert clean("Asset show chat.", NAMES, HEARD) == "show chat"
    assert clean("Asset Run Study Buddy.", NAMES, HEARD) == "Run Study Buddy"
    assert clean("zarevis, open youtube", NAMES, HEARD) == "open youtube"


def test_said_alone_it_is_just_the_name():
    assert clean("Asset.", NAMES, HEARD) == ""
    assert clean("Zarevis.", NAMES, HEARD) == ""


def test_anywhere_else_it_is_an_ordinary_word():
    assert clean("what is an asset", NAMES, HEARD) == "what is an asset"
    assert clean("assets are low", NAMES, HEARD) == "assets are low"
    assert clean("show me the asset list", NAMES, HEARD) == "show me the asset list"


def test_without_the_setting_nothing_changes():
    assert clean("Asset show chat.", NAMES) == "Asset show chat"


def test_jarvis_takes_it_off_what_you_say(jv):
    jv.j.settings["heard_as"] = HEARD
    jv.do("asset show chat")
    assert not jv.claude.asked() and not jv.questions  # "show chat" itself: no Claude, no "did you mean"
