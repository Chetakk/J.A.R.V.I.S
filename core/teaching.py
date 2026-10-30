"""JARVIS teaches itself other ways of saying things: Claude decides what things mean and writes the wordings,
JARVIS checks each one and learns it, without asking. "Did you learn anything new?" tells you what it picked up.

Two things get taught:

  what you got wrong  something JARVIS didn't place (misses.jsonl), or only just placed (outcomes.jsonl). Claude
                      says which of JARVIS's own phrases you meant, or that you meant none of them.
  what you got right  the phrases you actually use, then every other phrase JARVIS has. Claude writes other ways
                      of saying them, in your own style, so a different wording next time still works; and it
                      says when a phrase has enough, after which it's left alone.

Code does the counting (what's used most, what's been asked about already); meaning is Claude's call.

What's kept goes into ~/.local/share/jarvis/learned.yaml (yours, not part of the code: edit or delete lines
freely) and into the running matcher, so it works at once. Checking a wording only ever matches patterns and
compares sentences, never runs a handler, so it has no effect on your desktop.

Nothing with a number or a secret in it is sent to Claude or learned, and system mode's requests stay local.

jarvis.py runs a pass on a timer (settings `teaching`). tools/phrases.py writes wordings into the code by hand.
"""
import json
import os
import re
import time

import numpy as np
import yaml

from core import learned, misslog, outcomes
from core.intent import ASK_FOR_VALUE, _norm, wrong_kind
from core.registry import opposite_direction
from core.store import load_json, save_json
from skills.notes import SECRET
from skills.registry import entries

STATE = os.path.expanduser("~/.local/share/jarvis/teaching.json")
CASES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "phrases.yaml")
LONGEST = 16  # words in a wording: long enough for whole sentences with a reason in them
TOO_MANY = 40  # wordings a phrase can have before it's left alone, whatever Claude says
SHOWN = 30  # of those, how many Claude is shown
VAGUE = re.compile(r"^(?:(?:that'?s |thats )?enough|do|try|end|stop|start|go|run|fix|check|open|close|that|"
                   r"this|it|the one|again|more|less|carry on|go on|get on with it|yes|no|ok|okay|sure|now|"
                   r"please)(?: it| that| this| one| again| now)?$", re.I)
# How much better another phrase has to score before Claude saying what a wording means is overruled. A
# small model scores by topic, not direction: "the sound is way too loud for me" sits as close to "volume
# up" as to "volume down", so only a clear gap means the wording really is about something else.
SURER = 0.08
NEARLY = 0.62  # as close as "did you mean X?" asks from (UNSURE in jarvis.py): a request for Claude can be stopped there

SYSTEM = (
    "You write the everyday spoken wordings for JARVIS, a Linux voice assistant. Its meaning matcher holds a "
    "list of phrases it understands; your job is other ways a person would say the very same thing out loud, "
    "so the matcher recognises them. Answer with JSON only.")
RULES = """For each phrase below, give up to %d other ways someone would say the same thing to a voice assistant.

Rules:
- Same action, exactly. Never a related one: "stop the music" is not "next song", "quiet mode" is not "mute".
- An instruction stays an instruction and a question stays a question. Never turn "stop focusing" into "am I focusing".
- Never the opposite way round. For "unmute the mic" never give "mute my mic".
- No names, numbers, filenames, apps or other fill-in values in them.
- Spoken English, lower case, no "JARVIS" and no "please". No full stops or question marks; a comma is fine.
- Give both: short ways of saying it, AND whole sentences as someone really speaks them out loud, with the
  lead-ins and reasons people put in ("the music is too loud, bring it down", "i can't read this, make it bigger",
  "i'm going to be on a call for a bit so keep quiet"). Roughly half of each.
- Include the awkward real ones ("i can't hear it", "my eyes hurt"), not just polite rewordings.
- Each wording must be different from the others, from the phrase, and from the ones it already knows.
- Nothing so vague it could mean another action. "end it" or "that's enough" could be anything; a wording
  has to say what it acts on, even inside a longer sentence.
- Stay on the same thing the phrase is about. A card on screen is not a window, a note is not a reminder.

A phrase may show "knows:", the wordings JARVIS already has, and "they said:", how this person really says it.
Write new ways, in that person's style. It is your call when a phrase has enough: set "enough" to true when
more wordings would add nothing, and JARVIS won't ask about that phrase again. Give no wordings for a phrase
you have nothing safe to say for.

Phrases:
%s

Reply with one JSON object, nothing else: {"the phrase": {"wordings": ["wording", "wording"], "enough": false}, ...}."""

