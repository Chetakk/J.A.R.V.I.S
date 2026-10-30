"""Wordings JARVIS teaches itself: Claude writes other ways of saying what JARVIS already does, JARVIS
checks each one and keeps the ones that hold up.

Two things get taught, both decided by Claude:

  gaps     a phrase the skills registry lists that the meaning matcher has no other wordings for
  misses   something you said that JARVIS didn't place (~/.local/share/jarvis/misses.jsonl); Claude says
           which of JARVIS's own phrases it meant, or that it meant none of them

What survives goes into ~/.local/share/jarvis/learned.yaml like anything else JARVIS learned (yours, not
part of the code: edit or delete lines freely) and into the running matcher, so it works at once. Every
wording kept is also written to ~/.local/share/jarvis/taught.jsonl, so you can see what it taught itself.

A wording is only kept if it can't do any harm: it must not be a question JARVIS already answers another
way, a wording a skill's own pattern already takes, or a sentence that already means something else to
the matcher. Nothing here ever runs a handler, so checking has no effect on your desktop.

The runtime runs a pass on a timer (settings `teaching`, wired up in jarvis.py). tools/phrases.py is the
same thing by hand, with the fuller check that only a dry run can do.
"""
import json
import os
import re
import time

from core import learned, misslog
from core.intent import ASK_FOR_VALUE, _norm
from skills.registry import entries

TAUGHT = os.path.expanduser("~/.local/share/jarvis/taught.jsonl")
VAGUE = re.compile(r"^(?:(?:that'?s |thats )?enough|do|try|end|stop|start|go|run|fix|check|open|close|that|"
                   r"this|it|the one|again|more|less|carry on|go on|get on with it|yes|no|ok|okay|sure|now|"
                   r"please)(?: it| that| this| one| again| now)?$", re.I)
# How much better another phrase has to score before Claude saying what a wording means is overruled. A
# small model scores by topic, not direction: "the sound is way too loud for me" sits as close to "volume
# up" as to "volume down", so only a clear gap means the wording really is about something else.
SURER = 0.08

SYSTEM = (
    "You write the everyday spoken wordings for JARVIS, a Linux voice assistant. Its meaning matcher holds a "
    "list of phrases it understands; your job is other ways a person would say the very same thing out loud, "
    "so the matcher recognises them. Answer with JSON only.")
RULES = """For each phrase below, give %d other ways someone would say the same thing to a voice assistant.

Rules:
- Same action, exactly. Never a related one: "stop the music" is not "next song", "quiet mode" is not "mute".
- An instruction stays an instruction and a question stays a question. Never turn "stop focusing" into "am I focusing".
- Never the opposite way round. For "unmute the mic" never give "mute my mic".
- No names, numbers, filenames, apps or other fill-in values in them. Skip the phrase if you can't avoid them.
- Spoken English, lower case, no "JARVIS" and no "please". No full stops or question marks; a comma is fine.
- Give both: short ways of saying it, AND whole sentences as someone really speaks them out loud, with the
  lead-ins and reasons people put in ("the music is too loud, bring it down", "i can't read this, make it bigger",
  "i'm going to be on a call for a bit so keep quiet"). Roughly half of each.
- Include the awkward real ones ("i can't hear it", "my eyes hurt"), not just polite rewordings.
- Each wording must be different from the others, and from the phrase itself.
- Nothing so vague it could mean another action. "end it" or "that's enough" could be anything; a wording
  has to say what it acts on, even inside a longer sentence.
- Stay on the same thing the phrase is about. A card on screen is not a window, a note is not a reminder.

It is your call which phrases are worth wordings: leave out any you have nothing safe to say for.

Phrases:
%s

Reply with one JSON object, nothing else: {"the phrase": ["wording", "wording"], ...}."""

MEANT = """Someone said each of these to JARVIS, a voice assistant, and it didn't know what they meant.

What they said:
%s

JARVIS's own phrases:
%s

For each one, say which single phrase from that list it meant, if any. It is your call, and "none" is the
right answer whenever you aren't sure: a wrong guess teaches JARVIS to do the wrong thing from then on.
Only answer with a phrase when someone saying those words would always want exactly that, with nothing
filled in (a request naming a file, app, project, time or number means none: those take a value).

Reply with one JSON object, nothing else: {"what they said": "the phrase it meant", ...}. Leave out the
ones that meant none of them."""


