#!/usr/bin/env python3
"""The skills registry: every skill, what it does, how you ask, what you get, what it may do without you, and how
it checks itself (each skill says so in its class: skills/base.py).

  tools/skills.py               a table of all of them, in routing order
  tools/skills.py notes web     just those, in full
  tools/skills.py --check       which skills haven't described themselves fully
  tools/skills.py --claude      the abilities list Claude is given for its "do" field (skills' part)
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from skills import registry  # noqa: E402


def main(argv):
    entries = registry.entries()
    if argv[:1] == ["--check"]:
        bad = [(e["name"], registry.problems(e)) for e in entries if registry.problems(e)]
        for name, found in bad:
            print("%-18s %s" % (name, "; ".join(found)))
        print("%d of %d skills fully described" % (len(entries) - len(bad), len(entries)))
        return 1 if bad else 0
    if argv[:1] == ["--claude"]:
        print("\n".join(registry.abilities()))
        return 0
    if argv:
        for e in entries:
            if e["name"] in argv:
                print("%s (%s)\n  %s" % (e["name"], e["group"], e["does"]))
                print("  say:        %s" % ("; ".join(e["phrases"]) or "(not for Claude's list)"))
                print("  you get:    %s\n  permission: %s%s" % (e["gives"], e["permission"],
                                                              " — asks " + e["asks"] if e["asks"] else ""))
                print("  checks:     %s\n" % e["checks"])
        return 0
    for e in entries:
        print("%-18s %-8s %s" % (e["name"], e["permission"], e["does"] or "(not described yet)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