MEANT = """Someone said each of these to JARVIS, a voice assistant. It didn't understand them, or only just did.

What they said:
%s

JARVIS's own phrases:
%s

For each one, say which single phrase from that list it meant, if any. It may be misheard: speech recognition
gets words wrong, so work out what they meant to say. It is your call, and "none" is the right answer whenever
you aren't sure: a wrong guess teaches JARVIS to do the wrong thing from then on. Only answer with a phrase when
someone saying those words would always want exactly that, with nothing filled in (a request naming a file, app,
project, time or number means none: those take a value). A question for you, or a chat, means none.

Reply with one JSON object, nothing else: {"what they said": "the phrase it meant", ...}. Leave out the
ones that meant none of them."""


def fit_to_send(text):
    """Nothing with a number (a value, maybe a code) or a word for a secret leaves this computer to be taught from."""
    return bool(text) and not re.search(r"\d", text) and not SECRET.search(text)


def known(matcher=None, path=None):
    """Every wording and phrase the matcher already holds, word for word -> the phrase it runs."""
    if matcher is not None:
        out = {_norm(t): t for t in matcher.targets}
        out.update(matcher.exact)
        return out
    with open(path, encoding="utf-8") as f:
        intents = yaml.safe_load(f) or {}
    intents.update(intents.pop(ASK_FOR_VALUE, None) or {})
    out = {}
    for phrase, says in intents.items():
        out[_norm(phrase)] = phrase
        for s in says or []:
            out[_norm(s)] = phrase
    return out


def _plain_phrases(only=()):
    """[(skill, phrase, takes a value)] from the skills registry."""
    out = []
    for e in entries():
        if only and not any(o in (e["name"], e["group"]) for o in only):
            continue
        for phrase in e["phrases"]:
            phrase = phrase.split(" (")[0].strip()  # "delete <file> (to the Trash)" -> the words themselves
            out.append((e["name"], phrase, "<" in phrase or bool(re.search(r"\d", phrase))))
    return out


def gaps(have, only=()):
    """([(skill, phrase)] the matcher has nothing for, [(skill, phrase)] needing a {slot} template by hand:
    "focus for 45 minutes" needs a {dur}, not wordings)."""
    found = _plain_phrases(only)
    return ([(s, p) for s, p, value in found if not value and _norm(p) not in have],
            [(s, p) for s, p, value in found if value])


def missed(have, days=0, limit=0):
    """What you said that JARVIS sent on to Claude, most often said first, still not understood. Only those: one
    said in system mode was kept off Claude on purpose, and noise or Claude's own "do" phrases aren't yours."""
    counts, seen = {}, learned.load()
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - float(days) * 86400)) if days else ""
    for e in misslog.read():
        text = (e.get("text") or "").lower().strip()
        if e.get("outcome") != "claude" or e.get("time", "") < since or _norm(text) in have \
                or text in seen["wordings"] or text in seen["declined"]:
            continue
        if fit_to_send(text) and 1 < len(text.split()) <= LONGEST:
            counts[text] = counts.get(text, 0) + 1
    order = sorted(counts, key=lambda t: -counts[t])
    return order[:limit] if limit else order


