"""The meaning matcher's vectors (core/intent.py)."""


def test_a_sentences_vector_does_not_depend_on_what_else_is_in_the_batch(jv):
    """A long sentence joining the list used to shift every other vector: "go dark" dropped to 0.7196 against a
    threshold of 0.72 because a long wording had been learned elsewhere."""
    m = jv.j.intents
    alone = m.embed(["dark mode"])[0]
    beside = m.embed(["dark mode", "i'm going to be on a call for a little while so keep everything quiet for me"])[0]
    assert float(alone @ beside) > 0.9999


def test_a_learned_wording_scores_the_same_after_a_restart(jv):
    """Learned while running it's embedded alone; after a restart, with the whole list. Those must agree."""
    m = jv.j.intents
    assert m.embed(["bring the sound down"]).shape == (1, m.matrix.shape[1])
    assert float(m.embed(["bring the sound down"])[0] @ m.embed(["fan max", "bring the sound down"])[1]) > 0.9999


def test_a_wording_ending_in_now_or_for_me_still_matches_word_for_word(jv):
    """They're taken off what you say first, so "what is running right now" arrived as "what is running right"
    and never found its own wording: it asked "did you mean...?" instead."""
    m = jv.j.intents
    m.learn("what is going on right now", "what's running")
    m.learn("get the sound down for me", "volume down")
    assert m.early("what is going on right") == "what's running"
    assert m.early("get the sound down") == "volume down"
    jv.do("what is going on right now")
    assert not jv.questions and not jv.claude.asked()
