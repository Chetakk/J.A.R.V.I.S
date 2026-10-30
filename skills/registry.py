"""The skills registry: what each skill says about itself (see skills/base.py), in routing order.

Claude's list of what JARVIS can do (its "do" field) is built from it, tools/skills.py prints it, and
tests/test_registry.py checks every skill fills it in and that each phrase it lists really reaches it."""
from skills import ORDER
from skills.base import PERMISSIONS

FIELDS = ("does", "phrases", "gives", "permission", "asks", "checks")
REQUIRED = ("does", "gives", "permission", "checks")  # phrases may be empty: a skill Claude shouldn't "do"


def entries():
    """[{"name", "group", "does", "phrases", "gives", "permission", "asks", "checks"}], in routing order."""
    out = []
    for name, cls in ORDER:
        entry = {"name": name, "group": cls.group or name.replace("_", " ")}
        entry.update({f: getattr(cls, f) for f in FIELDS})
        phrases = entry["phrases"]
        entry["phrases"] = (phrases,) if isinstance(phrases, str) else tuple(phrases)  # ("x") is just a string
        out.append(entry)
    return out


def problems(entry):
    """What's missing or wrong in one entry (empty when it's complete)."""
    shadowed = [f for f in FIELDS if callable(entry[f])]
    if shadowed:  # a handler method with the same name hides the text
        return ["%s is a method, not its description" % f for f in shadowed]
    found = ["no %s" % f for f in REQUIRED if not entry[f]]
    if entry["permission"] not in PERMISSIONS:
        found.append("permission %r isn't one of %s" % (entry["permission"], "/".join(PERMISSIONS)))
    elif entry["permission"] == "confirm" and not entry["asks"]:
        found.append("permission confirm but no asks (when does it ask?)")
    return found


def abilities():
    """One line per group for Claude's system prompt: 'memory: "remember that <x>", ...'. Skills that share a
    group share a line, in routing order."""
    groups = {}
    for e in entries():
        if e["phrases"]:
            groups.setdefault(e["group"], []).extend(e["phrases"])
    return ["%s: %s" % (group, ", ".join(_quoted(p) for p in phrases)) for group, phrases in groups.items()]


def _quoted(phrase):
    """'delete <file> (to the Trash)' -> '"delete <file>" (to the Trash)': the words in quotes, a note after."""
    words, _, note = phrase.partition(" (")
    return '"%s"%s' % (words, " (" + note if note else "")
