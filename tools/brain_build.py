#!/usr/bin/env python3
"""Fill JARVIS's mind map (brain/, see core/mind.py) from what JARVIS can already read. Rules only, no AI.

  tools/brain_build.py            # everything
  tools/brain_build.py --local    # only ~/Projects, the day tracker and notes (no Google)

People come from Calendar (who you meet, last 30 days and next 14), Gmail (who mailed you, last 30 days: sender,
subject and date only, never the body; bulk mail with an unsubscribe link is left out) and Chat (who you talk to and
in which groups; no message text is kept, it's only checked for project names). Projects from ~/Projects with their
VS Code hours from the day tracker; apps and sites you used for half an hour or more; open to-dos and notes go on the
project they name. Run again any time: notes are updated, and what you wrote in them stays.
"""
import collections
import concurrent.futures
import difflib
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.mind import Mind, key, linker, title  # noqa: E402
from core.store import load_json  # noqa: E402

DAYS_BACK, DAYS_AHEAD = 30, 14
HUB = os.environ.get("DAY_TRACKER_URL", "http://localhost:7313")
NOTES = os.path.expanduser("~/.local/share/jarvis/notes.json")
NOTEPAD = os.path.expanduser("~/Projects/Notepad")  # the Notepad app; its notes_db.py reads ProducTVT's Note table
BOT = re.compile(r"no-?reply|do-?not-?reply|notifications?@|mailer-daemon|calendar-notification|drive-shares|"
                 r"@(?:\w+\.)*(?:google|github|slack|linkedin|zoom)\.com$|@docs\.google\.com", re.I)
# a company or a service, not a person ("Anthropic, PBC", "NVIDIA Inception Program", "Accountnotify")
ORG = re.compile(r"\b(?:inc|pbc|ltd|llc|pvt|corp|program|programme|team|support|billing|notify|accountnotify|"
                 r"news(?:letter)?|alerts?|service|services|account|accounts)\b|notify", re.I)
# calendar mail: the meeting itself is already in the map
INVITE = re.compile(r"^(?:invitation|updated invitation|accepted|declined|tentatively accepted|canceled event|"
                    r"cancelled event|new event|invitation from google calendar)\b", re.I)
# project wordings too common to link on their own ("platform" in "the platform team")
LOOSE = {"platform", "yourself", "jarvis", "notes", "temp", "notepad", "projects"}


def note_name(project):
    """How a project's note is called: its spoken name ("AI Supervisor frontend", "Study Buddy"), unless that is
    spelt out for the voice ("U P E Stations") or all lower case: then the folder ("UPE Stations", "Crowning qc")."""
    said = project.aliases[0]
    if re.search(r"\b[A-Z] [A-Z]\b", said) or said == said.lower():
        return title(re.sub(r"[-_]+", " ", project.name))
    return title(said)


def day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def hours(ms):
    return round(ms / 3.6e6, 1)


class People:
    """Everyone found, merged by email (Calendar, Gmail) or by name (Chat gives no email)."""

    def __init__(self):
        self.by_email, self.by_name = {}, {}

    def get(self, name, email=""):
        email = (email or "").lower()
        p = self.by_email.get(email) if email else None
        p = p or self.by_name.get(name.lower())
        if not p:
            p = {"name": name, "email": email, "meetings": collections.Counter(), "last_meeting": {}, "next_meeting": {},
                 "mails": [], "dm": 0, "groups": set(), "from": set(), "projects": set()}
        if email and not p["email"]:
            p["email"] = email
        if p["name"] == p["email"].split("@")[0] or "@" in p["name"]:
            p["name"] = name  # a real name beats one made from the address
        if p["email"]:
            self.by_email[p["email"]] = p
        self.by_name[p["name"].lower()] = p
        self.by_name[name.lower()] = p
        return p

    def all(self):
        seen = {}
        for p in list(self.by_email.values()) + list(self.by_name.values()):
            seen[id(p)] = p
        return list(seen.values())


# --- local sources ---

def tracker():
    try:
        with urllib.request.urlopen(HUB + "/api/state", timeout=3) as r:
            return json.load(r)
    except (OSError, ValueError) as e:
        print("[brain] day tracker not reachable (%s): no hours or to-dos this time" % e)
        return {}


def usage(state):
    """{label: (total ms, ms in the last 7 days, last used)} from the day tracker's sessions."""
    week = (time.time() - 7 * 86400) * 1000
    out = {}
    for e in state.get("autoEntries") or []:
        for label, ms in (e.get("parts") or {e.get("label") or "": e["end"] - e["start"]}).items():
            total, recent, last = out.get(label, (0, 0, 0))
            out[label] = (total + ms, recent + (ms if e["end"] >= week else 0), max(last, e["end"]))
    return out


