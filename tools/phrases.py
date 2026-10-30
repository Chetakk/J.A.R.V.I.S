#!/usr/bin/env python3
"""Other ways of saying each phrase JARVIS knows, by hand: Claude writes them, this checks them,
config/intents.yaml keeps them.

  tools/phrases.py                  # phrases the meaning matcher has no other wordings for
  tools/phrases.py --ask            # ask Claude for wordings for all of them, keep the ones that route right
  tools/phrases.py --ask notes todos   # only those skills (or groups)
  tools/phrases.py --ask --dry      # print what Claude suggested, write nothing (and check nothing)
  tools/phrases.py --misses 7       # the same, for what the last 7 days of misses nearly matched
  tools/phrases.py --taught         # what JARVIS taught itself while running

JARVIS also does this by itself as it runs (core/teaching.py, settings `teaching`), straight into
~/.local/share/jarvis/learned.yaml. This is the same wordings written into the code instead, with the
fuller check only a dry run can do: each one is routed with tools/dryrun.py before and after it's added,
so a wording that already goes to the right place isn't added, one that still goes elsewhere is dropped,
and one that pulls a phrase in tests/phrases.yaml away from where it went takes its whole entry back out.
What survives is appended to config/intents.yaml and tests/phrases.yaml, so `tools/dryrun.py --check`
guards it from then on.

Phrases with a value in them ("<x>", or "focus for 45 minutes") are left alone: those need a {slot}
template, written by hand.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import yaml  # noqa: E402

from core import teaching  # noqa: E402
from core.intent import _norm  # noqa: E402

INTENTS = os.path.join(HERE, "config", "intents.yaml")
CASES = os.path.join(HERE, "tests", "phrases.yaml")
MODEL = "claude-sonnet-5-5"
WANTED = 8  # wordings asked for per phrase
BATCH = 12  # phrases per question to Claude
# A phrase that routes to one of these isn't a known phrase to hang wordings on. "asks" is one of them:
# a dry run has nothing on screen and nothing being read aloud, so JARVIS asks instead of acting.
NOWHERE = ("claude", "ignored", "persona", "asks")


def known():
    return teaching.known(path=INTENTS)


def nearly_missed(days):
    """[(where it came from, what was said)] from the miss log: wordings JARVIS heard and didn't place."""
    return [("a miss", text) for text in teaching.missed(known(), days)], []


ROUTER = r"""
import json, sys
sys.path.insert(0, %r)
from tools.dryrun import DryJarvis
dry = DryJarvis()
print("@@" + json.dumps({p: dry.route(p)[0] for p in sys.argv[1:]}))
"""


def routes(phrases):
    """{phrase: where JARVIS would send it}, from a fresh tools/dryrun.py in its own process.

    Its own process every time: the routing stubs record into the DryJarvis that made them, so a second
    one in here would report the first one's results."""
    if not phrases:
        return {}
    out = {}
    for i in range(0, len(phrases), 400):  # one argv can't hold every phrase at once
        res = subprocess.run([sys.executable, "-c", ROUTER % HERE] + list(phrases[i:i + 400]),
                             cwd=HERE, capture_output=True, text=True)
        line = next((l for l in res.stdout.splitlines() if l.startswith("@@")), None)
        if not line:
            sys.exit("couldn't route: %s" % (res.stderr.strip().splitlines() or ["no output"])[-1])
        out.update(json.loads(line[2:]))
    return out


def ask(phrases, model, wanted):
    """{phrase: [wordings]} from Claude, for one batch of phrases (core/teaching.py asks the question)."""
    claude = shutil.which("claude")
    if not claude:
        sys.exit("Claude Code isn't installed (needs `claude` on the PATH)")

    def once(prompt, system):
        res = subprocess.run([claude, "-p", prompt, "--model", model, "--tools", "", "--strict-mcp-config",
                              "--no-session-persistence", "--system-prompt", system, "--output-format", "json"],
                             capture_output=True, text=True, timeout=600)
        try:
            reply = json.loads(res.stdout)
        except ValueError:
            sys.exit("couldn't reach Claude: %s" % (res.stderr.strip() or res.stdout.strip())[:200])
        if reply.get("is_error"):
            sys.exit("Claude: %s" % reply.get("result"))
        return reply.get("result", "")

    return teaching.ask_wordings(once, phrases, wanted)