def parse(reply, keys):
    """Claude's JSON, keyed back onto the exact phrases asked about (empty when it answered with none)."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return {}
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return {}
    back = {k.lower(): k for k in keys}
    return {back[k.lower()]: v for k, v in (got.items() if isinstance(got, dict) else ()) if k.lower() in back}


def ask_wordings(once, phrases, wanted=6, notes=None):
    """({phrase: [wordings]}, {phrases Claude says have enough}). once(prompt, system) -> its reply text.
    notes: {phrase: what to show beside it} (what JARVIS knows already, how you really say it)."""
    lines = "\n".join("- %s%s" % (p, "  (%s)" % notes[p] if notes and notes.get(p) else "") for p in phrases)
    says, enough = {}, set()
    for p, v in parse(once(RULES % (wanted, lines), SYSTEM), phrases).items():
        if isinstance(v, dict):
            if v.get("enough") is True:
                enough.add(p)
            v = v.get("wordings")
        if isinstance(v, list) and v:
            says[p] = [str(w).strip().lower().rstrip("?.!") for w in v]
    return says, enough


def ask_meant(once, saids, phrases):
    """{what you said: the phrase Claude says it meant}, for the ones it was sure about."""
    reply = once(MEANT % ("\n".join("- %s" % s for s in saids), "\n".join("- %s" % p for p in sorted(phrases))),
                 SYSTEM)
    return {s: p.strip() for s, p in parse(reply, saids).items() if isinstance(p, str) and p.strip() in phrases}


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
    """The skill or saved command that really owns these words, if any: a wording the matcher knows is taken before
    any pattern is tried, so teaching a skill's own words to mean something else would break that skill.

    A pattern owns them when most of the sentence is its own fixed words. One that's mostly a value ("(.+) from
    (.+)") matches all sorts of sentences and hands most of them straight back, so it owns nothing.

    Only patterns are matched here, never a handler: asking the question must never do the thing."""
    text = wording.strip().rstrip("?.!")
    for skill in jarvis.skills.all:
        for pattern, _ in skill.rules:
            m = pattern.match(text)
            if m and sum(len(g) for g in m.groups() if g) * 2 < len(text):
                return type(skill).__name__
    if jarvis.registry.match_exact(text):
        return "a saved command"
    return None


_guard = {"key": None, "texts": [], "matrix": None, "cases": {}}


def _guarded_sentences(matcher):
    """Every sentence tests/phrases.yaml says where it should go, with its vector. A wording learned here isn't
    seen by tools/dryrun.py (that's the code's wordings, not yours), so this is what keeps it from pulling them."""
    try:
        with open(CASES, encoding="utf-8") as f:
            text = f.read()
        if _guard["key"] != text:
            cases = yaml.safe_load(text) or {}
            texts = [t for t in cases if isinstance(t, str)]
            vectors = [matcher.embed(texts[i:i + 64]) for i in range(0, len(texts), 64)]
            _guard.update(key=text, texts=texts, cases=cases, matrix=np.vstack(vectors) if vectors else None)
    except (OSError, yaml.YAMLError):
        return [], None, {}
    return _guard["texts"], _guard["matrix"], _guard["cases"]


def pulls(jarvis, wording, phrase):
    """A known sentence that learning this wording could pull away from where it goes, or None. The matcher can't
    be shown what a sentence it has never heard would do, but these it can ("go into more detail" taking
    "more detail" away from Claude)."""
    matcher = jarvis.intents
    texts, matrix, cases = _guarded_sentences(matcher)
    if not texts:
        return None
    scores = matrix @ matcher.embed([wording])[0]
    for i in scores.argsort()[::-1]:
        if scores[i] < NEARLY:
            return None
        text = texts[i]
        if scores[i] < matcher.threshold and cases[text] != "claude":
            continue  # only near: what JARVIS already handles itself isn't asked about, only what's going to Claude
        if phrase in cases and cases[text] == cases[phrase]:
            continue  # goes to the same place anyway
        if (matcher.candidates(text, n=1) + [(None, 0.0)])[0][0] != phrase:
            return text
    return None


def why_not(jarvis, wording, phrase, data=None):
    """Why this wording can't be taught, or "" when it can. Learning happens without asking, so anything already
    spoken for is left alone."""
    data = data or learned.load()
    if not fit_to_send(wording) or not learned.worth_learning(wording, phrase, data, LONGEST):
        return "not worth learning (too long, a number or secret in it, or already known)"
    if opposite_direction(wording, phrase) or wrong_kind(wording, phrase):
        return "it goes the other way from “%s”" % phrase
    takes = claimed(jarvis, wording)
    if takes:
        return "%s already takes it" % takes
    close = dict(jarvis.intents.candidates(wording, n=8))
    best, score = (list(close.items()) + [(None, 0.0)])[0]
    if best and best != phrase and score >= jarvis.intents.threshold \
            and score - close.get(phrase, 0.0) >= SURER:
        return "it already means “%s” (%.2f, against %.2f)" % (best, score, close.get(phrase, 0.0))
    pulled = pulls(jarvis, wording, phrase)
    if pulled:
        return "it would pull “%s” away" % pulled
    return ""


def teach(jarvis, pairs, why=""):
    """Learn the wordings that are safe: [(wording, phrase)] -> ([kept], [(wording, phrase, why not)]).

    Saved to learned.yaml and added to the running matcher, so the next thing you say can use them."""
    kept, refused, data = [], [], learned.load()
    for wording, phrase in pairs:
        reason = why_not(jarvis, wording, phrase, data)
        if reason:
            refused.append((wording, phrase, reason))
            continue
        learned.remember(wording, phrase, why=why)
        jarvis.intents.learn(wording, phrase)
        data["wordings"][wording.lower().strip()] = phrase  # (so the rest of this batch sees it)
        kept.append((wording, phrase))
    return kept, refused


def _state():
    state = load_json(STATE, {})
    return {"last": state.get("last", 0), "placed": list(state.get("placed") or []),
            "phrases": dict(state.get("phrases") or {})}


def _save(state):
    try:
        save_json(STATE, dict(state, placed=state["placed"][-2000:]))
    except OSError as e:
        print("[teaching] can't write %s: %r" % (STATE, e))


def due_in(cfg=None):
    """Seconds until the next pass is due. Kept across restarts: restarting JARVIS doesn't mean asking Claude again."""
    return max(0, _state()["last"] + (cfg or {}).get("every", 3) * 3600 - time.time())


