"""The skills registry (skills/registry.py, fields in skills/base.py): every skill says what it does, how you ask,
what you get, what it may do without you and how it checks itself; and every phrase it tells Claude about
really reaches that skill (routed the way tools/dryrun.py does: nothing runs)."""
import os
import re

import pytest

import conftest
from skills import registry

# Owned by another session and not described yet (see tests for them, too). Remove once they are.
NOT_YET = {"daytracker", "studybuddy"}
# Sample values for the <placeholders> in phrases.
SAMPLES = {"x": "stretch", "y": "rust", "n": "2", "app": "chrome", "file": "plan.md", "project": "avtec hs",
           "topic": "camera calibration", "text": "hello there", "name": "jira", "url": "example.com/file.zip",
           "details": "my name", "task": "find the pricing", "device": "usb", "package": "requests",
           "button": "save", "item": "zoom in", "menu": "view", "folder": "documents", "command": "uptime",
           "folder, file or project": "~/Downloads"}
# Phrases that only mean the skill with something already going on (a text being read, dictation on):
# routed elsewhere otherwise, by design.
NEEDS_CONTEXT = {"read the rest", "read all of it", "summarise it", "stop dictation",
                 "open the first folder", "open the second one"}  # only after JARVIS has listed a folder
ENTRIES = registry.entries()


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["name"] for e in ENTRIES])
def test_every_skill_describes_itself(entry):
    if entry["name"] in NOT_YET:
        pytest.skip("described by its owner later")
    assert not registry.problems(entry), registry.problems(entry)


def test_the_registry_has_every_skill_in_routing_order():
    from skills import ORDER
    assert [e["name"] for e in ENTRIES] == [n for n, _ in ORDER]


def test_claudes_list_comes_from_the_registry(jv):
    text = jv.j._abilities()
    for e in ENTRIES:
        for p in e["phrases"]:
            assert '"%s"' % p.partition(" (")[0] in text, p
    assert "small talk" in text and "saved commands" in text  # what isn't a skill is still there


def concrete(phrase):
    words = phrase.partition(" (")[0]
    return re.sub(r"<([^>]+)>", lambda m: SAMPLES[m.group(1)], words)


@pytest.fixture
def router(jv):
    """tools/dryrun.py's routing (every way out recorded, not done), noting which skill took each phrase."""
    os.makedirs(os.path.join(conftest.HOME, "Projects", "avtec-hs"))
    os.makedirs(os.path.join(conftest.HOME, "Documents"), exist_ok=True)  # SAMPLES' stand-in <folder>
    with open(os.path.join(conftest.HOME, "Projects", "avtec-hs", "README.md"), "w") as f:
        f.write("# Avtec HS\n\nA camera station.\n")
    jv.desktop.add("Inbox - Gmail - Google Chrome", "google-chrome Google-chrome")
    import sys
    sys.path.insert(0, os.path.join(conftest.HERE, "tools"))
    import dryrun
    dry = dryrun.DryJarvis()
    took = []
    for (name, _), skill in zip(registry.ORDER, dry.j.skills.all):
        def handle(text, _orig=skill.handle, _name=name):
            done = _orig(text)
            if done:
                took.append(_name)
            return done
        skill.handle = handle

    def route(text):
        del took[:]
        dry.route(text)
        return took[0] if took else None
    return route


def phrases_of(entry):
    return [p for p in entry["phrases"] if p.partition(" (")[0] not in NEEDS_CONTEXT]


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e["phrases"]], ids=[e["name"] for e in ENTRIES if e["phrases"]])
def test_each_phrase_reaches_its_skill(router, entry):
    wrong = {}
    for p in phrases_of(entry):
        got = router(concrete(p))
        if got != entry["name"]:
            wrong[concrete(p)] = got
    assert not wrong, "phrases that don't reach %s: %s" % (entry["name"], wrong)
