"""A meaning-match guess that opens something is asked about first: "show me recent shared files" once opened the
newest shared file ("open the latest shared file", 0.86) when you wanted the list."""
SAID = "show me a few of those shared bits"  # (no skill pattern takes it: only the stubbed meaning match does)


def guessing(jv, monkeypatch, phrase, score=0.86):
    done = []
    monkeypatch.setattr(jv.j.intents, "match", lambda text: (phrase, score) if text == SAID else (None, 0.0))
    monkeypatch.setattr(jv.j, "_reword", lambda p: done.append(p) or True)
    return done


def test_yes_opens_it(jv, monkeypatch):
    done = guessing(jv, monkeypatch, "open the latest shared file")
    jv.do(SAID, "yes")
    assert jv.questions and "open the latest shared file?" in jv.questions[0].lower()
    assert done == ["open the latest shared file"]


def test_no_goes_to_claude_instead(jv, monkeypatch):
    done = guessing(jv, monkeypatch, "open the latest shared file")
    jv.claude.reply("Here's what was shared.")
    jv.do(SAID, "no")
    assert not done and jv.claude.calls


def test_a_guess_that_opens_nothing_is_not_asked(jv, monkeypatch):
    done = guessing(jv, monkeypatch, "what's been shared with me")
    jv.do(SAID)
    assert not jv.questions and done == ["what's been shared with me"]