def to_place(jarvis, state, rows, cfg):
    """What you said that JARVIS didn't place or only just did, not put to Claude before, most often said first."""
    have = known(jarvis.intents)
    near = outcomes.doubtful(rows, jarvis.intents.threshold, cfg.get("doubt", 0.06))
    out = missed(have, cfg.get("days", 30))
    # (a doubtful one is known only if it's been learned as itself; the matcher's rows of phrases don't count)
    out += [t.lower() for t in near if fit_to_send(t) and t.lower() not in out and _norm(t) not in jarvis.intents.exact]
    return [t for t in out if t not in state["placed"]][:cfg.get("misses", 6)]


def offered(jarvis):
    """JARVIS's own phrases, for Claude to choose from: what the matcher has and what the skills say, leaving out
    the ones only ever learned from what you said (those can hold your own words) and the ones taking a value."""
    mine = set(learned.load()["wordings"].values())
    phrases = {t for t in jarvis.intents.targets if t not in mine} | {p for _, p, value in _plain_phrases() if not value}
    return {p for p in phrases if _fit_phrase(p)}


def _fit_phrase(phrase):
    return not re.search(r"[/{<\d]", phrase) and len(phrase.split()) <= 10 and fit_to_send(phrase)


def wanted(jarvis, state, rows, cfg):
    """Which phrases to ask Claude for wordings of: the ones you use most, then the skills you use most, then those
    with the fewest. Whatever's been asked about least goes first, so none of them starve; none Claude said have
    enough. [phrases]"""
    have = known(jarvis.intents)
    same = lambda p: have.get(_norm(p), p)  # noqa: E731  (the matcher's own spelling: "where was I" is "where was i")
    uses, skill_uses = {}, {k.lower(): v for k, v in outcomes.counted(rows, "skill").items()}
    for p, n in outcomes.counted(rows, "phrase").items():
        uses[same(p)] = uses.get(same(p), 0) + n
    counts = {}
    for t in jarvis.intents.exact.values():
        counts[t] = counts.get(t, 0) + 1
    pool = {p: 0 for p in uses if _fit_phrase(p) and len(p.split()) > 1}
    for name, p, value in _plain_phrases():
        if not value and _fit_phrase(same(p)):
            pool[same(p)] = max(pool.get(same(p), 0), skill_uses.get(name.replace("_", "").lower(), 0))
    asked = state["phrases"]
    order = [p for p in pool if not asked.get(p, {}).get("enough") and counts.get(p, 0) < TOO_MANY]
    order.sort(key=lambda p: (asked.get(p, {}).get("asked", 0), -uses.get(p, 0), -pool[p], counts.get(p, 0)))
    return order[:cfg.get("phrases", 6)]


