"""Quick lookups (skills/lookup.py): Wikipedia answers spoken without Claude; anything current goes to one web
search through Claude. Wikipedia is faked here (nothing reaches the internet)."""
import json

import pytest

from skills import lookup

TURING = {"title": "Alan Turing", "url": "https://en.wikipedia.org/wiki/Alan_Turing",
          "text": "Alan Mathison Turing (/ˈtjʊərɪŋ/; 23 June 1912 – 7 June 1954) was an English mathematician. "
                  "He was highly influential in computer science. He was born in London."}


@pytest.fixture
def wiki(monkeypatch):
    asked = []

    def fake(query, timeout=4):
        asked.append(query)
        if isinstance(wiki.page, Exception):
            raise wiki.page
        return wiki.page
    wiki.page, wiki.asked = TURING, asked
    monkeypatch.setattr(lookup, "wikipedia", fake)
    return wiki


def test_wikipedia_is_spoken_without_brackets(jv, wiki):
    jv.do("wikipedia alan turing")
    assert wiki.asked == ["alan turing"] and not jv.claude.calls
    assert jv.says("Alan Mathison Turing was an English mathematician. He was highly influential")
    assert not jv.says("born in London")  # two sentences said, the rest on the card
    assert jv.cards[-1]["title"] == "Alan Turing" and "wiki/Alan_Turing" in jv.cards[-1]["text"]


def test_who_is_goes_to_wikipedia_before_claude(jv, wiki):
    jv.do("who is alan turing")
    assert wiki.asked == ["alan turing"] and not jv.claude.calls and jv.says("English mathematician")


def test_an_article_about_something_else_goes_to_claude(jv, wiki):
    wiki.page = dict(TURING, title="Enigma machine")
    jv.do("what is a turing award")
    assert jv.claude.calls and jv.cards == []


def test_wikipedia_down_goes_to_claude_quietly(jv, wiki):
    wiki.page = OSError("no network")
    jv.do("who is alan turing")
    assert jv.claude.calls and not jv.says("couldn't reach")


def test_asked_for_wikipedia_says_when_it_cant(jv, wiki):
    wiki.page = None
    jv.do("wikipedia zzqx")
    assert jv.says("Wikipedia doesn't have a page on zzqx") and not jv.claude.calls


@pytest.mark.parametrize("said", ["who is the president of france", "what is the price of bitcoin"])
def test_something_current_is_searched_on_the_web(jv, wiki, said):
    jv.claude.reply(json.dumps({"say": "It's the answer.", "card": "Sources: [x](https://x.example)"}))
    jv.do(said)
    assert not wiki.asked and jv.says("It's the answer.") and "Search the web" in jv.claude.asked("")[-1]


def test_search_the_web_and_tell_me(jv, wiki):
    jv.claude.reply(json.dumps({"say": "Orin NX is out.", "card": "x"}))
    jv.do("search the web for jetson orin nx and tell me")
    assert jv.claude.asked("answer: jetson orin nx$") and jv.says("Orin NX is out.") and not wiki.asked


@pytest.mark.parametrize("said", ["what is my ip", "who are you"])
def test_not_a_topic_is_left_alone(jv, wiki, said):
    jv.do(said)
    assert not wiki.asked


def test_spoken_keeps_sentences_whole():
    assert lookup.spoken("CUDA (Compute Unified Device Architecture) is a platform. It is by Nvidia. More.") == \
        "CUDA is a platform. It is by Nvidia."