def _yaml(text):
    """A phrase as one YAML flow-list item: quoted when a comma or a leading marker would change what it means."""
    return '"%s"' % text if re.search(r"[,:#]|^[/\"'&*?|<>!%@`\-]", text) else text


def block(additions, why):
    """The lines to append to config/intents.yaml for {phrase: [wordings]}."""
    lines = ["", "# --- added %s by tools/phrases.py (%s), each one checked with tools/dryrun.py" %
             (time.strftime("%Y-%m-%d"), why)]
    for phrase, says in additions.items():
        lines.append("%s: [%s]" % (_yaml(phrase), ", ".join(_yaml(s) for s in says)))
    return "\n".join(lines) + "\n"


def write(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def unwrite(path, old, new):
    """Put `new` in place of the `old` block just appended (or take it out again)."""
    with open(path, encoding="utf-8") as f:
        body = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(body[:body.rindex(old)] + new + body[body.rindex(old) + len(old):])


def cases_now():
    """{phrase: expected result} from tests/phrases.yaml: what must go on routing the same way."""
    with open(CASES, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run(targets, model, wanted, dry, why):
    """Ask, check, keep. targets: [(where it came from, phrase)]."""
    want = routes([p for _, p in targets])
    unknown = [(src, p) for src, p in targets if want[p] in NOWHERE or want[p].startswith("miss")]
    for src, p in unknown:
        print("skipped  %-44s %s doesn't go anywhere yet (%s)" % (p, src, want[p]))
    targets = [(src, p) for src, p in targets if (src, p) not in unknown]
    if not targets:
        return 0

    have, suggested = known(), {}
    for i in range(0, len(targets), BATCH):
        batch = [p for _, p in targets[i:i + BATCH]]
        print("asking Claude about %d phrases (%d-%d of %d)..." % (len(batch), i + 1, i + len(batch), len(targets)))
        for phrase, says in ask(batch, model, wanted).items():
            keep = teaching.tidy(phrase, says, have)
            if keep:
                suggested[phrase] = keep
    if not suggested:
        print("nothing new suggested")
        return 0

    if dry:
        for phrase, says in suggested.items():
            print("\n%s\n  %s" % (phrase, "\n  ".join(says)))
        print("\n%d phrases, %d wordings; nothing written (--dry)"
              % (len(suggested), sum(map(len, suggested.values()))))
        return 0

    # Where everything goes beforehand: the new wordings, and every phrase tests/phrases.yaml covers
    # (against where those go now, not where the file says they should: some already disagree).
    cases = list(cases_now())
    words = [w for says in suggested.values() for w in says]
    before = routes(words + cases)

    # One that already lands in the right place needs no entry; nothing needs it.
    additions, already = {}, 0
    for phrase, says in suggested.items():
        new = [w for w in says if before[w] != want[phrase]]
        already += len(says) - len(new)
        if new:
            additions[phrase] = new
    if not additions:
        print("%d wordings suggested, all of them already understood; nothing to add" % already)
        return 0

    added = block(additions, why)
    write(INTENTS, added)
    after = routes([w for says in additions.values() for w in says] + cases)

    # Each new wording must reach the phrase it was written for.
    kept, dropped = {}, []
    for phrase, says in additions.items():
        good = [w for w in says if after[w] == want[phrase]]
        dropped += [(w, phrase, after[w]) for w in says if w not in good]
        if good:
            kept[phrase] = good

    # And no phrase that already worked may be pulled away. Whichever added phrase it was pulled into
    # is the one at fault: its whole entry comes back out, printed, to pick from by hand.
    taken = [(c, after[c], before[c]) for c in cases if after[c] != before[c]]
    blamed = set()
    for c, went, _ in taken:
        owners = [phrase for phrase in kept if want[phrase] == went]
        if not owners:  # it didn't land on an added phrase (it asks, or gave up): blame the nearest wording
            near = difflib.get_close_matches(c, [w for ws in kept.values() for w in ws], 1, 0.4)
            owners = [phrase for phrase in kept if near and near[0] in kept[phrase]]
        blamed.update(owners)
        if not owners:
            print("conflict %-44s goes to %s now, not %s -- no added wording to blame, look at it by hand"
                  % (c, went, before[c]))
    for c, went, was in taken:
        print("conflict %-44s goes to %s now, not %s" % (c, went, was))
    for phrase in sorted(blamed):
        print("put back %-44s (it pulled a known phrase away) -> %s" % (phrase, ", ".join(kept.pop(phrase))))

    if kept != additions:
        unwrite(INTENTS, added, block(kept, why) if kept else "")
    for w, phrase, got in dropped:
        print("dropped  %-44s wanted %s, goes to %s" % (w, want[phrase], got))

    keeping = [(w, want[phrase]) for phrase, says in kept.items() for w in says]
    if keeping:
        write(CASES, "\n# --- added %s by tools/phrases.py (%s)\n" % (time.strftime("%Y-%m-%d"), why) +
              "".join('"%s": %s\n' % (w, route) for w, route in keeping))
    for phrase, says in kept.items():
        print("kept     %-44s %s" % (phrase, ", ".join(says)))
    print("\n%d wordings added for %d phrases (%d already understood, %d dropped, %d put back)."
          % (len(keeping), len(kept), already, len(dropped), len(blamed)))
    if keeping:
        print("config/intents.yaml and tests/phrases.yaml updated; run tools/dryrun.py --check.")
    return 0


def taught():
    """What JARVIS taught itself while running (~/.local/share/jarvis/taught.jsonl)."""
    have = known()
    try:
        with open(teaching.TAUGHT, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except OSError:
        print("nothing yet: %s isn't there" % teaching.TAUGHT)
        return 0
    for r in rows:
        where = "in the code" if _norm(r["said"]) in have else "learned.yaml only"
        print("%s  %-44s means %-28s (%s, %s)" % (r["time"], r["said"], r["means"], r.get("from", "?"), where))
    print("\n%d taught itself. Move the ones worth keeping into config/intents.yaml." % len(rows))
    return 0


def main(argv):
    dry = "--dry" in argv
    model = MODEL
    if "--model" in argv:
        model = argv[argv.index("--model") + 1]
        argv = argv[:argv.index("--model")] + argv[argv.index("--model") + 2:]
    wanted = WANTED
    if "--n" in argv:
        wanted = int(argv[argv.index("--n") + 1])
        argv = argv[:argv.index("--n")] + argv[argv.index("--n") + 2:]
    if "--taught" in argv:
        return taught()
    asking = "--ask" in argv or "--misses" in argv
    misses = "--misses" in argv
    rest = [a for a in argv if not a.startswith("--")]

    if misses:
        targets, skipped = nearly_missed(rest[0] if rest else 0)
        why = "from the miss log"
    else:
        targets, skipped = teaching.gaps(known(), [a.replace("-", "_") for a in rest])
        why = "the skills registry's own phrases"
    if not targets:
        print("nothing to do: every phrase has other wordings already")
        return 0
    if not asking:
        for src, phrase in targets:
            print("%-20s %s" % (src, phrase))
        if skipped:
            print("\n%d with a value in them (write a {slot} template by hand):" % len(skipped))
            for src, phrase in skipped:
                print("%-20s %s" % (src, phrase))
        print("\n%d phrases with no other wordings. tools/phrases.py --ask writes them." % len(targets))
        return 0
    return run(targets, model, wanted, dry, why)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
