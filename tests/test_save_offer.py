"""Saving Claude's suggested shortcut (jarvis.py _save): only on a yes, and a yes that comes late still counts."""
import pytest

SAVE = {"name": "random_fact", "phrases": ["random fact", "tell me a random fact"], "run": "fortune"}


@pytest.fixture
def saved(jv, monkeypatch):
    added = []
    monkeypatch.setattr(jv.j.registry, "add", lambda name, *a, **k: added.append(name))  # not commands.yaml
    return added


def test_yes_saves_it(jv, saved):
    jv.answers.append("yes")
    jv.j._save(SAVE)
    assert saved == ["random_fact"]
    assert jv.says("saved")


def test_no_doesnt(jv, saved):
    jv.answers.append("no")
    jv.j._save(SAVE)
    assert not saved and jv.says("not saved")


def test_a_late_yes_still_saves_it(jv, saved):
    jv.j._save(SAVE)  # no answer in time
    assert not saved and jv.says("not saved yet")
    jv.do("yes")
    assert saved == ["random_fact"]
    assert not jv.claude.calls


def test_a_late_yes_after_something_else_is_just_a_yes(jv, saved, monkeypatch):
    monkeypatch.setattr(jv.j, "_run_match", lambda match: True)
    jv.j._save(SAVE)
    jv.do("what time is it")
    jv.do("yes")
    assert not saved