def _notes(jarvis, want, rows):
    """What to show Claude beside each phrase: the ways JARVIS knows already, and how you really say it."""
    out = {}
    for p in want:
        knows = [w for w, t in jarvis.intents.exact.items() if t == p][:SHOWN]
        said = [t for t in outcomes.said_for(rows, p) if fit_to_send(t)][:5]
        parts = (["knows: %s" % "; ".join(knows)] if knows else []) + (["they said: %s" % "; ".join(said)] if said else [])
        out[p] = " ".join(parts)
    return out


def run(jarvis, cfg=None):
    """One teaching pass, at most two questions to Claude. What it did, as a line for the log ("" for nothing)."""
    cfg = cfg or {}
    if not jarvis.intents:
        return ""
    state = _state()
    state["last"] = time.time()
    _save(state)
    once = lambda prompt, system: jarvis.claude.once(prompt, system, model=cfg.get("model") or "", timeout=120)  # noqa: E731
    rows = outcomes.read(cfg.get("days", 30))
    done = []

    saids = to_place(jarvis, state, rows, cfg)
    if saids:
        meant = ask_meant(once, saids, offered(jarvis))
        state["placed"] += saids  # asked once: "none" stays none, and it isn't sent again
        _save(state)
        kept, refused = teach(jarvis, list(meant.items()), "something you said")
        _log(kept, refused)
        if kept:
            done.append("placed %d of %d things you said" % (len(kept), len(saids)))

    want = wanted(jarvis, state, rows, cfg)
    if want:
        says, enough = ask_wordings(once, want, cfg.get("wordings", 6), _notes(jarvis, want, rows))
        for p in want:
            entry = state["phrases"].setdefault(p, {})
            entry["asked"] = entry.get("asked", 0) + 1
            if p in enough:
                entry["enough"] = True
        _save(state)
        pairs = [(w, p) for p, words in says.items() for w in tidy(p, words, known(jarvis.intents))]
        kept, refused = teach(jarvis, pairs, "a new way of saying it")
        _log(kept, refused)
        if kept:
            done.append("%d new wordings for %d phrases" % (len(kept), len({p for _, p in kept})))
    return "; ".join(done)


def _log(kept, refused):
    """Learning is silent: only the log, never the chat or the speaker."""
    for wording, phrase in kept:
        print("[teaching] learned %r means %r" % (wording, phrase))
    for wording, phrase, why in refused:
        print("[teaching] not keeping %r for %r: %s" % (wording, phrase, why))
