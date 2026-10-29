#!/usr/bin/env python3
"""Shows what JARVIS would do with a phrase, without doing it.

  tools/dryrun.py "turn the sound down" "what's on my screen"
  tools/dryrun.py --check            # every phrase in tests/phrases.yaml against its expected result

The real routing in jarvis.py runs; every way out of it (shell commands, skills, card actions, Claude,
speech, memory writes) is swapped for a recorder, so nothing on the desktop changes.
Results look like: command:volume_down, skill:media, card:close, persona, browser, claude, ignored.
"""
import os
import queue
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import yaml  # noqa: E402

import jarvis as J  # noqa: E402

PHRASES = os.path.join(HERE, "tests", "phrases.yaml")


class DryJarvis:
    def __init__(self):
        j = self.j = J.Jarvis(queue.Queue())
        self.log = []
        rec = self.log.append
        j.ui = None
        j.speaker.say = lambda *a, **k: rec(("say", a[0] if a else ""))
        j.heard = lambda text, outcome: rec(("note", outcome))
        j.executor.run = lambda command, **k: rec(("run", command))
        j._run_match = lambda match: rec(("command", match.command.get("name"))) or True
        j._ask_claude = lambda text, **k: rec(("claude", text))
        j._slash = lambda raw: rec(("slash", raw)) or True
        j._miss = lambda text, outcome: rec(("miss", outcome))
        for name in ("seen", "used", "add_reminder", "clear_reminders"):
            setattr(j.memory, name, lambda *a, **k: 0)
        self._stub_rules("skill", j.skills)
        self._stub_rules("card", j.cards)

    def _stub_rules(self, kind, obj):
        """Rules point at bound methods (record their name) or lambdas calling methods (stubbed below)."""
        rec = self.log.append
        for name in dir(obj):
            fn = getattr(obj, name)
            if callable(fn) and not name.startswith("_") and name != "handle":
                setattr(obj, name, lambda *a, _n=name, **k: rec((kind, _n)) or True)
        obj.rules = [(p, (lambda m, _n=fn.__name__: rec((kind, _n)) or True) if fn.__name__ != "<lambda>" else fn)
                     for p, fn in obj.rules]

    def route(self, text):
        """(result, details) for one phrase."""
        del self.log[:]
        self.j.last_request = None
        self.j.handle(text)
        acts = [(k, v) for k, v in self.log if k not in ("say", "note", "miss")]
        notes = [v for k, v in self.log if k == "note"]
        if not acts:
            said = [v for k, v in self.log if k == "say"]
            if any("ignored" in n for n in notes) or not said:
                return "ignored", "; ".join(notes)
            return "persona", str(said[0])
        kind, value = acts[0]
        if kind in ("command", "skill", "card", "slash"):
            result = "%s:%s" % (kind, value)
        elif kind == "run":
            result = "browser"
        else:
            result = kind
        return result, "; ".join(notes + ([str(value)] if kind == "run" else []))


def main(argv):
    dry = DryJarvis()
    if argv[:1] == ["--check"]:
        with open(PHRASES, encoding="utf-8") as f:
            cases = yaml.safe_load(f) or {}
        bad = 0
        for phrase, want in cases.items():
            got, why = dry.route(phrase)
            if got != want:
                bad += 1
                print("FAIL  %-40s want %-24s got %s  %s" % (phrase, want, got, why))
        print("%d/%d passed" % (len(cases) - bad, len(cases)))
        return 1 if bad else 0
    for phrase in argv:
        got, why = dry.route(phrase)
        print("%-40s %-26s %s" % (phrase, got, why))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
