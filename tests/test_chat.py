"""Chat replies (core/chat.py, config/chat.yaml): JARVIS answers chat itself; requests and real questions don't
become chat."""
import os
import re

import yaml

CHAT = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        "config", "chat.yaml")))


def _pattern(reply):
    return re.compile(re.sub(r"\\\{\w+\\\}", ".+", re.escape(reply)).replace(",\\ .+", "(,\\ .+)?") + "$")


def _from(topic, jv):
    return [line for line in jv.said if any(_pattern(r).match(line) for r in CHAT[topic]["replies"])]


def test_a_chat_question_in_new_words_is_answered_without_claude(jv):
    jv.do("do you own a dog")
    assert _from("pets", jv) and not jv.claude.calls


def test_how_the_user_feels_gets_a_kind_reply(jv):
    jv.do("i've been feeling really alone today")
    assert _from("user_lonely", jv) and not jv.claude.calls


def test_who_built_jarvis(jv):
    jv.do("who is the person who coded you")
    assert _from("my_creator", jv) and not jv.claude.calls


def test_replies_call_you_sir_or_ckay_and_know_you_are_chetak(jv):
    jv.j.settings.update(call_me=["sir", "Ckay"], real_name="Chetak", user_name="sir")
    lines = []
    for _ in range(12):
        jv.clear()
        jv.do("who is the person who coded you")
        lines += jv.said
    assert all("{" not in line for line in lines)
    assert any("Chetak built me" in line for line in lines)
    assert {"sir", "Ckay"} <= {w.strip(".,!?") for line in lines for w in line.split()}


def test_a_real_question_still_goes_to_claude(jv):
    jv.claude.reply("Black holes are where gravity is so strong that light can't escape.")
    jv.do("explain black holes in simple terms")
    assert jv.claude.asked("black holes") and not any(_from(t, jv) for t in CHAT)


def test_asking_for_advice_about_food_is_not_chat(jv):
    """"What's your favourite food" is chat; "what should I eat for dinner" wants a real answer."""
    jv.claude.reply("How about a quick stir fry?")
    jv.do("what should i eat for dinner")
    assert jv.claude.asked("dinner") and not _from("food", jv)


def test_a_command_is_never_taken_as_chat(jv):
    jv.do("play some music")
    assert not any(_from(t, jv) for t in CHAT)


def test_every_topic_has_replies_and_wordings():
    for name, topic in CHAT.items():
        assert topic.get("replies") and topic.get("says"), name
        assert all(len(r) <= 140 for r in topic["replies"]), name  # spoken: short


def _asked(jv, topic, ago=0):
    """As if JARVIS had just asked you something in that topic ("Do you have any pets?")."""
    import time
    jv.j.chat_replies.asked = (topic, time.time() - ago)


def _then(topic, kind, jv):
    return [line for line in jv.said if any(_pattern(r).match(line) for r in CHAT[topic]["then"][kind])]


def test_a_reply_that_asks_something_remembers_it():
    from core import chat
    c = chat.ChatReplies.__new__(chat.ChatReplies)
    c.replies, c.then, c.last = {"pets": ["No pets. Do you have any, {name}?"]}, {}, None
    c.match = lambda text: ("pets", 0.9)
    c.reply("do you have pets", "sir")
    assert c.take() == "pets" and c.take() is None  # asked once


def test_your_answer_to_its_question_gets_a_matching_reply(jv):
    _asked(jv, "pets")
    jv.do("yes a golden retriever called max")
    assert _then("pets", "yes", jv) and not jv.claude.calls


def test_a_short_no_to_its_question_isnt_the_usual_small_talk(jv):
    _asked(jv, "pets")
    jv.do("nope")
    assert _then("pets", "no", jv)


def test_how_your_week_was(jv):
    _asked(jv, "how_week")
    jv.do("pretty rough actually")
    assert _then("how_week", "bad", jv)
    jv.clear()
    _asked(jv, "how_week")
    jv.do("not bad")
    assert _then("how_week", "good", jv)


def test_an_answer_that_is_neither_yes_nor_no(jv):
    _asked(jv, "food")
    jv.do("a big bowl of noodles")
    assert _then("food", "other", jv) and not jv.claude.calls


def test_a_no_never_gets_the_good_choice_line(jv):
    _asked(jv, "travel_dream")
    jv.do("no idea really")
    assert not _then("travel_dream", "other", jv)


def test_a_request_after_its_question_is_still_a_request(jv):
    _asked(jv, "pets")
    jv.do("open youtube")
    assert jv.shell.ran("youtube") and not any(_then("pets", k, jv) for k in CHAT["pets"]["then"])


def test_a_question_of_your_own_is_answered_as_usual(jv):
    _asked(jv, "pets")
    jv.do("do you have any hobbies")
    assert _from("free_time", jv)


def test_its_question_is_forgotten_after_a_minute(jv):
    _asked(jv, "pets", ago=61)
    jv.do("yes a golden retriever called max")
    assert not _then("pets", "yes", jv)