def known(matcher=None, path=None):
    """Every wording the matcher already holds, word for word -> the phrase it runs."""
    if matcher is not None:
        return dict(matcher.exact)
    import yaml  # (only the by-hand tool reads the file itself; the runtime has the matcher)
    with open(path, encoding="utf-8") as f:
        intents = yaml.safe_load(f) or {}
    intents.update(intents.pop(ASK_FOR_VALUE, None) or {})
    out = {}
    for phrase, says in intents.items():
        out[_norm(phrase)] = phrase
        for s in says or []:
            out[_norm(s)] = phrase
    return out


def gaps(have, only=()):
    """([(skill, phrase)] with no other wordings, [(skill, phrase)] needing a {slot} template by hand)."""
    out, templated = [], []
    for e in entries():
        if only and not any(o in (e["name"], e["group"]) for o in only):
            continue
        for phrase in e["phrases"]:
            phrase = phrase.split(" (")[0].strip()  # "delete <file> (to the Trash)" -> the words themselves
            if "<" in phrase or re.search(r"\d", phrase):  # "focus for 45 minutes" needs a {dur}, not wordings
                templated.append((e["name"], phrase))
            elif _norm(phrase) not in have:
                out.append((e["name"], phrase))
    return out, templated


def missed(have, days=0, limit=0):
    """What you said that JARVIS didn't place, most often said first, still not understood."""
    counts, seen = {}, learned.load()
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - float(days) * 86400)) if days else ""
    for e in misslog.read():
        text = (e.get("text") or "").lower().strip()
        if e["time"] < since or _norm(text) in have or text in seen["wordings"] or text in seen["declined"]:
            continue
        if e.get("outcome") in ("ignored", "unclear") or not 1 < len(text.split()) <= 12:
            continue
        counts[text] = counts.get(text, 0) + 1
    order = sorted(counts, key=lambda t: -counts[t])
    return order[:limit] if limit else order