# --- Google sources ---

def google_people(people, link):
    from skills.google_common import parse_time, person, sender, header
    from system import google_account as google
    if not google.connected():
        print("[brain] Google isn't connected: no people this time")
        return {}
    now = time.time()
    me = google.run(google.api("gmail", "v1").users().getProfile(userId="me")).get("emailAddress", "").lower()
    meetings = {}

    # Calendar: meetings with at least one other person
    cal = google.api("calendar", "v3")
    from skills.google_common import rfc
    events, token = [], None
    while True:
        got = google.run(cal.events().list(calendarId="primary", timeMin=rfc(now - DAYS_BACK * 86400),
                                           timeMax=rfc(now + DAYS_AHEAD * 86400), singleEvents=True,
                                           orderBy="startTime", maxResults=250, pageToken=token))
        events += got.get("items") or []
        token = got.get("nextPageToken")
        if not token:
            break
    for ev in events:
        others = [a for a in ev.get("attendees") or [] if not a.get("self") and not a.get("resource")
                  and a.get("responseStatus") != "declined" and not BOT.search(a.get("email", ""))]
        if not others or ev.get("status") == "cancelled":
            continue
        name = title(ev.get("summary") or "Meeting")
        at = parse_time((ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date"))
        m = meetings.setdefault(name, {"dates": set(), "people": set(), "link": ev.get("htmlLink")})
        m["dates"].add(at)
        for a in others:
            p = people.get(person(a.get("displayName"), a["email"]), a["email"])
            p["from"].add("calendar")
            p["meetings"][name] += at <= now
            if at <= now:
                p["last_meeting"][name] = max(p["last_meeting"].get(name, 0), at)
            else:
                p["next_meeting"][name] = min(p["next_meeting"].get(name, at), at)
            m["people"].add(p["name"])
    print("[brain] calendar: %d events, %d meetings with people" % (len(events), len(meetings)))

    # Gmail: who wrote to you, and about what (headers only)
    gm = google.api("gmail", "v1")
    ids, token = [], None
    while len(ids) < 300:
        got = google.run(gm.users().messages().list(userId="me", q="in:inbox newer_than:%dd" % DAYS_BACK,
                                                     maxResults=100, pageToken=token))
        ids += [x["id"] for x in got.get("messages") or []]
        token = got.get("nextPageToken")
        if not token:
            break

    def meta(msg_id):  # each thread gets its own Gmail client: httplib2 isn't thread-safe
        return google.run(google.api("gmail", "v1").users().messages().get(
            userId="me", id=msg_id, format="metadata",
            metadataHeaders=["From", "Subject", "List-Unsubscribe", "Auto-Submitted", "Precedence"]))
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        msgs = list(pool.map(meta, ids))
    kept = 0
    for msg in msgs:
        name, email = sender(header(msg, "From"))
        if not email or email == me or BOT.search(email) or ORG.search(name) or INVITE.match(header(msg, "Subject")) \
                or header(msg, "List-Unsubscribe") or header(msg, "Precedence").lower() in ("bulk", "list") \
                or header(msg, "Auto-Submitted").lower() not in ("", "no"):
            continue
        p = people.get(name, email)
        p["from"].add("gmail")
        p["mails"].append((int(msg.get("internalDate", 0)) / 1000, header(msg, "Subject") or "(no subject)"))
        kept += 1
    print("[brain] gmail: %d mails read, %d from people" % (len(msgs), kept))

    # Chat: direct messages and groups you were active in
    try:
        chat = google.api("chat", "v1")
        try:
            got = google.run(google.api("people", "v1").people().get(resourceName="people/me", personFields="metadata"))
            me_chat = "users/" + got["resourceName"].split("/")[-1]
        except google.GoogleError:
            me_chat = None
        groups = {}
        spaces = [s for s in google.run(chat.spaces().list(pageSize=200)).get("spaces") or []
                  if not s.get("singleUserBotDm") and parse_time(s.get("lastActiveTime")) > now - DAYS_BACK * 86400]
        for s in spaces:
            msgs = google.run(chat.spaces().messages().list(parent=s["name"], pageSize=30,
                                                             orderBy="createTime desc")).get("messages") or []
            group = None if s.get("spaceType") == "DIRECT_MESSAGE" else title(s.get("displayName") or "Group chat")
            for x in msgs:
                who = x.get("sender") or {}
                found = {n for n in link(x.get("text") or "") and LINKED.findall(link(x.get("text") or ""))}
                if group:
                    groups.setdefault(group, {"people": set(), "projects": set(), "last": 0})
                    groups[group]["projects"] |= found
                    groups[group]["last"] = max(groups[group]["last"], parse_time(x.get("createTime")))
                if who.get("type") != "HUMAN" or (me_chat and who.get("name") == me_chat) or not who.get("displayName"):
                    continue
                p = people.get(person(who["displayName"]))
                p["from"].add("chat")
                p["projects"] |= found
                if group:
                    p["groups"].add(group)
                    groups[group]["people"].add(p["name"])
                else:
                    p["dm"] = max(p["dm"], parse_time(x.get("createTime")))
        print("[brain] chat: %d active spaces" % len(spaces))
    except google.GoogleError as e:
        print("[brain] chat skipped: %s" % e)
        groups = {}
    return {"meetings": meetings, "groups": groups}


LINKED = re.compile(r"\[\[([^\]|]+)")


def notepad(mind, projects, people):
    """Each Notepad tab (ProducTVT's Note table, ~/Projects/Notepad) as brain/notepad/<title>.md, linked to the
    projects and people it names. The vault copy follows the tab: renamed, changed or closed there, the same here."""
    sys.path.insert(0, NOTEPAD)
    try:
        import notes_db
        if not notes_db.available():
            print("[brain] Notepad's database isn't set up: no Notepad notes this time")
            return
        tabs = notes_db.Notes().list()
    except Exception as e:  # ImportError, notes_db.Unavailable: skip, the rest of the map still gets built
        print("[brain] Notepad notes skipped: %s" % e)
        return
    who = {p["name"]: [] for p in people.all() if " " in p["name"].strip() or len(p["name"]) >= 5}
    link = linker(dict({n: [w for w in ws if w.lower() not in LOOSE] for n, ws in projects.items()
                        if n.lower() not in LOOSE}, **who))
    taken = {key(w) for n, ws in list(projects.items()) + list(who.items()) for w in [n] + ws}
    keep = set()
    for t in tabs:
        text = t["content"]  # a [[name]] you write in Notepad stays a link
        named = set(LINKED.findall(link(t["title"] + "\n" + text)))
        # a misspelt title ("Pant Integnce"): on the 7 tabs, the right project scored 0.69, wrong ones 0.59 or less
        score, best = max(((difflib.SequenceMatcher(None, key(t["title"]), key(a)).ratio(), n)
                           for n, ws in projects.items() for a in [n] + ws), default=(0, None))
        if score >= 0.65:
            named.add(best)
        named = sorted(named)
        lines = ["*From Notepad, updated %s. Edit it there: changes made here aren't sent back.*" % t["at"][:16], ""]
        if named:
            lines += ["Mentions " + ", ".join("[[%s]]" % n for n in named), ""]
        lines += [link(line) for line in text.splitlines()]
        clash = key(t["title"]) in taken  # Obsidian finds [[links]] by name: the "Notepad" tab isn't the project
        path = mind.write("notepad", t["title"] + (" (note)" if clash else ""), {"notepad_id": t["id"]}, lines)
        if path:
            keep.add(path)
    for n in mind.notes():
        if n.kind == "notepad" and n.path not in keep and n.meta.get("notepad_id"):
            mind.forget(n.path)  # closed or renamed in Notepad
    print("[brain] notepad: %d notes" % len(keep))


def main(argv):
    mind = Mind()
    from system.projects import Projects
    projects = Projects().items
    names = {}
    for p in projects:
        names[note_name(p)] = [a for a in [p.name, p.title] + p.aliases if a]
    link = linker({n: [w for w in ws if w.lower() not in LOOSE] for n, ws in names.items() if n.lower() not in LOOSE})
    state = tracker()
    used = usage(state)
    people = People()
    google_part = {} if "--local" in argv else google_people(people, link)
    meetings, groups = google_part.get("meetings") or {}, google_part.get("groups") or {}
    # Obsidian finds [[links]] by name, case ignored: a meeting called like a project gets its own name
    taken = {key(w) for n, ws in names.items() for w in [n] + ws}
    called = {n: n + " (meeting)" if key(n) in taken else n for n in meetings}
    called.update({n: n + " (chat)" if key(n) in taken else n for n in groups})

    # what each project is linked to from to-dos, notes, meetings, mail and chat
    pending = collections.defaultdict(list)
    for t in state.get("todos") or []:
        if not t.get("done"):
            for n in set(LINKED.findall(link(t.get("text") or ""))):
                pending[n].append("- [ ] %s" % t["text"])
    for n_ in load_json(NOTES, []):
        named = n_.get("project") or ""
        for n in set(LINKED.findall(link(named or n_.get("text") or ""))):
            pending[n].append("- %s (note, %s)" % (n_.get("text"), day(n_.get("at") or 0)))

    for p in projects:
        name = note_name(p)
        total, recent, last = used.get("VS Code · " + p.name, (0, 0, 0))
        lines = ["**%s**" % (p.summary or p.title or "A folder in ~/Projects."), ""]
        if total:
            lines.append("- VS Code: %sh in all, %sh in the last 7 days, last on %s" % (
                hours(total), hours(recent), day(last / 1000)))
        touched = p.touched()
        if touched:
            lines.append("- Files last changed %s" % day(touched))
        lines += [""] + (["## Open"] + pending[name] if pending.get(name) else [])
        mind.write("projects", name, {"folder": p.path, "aliases": sorted(set(names[name]) - {name}),
                                      "about": p.summary or p.title}, lines)

    for label, (total, recent, last) in used.items():
        if not label or label.startswith("VS Code ·") or total < 0.5 * 3.6e6:
            continue
        name = label.split(" · ")[0] if " · " in label and "YouTube" in label else label
        mind.write("apps", label, {"kind": "site" if "." in name else "app"},
                   ["- %sh in all, %sh in the last 7 days, last on %s" % (hours(total), hours(recent),
                                                                          day(last / 1000))])

    for name, m in meetings.items():
        dates = sorted(m["dates"])
        past = [d for d in dates if d <= time.time()]
        ahead = [d for d in dates if d > time.time()]
        lines = ["- With %s" % ", ".join("[[%s]]" % title(n) for n in sorted(m["people"])),
                 "- %d time%s in the last %d days, last on %s" % (len(past), "" if len(past) == 1 else "s", DAYS_BACK,
                                                                  day(past[-1])) if past else "- Not held yet",
                 "- Next: %s" % day(ahead[0]) if ahead else "- Nothing booked in the next %d days" % DAYS_AHEAD]
        about = LINKED.findall(link(name))
        if about:
            lines.append("- About %s" % ", ".join("[[%s]]" % n for n in sorted(set(about))))
        mind.write("meetings", called[name], {"times": len(dates)}, lines)

    for name, g in groups.items():
        lines = ["- People talking: %s" % (", ".join("[[%s]]" % title(n) for n in sorted(g["people"])) or "nobody else"),
                 "- Last message %s" % day(g["last"])]
        if g["projects"]:
            lines.append("- Mentions %s" % ", ".join("[[%s]]" % n for n in sorted(g["projects"])))
        mind.write("groups", called[name], {}, lines)

    for p in people.all():
        lines = []
        if p["meetings"]:
            lines.append("## Meetings")
            for n, c in p["meetings"].most_common(8):
                held = "%d time%s, last %s" % (c, "" if c == 1 else "s", day(p["last_meeting"][n])) if c else "not held yet"
                ahead = p["next_meeting"].get(n)
                lines.append("- [[%s]]: %s%s" % (called[n], held, ", next %s" % time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(ahead)) if ahead else ""))
        if p["mails"]:
            lines.append("## Mail to you")
            lines += ["- %s: %s" % (day(at), link(subj.replace("[[", "").replace("]]", "")))
                      for at, subj in sorted(p["mails"], reverse=True)[:6]]
            for _, subj in p["mails"]:
                p["projects"] |= set(LINKED.findall(link(subj)))
        if p["dm"] or p["groups"]:
            lines.append("## Chat")
            if p["dm"]:
                lines.append("- Direct messages, last %s" % day(p["dm"]))
            if p["groups"]:
                lines.append("- In %s" % ", ".join("[[%s]]" % called[g] for g in sorted(p["groups"])))
        if p["projects"]:
            lines += ["## Projects", "- " + ", ".join("[[%s]]" % n for n in sorted(p["projects"]))]
        mind.write("people", p["name"], {"email": p["email"], "seen_in": sorted(p["from"])}, lines)

    notepad(mind, names, people)
    counts = collections.Counter(n.kind for n in mind.notes())
    summary = ", ".join("%d %s" % (c, k) for k, c in sorted(counts.items()))
    mind.log("rebuilt the map%s: %s" % (" (no Google)" if "--local" in argv else "", summary))
    print("[brain] %s in %s" % (summary, mind.root))


if __name__ == "__main__":
    main(sys.argv[1:])
