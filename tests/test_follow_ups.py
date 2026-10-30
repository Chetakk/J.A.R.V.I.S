"""One question back after a Wikipedia answer (skills/lookup.py follow_up), and what your answer gets. Wikipedia is
faked here (nothing reaches the internet)."""
import pytest

from skills import lookup

MALARIA = {"title": "Malaria", "url": "https://en.wikipedia.org/wiki/Malaria",
           "text": "Malaria is a mosquito-borne infectious disease which affects vertebrates. Symptoms include fever, "
                   "tiredness and vomiting. It is spread by mosquitoes."}
GOA = {"title": "Goa", "url": "https://en.wikipedia.org/wiki/Goa",
       "text": "Goa is a state on the southwestern coast of India. It is known for its beaches. Panaji is its capital."}
RECURSION = {"title": "Recursion", "url": "https://en.wikipedia.org/wiki/Recursion",
             "text": "Recursion occurs when the definition of a concept depends on a simpler version of itself. It is "
                     "used in mathematics and computer science. The most common use is in programming."}


@pytest.fixture
def wiki(monkeypatch):
    def fake(query, timeout=4):
        return wiki.page
    monkeypatch.setattr(lookup, "wikipedia", fake)
    return wiki


def test_after_an_illness_it_asks_how_you_are(jv, wiki):
    wiki.page = MALARIA
    jv.do("what is malaria")
    assert jv.says("feeling") and not jv.claude.calls


def test_you_are_not_feeling_well(jv, wiki):
    wiki.page = MALARIA
    jv.do("what is malaria")
    jv.clear()
    jv.do("not really")
    assert jv.says("sorry to hear", "doctor") and not jv.claude.calls


def test_a_longer_answer_about_how_you_feel_goes_to_claude_with_the_question(jv, wiki):
    wiki.page = MALARIA
    jv.do("what is malaria")
    jv.clear()
    jv.claude.reply("A fever for three days needs a doctor's look, sir.")
    jv.do("i've had a fever since monday")
    assert jv.claude.asked(r"JARVIS said: .*feeling.*\n.*fever since monday")  # Claude sees its question


def test_after_a_place_it_asks_about_a_trip(jv, wiki):
    wiki.page = GOA
    jv.do("what is goa")
    assert jv.says("there")
    jv.clear()
    jv.do("yes")
    assert jv.says("plan") or jv.says("ask me")


def test_now_and_then_it_offers_more_and_a_yes_reads_on(jv, wiki, monkeypatch):
    monkeypatch.setattr(lookup, "MORE_CHANCE", 1.0)
    wiki.page = RECURSION
    jv.do("what is recursion")
    assert jv.says("hear a bit more")
    jv.clear()
    jv.do("yes")
    assert jv.says("most common use is in programming")


def test_no_offer_when_the_chance_says_no(jv, wiki, monkeypatch):
    monkeypatch.setattr(lookup, "MORE_CHANCE", 0.0)
    wiki.page = RECURSION
    jv.do("what is recursion")
    assert not jv.says("hear a bit more")
