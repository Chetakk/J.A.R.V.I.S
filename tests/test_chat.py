"""Chat replies (core/chat.py, config/chat.yaml): JARVIS answers chat itself; requests and real questions don't
become chat."""
import os

import yaml

CHAT = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        "config", "chat.yaml")))


def _from(topic, jv):
    return [line for line in jv.said if any(line.startswith(r.split("{")[0]) for r in CHAT[topic]["replies"])]


def test_a_chat_question_in_new_words_is_answered_without_claude(jv):
    jv.do("do you own a dog")
    assert _from("pets", jv) and not jv.claude.calls


def test_how_the_user_feels_gets_a_kind_reply(jv):
    jv.do("i've been feeling really alone today")
    assert _from("user_lonely", jv) and not jv.claude.calls


def test_who_built_jarvis(jv):
    jv.do("who is the person who coded you")
    assert _from("my_creator", jv) and not jv.claude.calls


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