def parse(reply, keys):
    """Claude's JSON, keyed back onto the exact phrases asked about ([] / {} when it answered with none)."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return {}
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return {}
    back = {k.lower(): k for k in keys}
    out = {}
    for k, v in (got.items() if isinstance(got, dict) else ()):
        if k.lower() in back:
            out[back[k.lower()]] = v
    return out


def ask_wordings(once, phrases, wanted=6):
    """{phrase: [wordings]} from Claude. once(prompt, system) -> its reply text."""
    reply = once(RULES % (wanted, "\n".join("- %s" % p for p in phrases)), SYSTEM)
    got = parse(reply, phrases)
    return {p: [str(w).strip().lower().rstrip("?.!") for w in v]
            for p, v in got.items() if isinstance(v, list) and v}


def ask_meant(once, saids, phrases):
    """{what you said: the phrase Claude says it meant}, for the ones it was sure about."""
    reply = once(MEANT % ("\n".join("- %s" % s for s in saids), "\n".join("- %s" % p for p in sorted(phrases))),
                 SYSTEM)
    got = parse(reply, saids)
    return {s: str(p).strip() for s, p in got.items()
            if isinstance(p, str) and str(p).strip() in phrases}


def tidy(phrase, says, have):
    """The wordings worth checking: new, not the phrase again, no values or markup in them."""
    seen, out = set(), []
    for w in says:
        w = (w or "").strip().lower().rstrip("?.!")
        if w and _norm(w) != _norm(phrase) and _norm(w) not in have and _norm(w) not in seen \
                and not re.search(r"[<>{}\[\]:\"]", w) and not VAGUE.match(w):
            seen.add(_norm(w))
            out.append(w)
    return out


def claimed(jarvis, wording):
    """The skill or saved command that really owns these exact words, if any.

    Only a pattern made of the words themselves counts (no value in it, so nothing filled in): those are
    a skill's own phrase, and teaching them to mean something else would break it. A pattern with a value
    in it ("summarise {file}") matches all sorts of sentences and hands most of them straight back, so it
    owns nothing; a wording the matcher knows word for word is taken before any pattern is tried anyway.

    Only patterns are matched here, never a handler: asking the question must never do the thing."""
    text = wording.strip().rstrip("?.!")
    for skill in jarvis.skills.all:
        for pattern, _ in skill.rules:
            m = pattern.match(text)
            if m and not m.groups():  # matched on its own words alone
                return type(skill).__name__
    if jarvis.registry.match_exact(text):
        return "a saved command"
    return None


def why_not(jarvis, wording, phrase, data=None):
    """Why this wording can't be taught, or "" when it can.

    A wording is only safe when nothing else already wants it: the matcher can't be shown what a sentence
    it has never heard would have done, so anything already spoken for is left alone."""
    data = data or learned.load()
    if not learned.worth_learning(wording, phrase, data):
        return "not worth learning (too long, has a number in it, or already known)"
    takes = claimed(jarvis, wording)
    if takes:
        return "%s already takes it" % takes
    close = dict(jarvis.intents.candidates(wording, n=8))
    best, score = (list(close.items()) + [(None, 0.0)])[0]
    if best and best != phrase and score >= jarvis.intents.threshold \
            and score - close.get(phrase, 0.0) >= SURER:
        return "it already means “%s” (%.2f, against %.2f)" % (best, score, close.get(phrase, 0.0))
    return ""


def teach(jarvis, pairs, note=""):
    """Keep the wordings that are safe: [(wording, phrase)] -> ([kept], [(wording, phrase, why not)]).

    Saved to learned.yaml and added to the running matcher, so the next thing you say can use them."""
    kept, refused, data = [], [], learned.load()
    for wording, phrase in pairs:
        why = why_not(jarvis, wording, phrase, data)
        if why:
            refused.append((wording, phrase, why))
            continue
        learned.remember(wording, phrase)
        jarvis.intents.learn(wording, phrase)
        data["wordings"][wording.lower().strip()] = phrase  # (so the rest of this batch sees it)
        kept.append((wording, phrase))
        _record(wording, phrase, note)
    return kept, refused


def _record(wording, phrase, note):
    try:
        with open(TAUGHT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M"), "said": wording,
                                "means": phrase, "from": note}) + "\n")
    except OSError as e:
        print("[teaching] can't write %s: %r" % (TAUGHT, e))


def once_with(claude, model=""):
    """once(prompt, system) through Claude Code, outside JARVIS's conversation."""
    return lambda prompt, system: claude.once(prompt, system, model=model)


def run(jarvis, cfg=None):
    """One teaching pass. Returns what it did, as a line for the log ("" when there was nothing to do)."""
    cfg = cfg or {}
    if not jarvis.intents:
        return ""
    once = once_with(jarvis.claude, cfg.get("model") or "")
    have = known(jarvis.intents)
    done = []

    saids = missed(have, cfg.get("miss_days", 30), cfg.get("misses", 6))
    if saids:
        phrases = {p for p in jarvis.intents.exact.values() if not p.startswith("/") and "{" not in p}
        pairs = list(ask_meant(once, saids, phrases).items())
        kept, refused = teach(jarvis, pairs, "a miss")
        done += ["learned %d of %d things you'd said" % (len(kept), len(saids))] if kept else []
        _log(jarvis, kept, refused)

    want, _ = gaps(known(jarvis.intents))
    want = [p for _, p in want][:cfg.get("phrases", 6)]
    if want:
        pairs = [(w, p) for p, says in ask_wordings(once, want, cfg.get("wordings", 6)).items()
                 for w in tidy(p, says, known(jarvis.intents))]
        kept, refused = teach(jarvis, pairs, "a phrase with no wordings")
        done += ["%d new wordings for %d phrases" % (len(kept), len({p for _, p in kept}))] if kept else []
        _log(jarvis, kept, refused)
    return "; ".join(done)


def _log(jarvis, kept, refused):
    for wording, phrase in kept:
        jarvis.heard(wording, "taught itself: means “%s”" % phrase)
    for wording, phrase, why in refused:
        print("[teaching] not keeping %r for %r: %s" % (wording, phrase, why))
