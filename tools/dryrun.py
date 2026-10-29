#!/usr/bin/env python3
"""Shows what JARVIS would do with a phrase, without doing it.

  tools/dryrun.py "turn the sound down" "what's on my screen"
  tools/dryrun.py --check            # every phrase in tests/phrases.yaml against its expected result

The real routing in jarvis.py runs; every way out of it (shell commands, skills, card actions, Claude,
speech, memory writes) is swapped for a recorder, so nothing on the desktop changes.
Results look like: command:volume_down, skill:media, app:open Rhythmbox, card:close, persona, browser, claude, ignored.
"""
import os
import queue
import re as J_re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import yaml  # noqa: E402

import jarvis as J  # noqa: E402
from skills import controls as J_controls  # noqa: E402
from skills import copy_between as J_copy  # noqa: E402
from skills import documents as J_docs  # noqa: E402
from skills import frozen as J_frozen  # noqa: E402
from skills import maths as J_maths  # noqa: E402
from skills import organise as J_organise  # noqa: E402
from skills import troubleshoot as J_troubleshoot  # noqa: E402

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
        j._answer = lambda question: rec(("asks", question))  # a yes/no or "X, or Y?": no answer comes
        j._slash = lambda raw: rec(("slash", raw)) or True
        j._miss = lambda text, outcome: rec(("miss", outcome))
        j._open_app = lambda text: self._app(J.apps.OPEN, text, "open")
        j._close_app = lambda text: self._app(J.apps.CLOSE, text, "close")
        for name in ("seen", "used", "add_reminder", "clear_reminders", "set_pref"):
            setattr(j.memory, name, lambda *a, **k: 0)
        j.claude.open_terminal = lambda *a, **k: rec(("claude-terminal", "")) or False
        J.open_in_vscode = lambda *a, **k: rec(("vscode", ""))
        for skill in j.skills.all:
            self._stub_rules("card" if skill is j.cards else "skill", skill)
        j.skills.first = lambda text: False  # not dictating; no "where was I" history
        j.skills.focusing = lambda: False

    def _app(self, pattern, text, verb):
        app, _ = self.j.app_for(pattern, text)
        if app:
            self.log.append(("app", "%s %s" % (verb, app["name"])))
        return bool(app)

    def _stub_rules(self, kind, obj):
        """Rules point at bound methods (record their name) or lambdas calling methods (stubbed below)."""
        rec = self.log.append
        for name in dir(obj):
            fn = getattr(obj, name)
            if callable(fn) and not name.startswith("_") and name != "handle":
                setattr(obj, name, lambda *a, _n=name, **k: rec((kind, _n)) or True)
        if obj is self.j.skills.reading:  # nothing is being read in a dry run: "continue" is the music's
            obj.rest = obj.summarise = lambda *a, **k: False
        # Skills that hand non-matches on at run time: the same read-only lookup decides here too.
        projects = getattr(obj, "projects", None)
        find = projects and (lambda m: projects.find(_first(m)) or len(projects.candidates(_first(m))) > 1)  # as _find
        passes = {"run_project": find, "stop_project": find} if find else {}
        if find and obj is self.j.skills.troubleshoot:  # "why isn't my wifi working": not a project
            passes["why"] = lambda m: J_re.fullmatch(J_troubleshoot.GENERIC, _first(m), J_re.I) or find(m)
        if obj is self.j.skills.controls:  # "click it", "hit me with a joke": handed on
            passes["click"] = lambda m: m.group(2) or J_controls._spoken_keys(m.group(1)) or J_controls._name(m.group(1))
            passes["hit"] = lambda m: J_controls._spoken_keys(m.group(1))
        if obj is self.j.skills.frozen:  # "the build is stuck": not a window, handed on
            passes["about"] = lambda m: J_frozen.target(m.group(1)) is not None
            passes["force"] = lambda m: J_frozen.target(m.group(1) or m.group(2)) not in (None, J_frozen.ALL)
        if obj is self.j.skills.copy_between:  # "send this to my phone": no such window or app, handed on
            passes["move"] = passes["transform"] = lambda m: any(J_copy.target(self.j, m.group("to")))
        if obj is self.j.skills.documents:  # "summarise example.com", "compare jetson and pi": not files
            passes["summarise"] = lambda m: J_docs.could_be(_first(m))
            passes["question"] = lambda m: J_docs.could_be(m.group(1), strict=True)
            passes["question_after"] = lambda m: J_docs.could_be(m.group(2), strict=True)
            passes["compare"] = lambda m: J_docs.could_be(m.group(1), True) and J_docs.could_be(m.group(2), True)
            passes["with_claude"] = lambda m: J_docs.could_be(_first(m), strict=True)
        if obj is self.j.skills.organise:  # "delete the milk todo", "put this on the left": not files
            passes["rename"] = passes["move"] = lambda m: J_docs.could_be(m.group(1))
            passes["trash"] = lambda m: J_docs.could_be(_first(m)) and not (
                J_organise.OTHER_THINGS.search(_first(m)) and not J_docs.sure(_first(m)))
        if obj is self.j.skills.windows:
            passes["focus"] = lambda m: J.desktop.find_window(m.group(1).strip())  # read-only window list
        units = lambda a, src, dst: _convertible(a) and J_maths.convert(1, src.strip(), dst.strip()) is not None  # noqa: E731
        passes.update({"maths": lambda m: J_maths.calculate(m.group(0)) is not None,
                       "units": lambda m: units(*m.groups()),
                       "units_how_many": lambda m: units(m.group(2), m.group(3), m.group(1))})

        def stub(name):
            def fn(m):
                if name in passes and not passes[name](m):
                    return False
                rec((kind, name))
                return True
            return fn
        # Lambdas call methods on their own objects (e.g. Todos, DayTracker): stub those too.
        for _, fn in obj.rules:
            for cell in (fn.__closure__ or ()) if fn.__name__ == "<lambda>" else ():
                inner = cell.cell_contents
                if inner is not obj and hasattr(inner, "__dict__") and not callable(inner):
                    for name in dir(inner):
                        if not name.startswith("_") and callable(getattr(inner, name, None)):
                            setattr(inner, name, lambda *a, _n=name, **k: rec((kind, _n)) or True)
        obj.rules = [(p, fn if fn.__name__ == "<lambda>" or ".stub." in fn.__qualname__ else stub(fn.__name__))
                     for p, fn in obj.rules]  # (shared rule lists are stubbed once)

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
        if kind in ("command", "skill", "card", "extra", "slash", "app"):
            result = "%s:%s" % (kind, value)
        elif kind == "run":
            result = "browser"
        else:
            result = kind
        return result, "; ".join(notes + ([str(value)] if kind == "run" else []))


def _first(m):
    return next((g for g in m.groups() if g), "")


def _convertible(amount):
    try:
        J_maths._number(amount)
        return True
    except ValueError:
        return False


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
