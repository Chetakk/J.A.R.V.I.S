"""What JARVIS did with what you said: one JSON line per request it handled.

  {"time", "how", "text", "phrase", "skill", "score"}  (only the fields that apply)

`how` is how it got there: pattern (a skill's own words), command (a saved command's wording), wording (a
wording it already knows, word for word), meaning (the matcher, with its score), fuzzy (near enough to a
command's wording), confirmed (you said yes to "did you mean X?"). Failures aren't in here: those go to
misses.jsonl (core/misslog.py).

core/teaching.py reads this: the phrases you lean on are the ones Claude writes other wordings for first,
and a match that only just got there goes to Claude to check what you really meant. Only counting happens
here; what anything means is Claude's call.

A request with a value in it, a secret or a private note keeps no text (Jarvis.handled decides), so this
isn't a record of everything you said. `teaching.log: false` turns it off; only the last MAX lines are kept.
"""
import json
import os
import time

PATH = os.path.expanduser("~/.local/share/jarvis/outcomes.jsonl")
MAX = 20000  # lines kept (~2 MB); the oldest go when it grows past this


def log(how, text="", phrase="", skill="", score=0.0, path=None):
    """Record one handled request. Never raises: a log that won't write must not stop JARVIS working."""
    path = path or PATH
    entry = {"time": time.strftime("%Y-%m-%d %H:%M"), "how": how}
    for key, value in (("text", text.strip()), ("phrase", phrase), ("skill", skill)):
        if value:
            entry[key] = value
    if score:
        entry["score"] = round(score, 2)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        if os.path.getsize(path) > MAX * 110:  # a rough line size, checked cheaply
            trim(path)
    except OSError as e:
        print("[outcomes] can't write %s: %r" % (path, e))


def trim(path=None):
    """Keep the last MAX lines."""
    path = path or PATH
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > MAX:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX:])
        os.replace(tmp, path)


def read(days=0, path=None):
    """The rows, oldest first (a half-written line is skipped); days: only the last so many."""
    rows = []
    try:
        with open(path or PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        return []
    if days:
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - float(days) * 86400))
        rows = [r for r in rows if r.get("time", "") >= since]
    return rows


def doubtful(rows, threshold, margin=0.06):
    """{what you said: the phrase it reached}, most often said first, for matches that only just got there:
    a score barely over the threshold, or a saved command's wording that was merely close."""
    counts, phrase = {}, {}
    for r in rows:
        text = r.get("text")
        if text and r.get("phrase") and (r.get("how") == "fuzzy" or (
                r.get("how") == "meaning" and threshold <= r.get("score", 0) < threshold + margin)):
            counts[text] = counts.get(text, 0) + 1
            phrase[text] = r["phrase"]
    return {t: phrase[t] for t in sorted(counts, key=lambda t: -counts[t])}


def counted(rows, field):
    """{phrase or skill: how many requests reached it}."""
    counts = {}
    for r in rows:
        if r.get(field):
            counts[r[field]] = counts.get(r[field], 0) + 1
    return counts


def said_for(rows, phrase):
    """What you really said that reached `phrase`, in your own words."""
    out = []
    for r in rows:
        text = r.get("text")
        if text and (r.get("phrase") or "").lower() == phrase.lower() and text.lower() != phrase.lower() and text not in out:
            out.append(text)
    return out
