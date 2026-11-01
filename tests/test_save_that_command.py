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
