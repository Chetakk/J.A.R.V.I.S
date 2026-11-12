"""Claude's answer is spoken a sentence at a time as it streams in, and the chat still gets it as one line."""
import json

from claude import bridge
from claude.bridge import Claude, _say_so_far, _whole_sentences


def test_the_say_so_far_and_whether_it_is_finished():
    assert _say_so_far('{"details": "x", "sa') is None
    assert _say_so_far('{"say": "Sure. Here') == ("Sure. Here", False)
    assert _say_so_far('{"say": "Sure.", "det') == ("Sure.", True)
    assert _say_so_far('{"say": "Caf\\u00') == ("Caf", False)  # half an escape: left for the next bit
    assert _say_so_far('{"suggest": {"say": "Want a shortcut?"}') is None  # not the answer's own line
    assert _say_so_far('{"do": ["what time is it"], "say": "It is') is None  # the phrase says its own result


def test_only_sentences_that_more_text_follows_are_whole():
    assert _whole_sentences("Sure. Here are") == "Sure. "
    assert _whole_sentences("It's 3.5 degrees") == ""  # a decimal point isn't a sentence end
    assert _whole_sentences('He said "go." Then') == 'He said "go." '


class _Proc:
    """A Claude process whose stream-json output is `chunks` of answer text."""
    def __init__(self, chunks, answer):
        events = [{"type": "stream_event", "event": {"type": "content_block_start"}}]
        events += [{"type": "stream_event", "event": {"type": "content_block_delta",
                                                      "delta": {"type": "text_delta", "text": c}}} for c in chunks]
        events.append({"type": "result", "result": answer, "session_id": "s1"})
        self.stdout = [json.dumps(e) + "\n" for e in events]
        self.returncode = 0

    def wait(self):
        return 0

    def kill(self):
        pass


def _ask(chunks, monkeypatch, tmp_path):
    answer = "".join(chunks)
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda *a, **k: _Proc(chunks, answer))
    monkeypatch.setattr(bridge, "STATE_DIR", str(tmp_path))
    c = Claude.__new__(Claude)
    c.bin, c.model, c.effort, c.prompt, c.timeout = "claude", "m", "low", "p", 30
    c.session_file, c.usage_file = str(tmp_path / "session"), str(tmp_path / "usage.json")
    c.running, c.stopped, c.idle = set(), set(), 1200
    said = []
    result = c._ask("hi", on_say=lambda piece, last: said.append((piece, last)))
    return said, result


def test_each_sentence_is_said_as_soon_as_it_has_streamed_in(monkeypatch, tmp_path):
    said, answer = _ask(['{"say": "Sure', '. Here are your', ' three to-dos.', ' Want them', ' on a card?"',
                         ', "details": "1, 2, 3"}'], monkeypatch, tmp_path)
    assert said == [("Sure.", False), ("Here are your three to-dos.", False), ("Want them on a card?", True)]
    assert answer["spoken"] and answer["say"] == "Sure. Here are your three to-dos. Want them on a card?"


def test_a_one_sentence_answer_is_said_once(monkeypatch, tmp_path):
    said, answer = _ask(['{"say": "It is', ' 5 pm."}'], monkeypatch, tmp_path)
    assert said == [("It is 5 pm.", True)] and answer["spoken"]


def test_an_answer_that_opens_with_a_phrase_is_not_said_early(monkeypatch, tmp_path):
    said, answer = _ask(['{"do": ["what time is it"], "say": "It is', ' 5 pm."}'], monkeypatch, tmp_path)
    assert said == [] and not answer["spoken"]


def test_the_chat_gets_the_whole_answer_as_one_line(jv):
    j = jv.j
    for piece, last in (("Sure.", False), ("Here are your three to-dos.", False), ("Want them on a card?", True)):
        j.claude_says(piece, last)
    lines = [l for l in j.recent if l.startswith("JARVIS said: ")]
    assert lines[-1] == "JARVIS said: Sure. Here are your three to-dos. Want them on a card?"
    assert not any(l.endswith("Sure.") for l in lines)
