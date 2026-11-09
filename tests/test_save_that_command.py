""""Save that command" right after a confirmed guess says it's already kept, and never saves the card on screen
(it once saved the "Did you mean?" card, because the nearest phrase was "save that")."""
import json
import time

from core import learned


def test_save_that_command_after_a_yes(jv):
    with open(learned.TAUGHT, "a") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M"), "said": "nothing. just leave it",
                            "means": "stop", "from": "you said yes"}) + "\n")
    for said in ("save that command", "save the last command", "remember that for next time"):
        jv.clear()
        jv.do(said)
        assert jv.said == ["Already saved. Next time, nothing. just leave it means stop."], said


def test_save_that_command_with_nothing_new(jv):
    jv.do("save the last command")
    assert jv.says("nothing new to save")


def test_remember_that_after_claude_did_it(jv):
    """"pull up big boss" -> Claude asks which -> "a series on Hotstar" -> Claude searches Hotstar: "remember that"
    keeps what you asked first for Claude's phrase, so it works without Claude next time."""
    jv.j.settings["learn"] = "quiet"
    jv.claude.reply({"say": "Big boss? Do you mean a site, an app, or a file?"})
    jv.claude.reply({"say": "Searching for it now.", "do": ["open youtube"]})
    jv.do("pull up big boss")
    jv.do("tell claude it's a series that i usually watch every evening on hotstar")
    jv.clear()
    jv.do("remember that")
    assert jv.said == ["Saved. Next time, pull up big boss means open youtube."]
    assert learned.load()["wordings"]["pull up big boss"] == "open youtube"
    jv.clear()
    jv.do("pull up big boss")
    assert not jv.claude.calls
