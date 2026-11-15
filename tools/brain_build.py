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
CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "brain.yaml")
STUDY_DB = os.environ.get("STUDY_BUDDY_DB") or os.path.expanduser("~/.local/share/study-buddy/study.db")
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
HOUR = 3.6e6
TOGETHER = 5 * HOUR  # an app links to a project only after this long used together (Claude Code + Jarvis: 21.7h)
WORK_AND_OWN = ("Biztel work", "Personal")  # the two areas under the top Projects node
OUTCOMES = os.path.expanduser("~/.local/share/jarvis/outcomes.jsonl")
LOCAL = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3}){3}|localhost)(?::\d+)?$")  # 127.0.0.1: test servers
# an installed app's name -> what the day tracker calls it, so both land on one note
SHORT = {"Firefox Web Browser": "Firefox", "Google Chrome": "Google Chrome", "Visual Studio Code": "VS Code",
         "GNOME System Monitor": "System Monitor"}


def jarvis_opened():
    """{key: (app name, times)} for apps you opened by asking JARVIS ("open cheese"), from its outcome log."""
    from system.apps import Apps
    apps, out = Apps(), {}
    try:
        with open(OUTCOMES) as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return {}
    for r in rows:
        m = re.match(r"^open (?:the )?(.+?)(?: app)?$", r.get("phrase") or "", re.I)
        app = apps.find(m.group(1)) if m else None
        if app:
            short = SHORT.get(app["name"], app["name"])
            name, n = out.get(key(short), (short, 0))
            out[key(short)] = (name, n + 1)
    return out


def load_yaml(path):
    import yaml
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return {}


def spaced(name):
    """A chat group's name with its punctuation as spaces, so its words link: "Annotation-Avtec(Hub,sleeve)"."""
    return re.sub(r"[-_(),.]+", " ", name)



def together_with(state):
    """{app: {project folder: ms}}: time an app was in use in the same day-tracker session as a VS Code folder
    (Claude Code alongside J.A.R.V.I.S, Firefox alongside Plant Intelligence System)."""
    out = collections.defaultdict(lambda: collections.defaultdict(float))
    for e in state.get("autoEntries") or []:
        parts = e.get("parts") or {}
        folders = {k[len("VS Code · "):]: ms for k, ms in parts.items() if k.startswith("VS Code · ")}
        for app, ms in parts.items():
            if app and not app.startswith("VS Code"):
                for f, fms in folders.items():
                    out[app][f] += min(ms, fms)
    return out


def mins(secs):
    secs = int(secs or 0)
    if not secs:
        return "0 min"
    return "%dh %02dm" % (secs // 3600, secs % 3600 // 60) if secs >= 3600 else "%d min" % max(1, round(secs / 60))


def clock_at(secs):
    secs = int(secs or 0)
    return "%d:%02d:%02d" % (secs // 3600, secs % 3600 // 60, secs % 60) if secs >= 3600 else \
        "%d:%02d" % (secs // 60, secs % 60)


def learning(mind):
    """Study Buddy (~/Projects/study-budd) as brain/learning/: Learning.md, a folder and note per group, a note per
    playlist or problem set listing every video or problem with its link and how far you got, and a note of its own
    for a video you wrote notes or flashcards on (each note's time opens the video there). Read-only from its
    database, so it works when the app is closed."""
    import sqlite3
    if not os.path.exists(STUDY_DB):
        print("[brain] no Study Buddy database: no Learning this time")
        return
    try:
        db = sqlite3.connect("file:%s?mode=ro" % STUDY_DB, uri=True)
        db.row_factory = sqlite3.Row
        q = lambda sql, *a: db.execute(sql, a).fetchall()
        groups = q("SELECT * FROM groups ORDER BY position")
        sources = q("SELECT * FROM sources ORDER BY group_id, position")
        items = q("SELECT i.*, p.status, p.watched_s, p.completed_at, p.updated_at FROM items i "
                  "LEFT JOIN progress p ON p.item_id = i.id ORDER BY i.source_id, i.position")
        notes = q("SELECT * FROM notes ORDER BY item_id, at_s")
        cards = q("SELECT * FROM cards WHERE pending = 0 ORDER BY item_id, at_s")
        week = q("SELECT SUM(seconds) FROM activity WHERE day >= date('now', '-6 days')")[0][0] or 0
        days = [r[0] for r in q("SELECT DISTINCT day FROM activity ORDER BY day DESC")]
    except sqlite3.Error as e:
        print("[brain] Study Buddy unreadable: %s" % e)
        return
    by_source = collections.defaultdict(list)
    for it in items:
        by_source[it["source_id"]].append(it)
    noted = collections.defaultdict(list)
    for n in notes:
        noted[n["item_id"]].append(n)
    carded = collections.defaultdict(list)
    for c in cards:
        if c["item_id"]:
            carded[c["item_id"]].append(c)

    def url(it, at=None):
        if it["kind"] == "video" and it["external_id"]:
            return "https://www.youtube.com/watch?v=%s%s" % (it["external_id"], "&t=%ds" % at if at else "")
        return it["url"] or ""

    def counts(its):
        live = [i for i in its if not i["skipped"]]
        return sum(i["status"] == "done" for i in live), len(live)

    keep, totals, picking = set(), [0, 0], None
    for g in groups:
        g_items = [it for s in sources if s["group_id"] == g["id"] for it in by_source[s["id"]]]
        done, total = counts(g_items)
        totals[0] += done
        totals[1] += total
        watched = sum(i["watched_s"] or 0 for i in g_items)
        started = [i for i in g_items if i["status"] == "in_progress"]
        nxt = started[0] if started else next((i for i in g_items if not i["skipped"] and i["status"] != "done"), None)
        if started and (not picking or (started[0]["updated_at"] or "") > (picking[1]["updated_at"] or "")):
            picking = (g["name"], started[0])
        lines = ["- Part of [[Learning]]",
                 "- %d of %d done (%d%%), %s watched" % (done, total, 100 * done // total if total else 0, mins(watched))]
        if g["on_today"]:
            lines.append("- On your Today page%s" % (", goal %d min a day" % g["goal_min"] if g["goal_min"] else ""))
        if nxt:
            lines.append("- Next: [%s](%s)" % (nxt["title"].replace("]", ")"), url(nxt)))
        keep.add(mind.write("learning", g["name"], {"type": "study group"}, lines, folder=g["name"]))
        for s in (s for s in sources if s["group_id"] == g["id"]):
            its = by_source[s["id"]]
            done, total = counts(its)
            kind = "problem set" if s["kind"] == "problem_set" else "playlist"
            lines = ["- Part of [[%s]]" % title(g["name"]),
                     "- %s%s, [open it](%s)" % (kind.capitalize(), " by %s" % s["channel"] if s["channel"] and
                                                kind == "playlist" else "", s["url"]),
                     "- %d of %d %s" % (done, total, "solved" if kind == "problem set" else "done"), "",
                     "✓ done · ◐ started · ○ not yet · ✗ skipped", ""]
            for n, it in enumerate(its, 1):
                mark = "✗" if it["skipped"] else {"done": "✓", "in_progress": "◐"}.get(it["status"], "○")
                name = it["title"]
                if noted[it["id"]] or carded[it["id"]]:
                    name = "[[%s]]" % title(it["title"])
                    lines_v = ["- In [[%s]] · [watch it](%s)" % (title(s["title"]), url(it)), "", "## Notes"]
                    lines_v += ["- [%s](%s) %s" % (clock_at(x["at_s"]), url(it, int(x["at_s"] or 0)), x["body"])
                                for x in noted[it["id"]]] or ["- none yet"]
                    if carded[it["id"]]:
                        lines_v += ["", "## Flashcards"] + ["- Q: %s · A: %s" % (c["front"], c["back"])
                                                            for c in carded[it["id"]]]
                    keep.add(mind.write("learning", it["title"], {"type": "video"}, lines_v,
                                        folder="%s/%s" % (g["name"], s["title"])))
                extra = ""
                if it["kind"] == "video":
                    extra = " (%s%s)" % (mins(it["duration_s"]), ", %s watched" % mins(it["watched_s"])
                                         if it["status"] == "in_progress" else "")
                else:
                    meta = json.loads(it["meta"] or "{}")
                    extra = " (%s)" % meta["difficulty"] if meta.get("difficulty") else ""
                link = url(it)
                lines.append("%s %d. %s%s%s" % (mark, n, name, extra, " · [link](%s)" % link if link else ""))
                lines.append("")  # each item its own line in Obsidian's reading view
            keep.add(mind.write("learning", s["title"], {"type": kind}, lines, folder=g["name"]))
    lines = ["- %d of %d videos and problems done across %d groups" % (totals[0], totals[1], len(groups))]
    if picking:
        lines.append("- Picking up %s, in %s" % (picking[1]["title"], picking[0]))
    lines.append("- Studied %s in the last 7 days%s" % (mins(week), ", last on %s" % days[0] if days else ""))
    keep.add(mind.write("learning", "Learning", {"type": "area", "about": "What you're studying in Study Buddy"},
                        lines))
    for n in mind.notes():  # a group, playlist or video gone from Study Buddy goes from here too
        if n.kind == "learning" and n.path not in keep:
            mind.forget(n.path)
    print("[brain] learning: %d groups, %d playlists and problem sets" % (len(groups), len(sources)))


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
    cfg = load_yaml(CONFIG)
    families = cfg.get("families") or {}
    family_of = {f: fam for fam, c in families.items() for f in c.get("projects") or []}

    def chain(fam):
        """"Avtec" -> "Biztel work/AI Supervisor/Avtec": the folders a family's notes sit in."""
        path, seen = [fam], {fam}
        while path[0] in families and families[path[0]].get("in") and families[path[0]]["in"] not in seen:
            path.insert(0, families[path[0]]["in"])
            seen.add(path[0])
        return "/".join(path)
    link = linker(dict({n: [w for w in ws if w.lower() not in LOOSE] for n, ws in names.items() if n.lower() not in LOOSE},
                       **{fam: c.get("words") or [] for fam, c in families.items()}))
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

    by_folder = {p.name: note_name(p) for p in projects}
    folder_hours = {label[len("VS Code · "):]: ms for label, (ms, _, _) in used.items() if label.startswith("VS Code · ")}
    together = together_with(state)
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
        if p.name in family_of:
            lines.append("- Part of [[%s]]" % family_of[p.name])
        lines += [""] + (["## Open"] + pending[name] if pending.get(name) else [])
        mind.write("projects", name, {"folder": p.path, "aliases": sorted(set(names[name]) - {name}),
                                      "about": p.summary or p.title}, lines,
                   folder=chain(family_of[p.name]) if p.name in family_of else "")

    opened = jarvis_opened()
    for label, (total, recent, last) in used.items():
        if not label or label.startswith("VS Code ·") or total < 0.5 * 3.6e6 or LOCAL.match(label):
            if LOCAL.match(label or "-"):
                gone = mind.note(label)
                if gone and gone.kind == "apps":
                    mind.forget(gone.path)  # your own computer's test servers, not an app or a site
            continue
        name = label.split(" · ")[0] if " · " in label and "YouTube" in label else label
        lines = ["- %sh in all, %sh in the last 7 days, last on %s" % (hours(total), hours(recent), day(last / 1000)),
                 "- One of your [[Apps]]"] + (["- What you study in: [[Learning]]"] if "study account" in label else [])
        if label == "VS Code":  # every project is edited in it: hours in words, no links
            on = sorted(((ms, by_folder[f]) for f, ms in folder_hours.items() if f in by_folder and ms >= HOUR),
                        reverse=True)
            if on:
                lines.append("- Projects open in it: %s" % ", ".join("%s (%sh)" % (n, hours(ms)) for ms, n in on[:8]))
        else:
            on = sorted(((ms, by_folder[f]) for f, ms in together.get(label, {}).items()
                         if f in by_folder and ms >= TOGETHER), reverse=True)
            if on:
                lines.append("- Used while working on %s" % ", ".join(
                    "[[%s]] (%sh)" % (n, hours(ms)) for ms, n in on[:8]))
        times = opened.pop(key(label), None)
        if times:
            lines.append("- Opened through JARVIS %d time%s" % (times[1], "" if times[1] == 1 else "s"))
        mind.write("apps", label, {"kind": "site" if "." in name else "app"}, lines)
    for app, n in opened.values():  # opened through JARVIS but not in the day tracker
        mind.write("apps", app, {"kind": "app"}, ["- Opened through JARVIS %d time%s" % (n, "" if n == 1 else "s"),
                                                 "- One of your [[Apps]]"])

    for fam, c in families.items():
        members = [by_folder[f] for f in c.get("projects") or [] if f in by_folder]
        lines = ["- Part of [[%s]]" % c["in"]] if c.get("in") else []
        lines.append("- %d project%s link here" % (len(members), "" if len(members) == 1 else "s"))
        mind.write("projects", fam, {"type": "family", "aliases": c.get("words") or []}, lines, folder=chain(fam))
    for area, about in (cfg.get("areas") or {}).items():
        mind.write("projects", area, {"type": "area", "about": about},
                   [about] + (["- Part of [[Projects]]"] if area in WORK_AND_OWN else []), folder=area)
    mind.write("projects", "Projects", {"type": "area"}, ["All your projects: Biztel work and Personal."])
    mind.write("people", "People", {"type": "area"},
               ["Everyone JARVIS found in your Calendar, Gmail and Chat (your work account)."])
    mind.write("apps", "Apps", {"type": "area"},
               ["Apps and sites you used for half an hour or more, and ones you opened through JARVIS."])
    for n in mind.notes():  # the old flat areas/ and families/ folders (2a10a5c): their notes now sit with projects
        if n.kind in ("areas", "families"):
            mind.forget(n.path)

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
        about = sorted(set(LINKED.findall(link(spaced(name)))))
        if about:
            lines.append("- About %s" % ", ".join("[[%s]]" % n for n in about))
        talked = sorted(set(g["projects"]) - set(about))
        if talked:
            lines.append("- Talked about %s" % ", ".join("[[%s]]" % n for n in talked))
        mind.write("groups", called[name], {}, lines)

    for p in people.all():
        lines = []
        if p["meetings"]:
            lines.append("## Meetings")
            for n, c in p["meetings"].most_common(8):
                held = "%d time%s, last %s" % (c, "" if c == 1 else "s", day(p["last_meeting"][n])) if c else "not held yet"
                ahead = p["next_meeting"].get(n)
                lines.append("- %s: %s%s" % (called[n], held, ", next %s" % time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(ahead)) if ahead else ""))
        if p["mails"]:
            lines.append("## Mail to you")
            lines += ["- %s: %s" % (day(at), subj.replace("[[", "").replace("]]", ""))  # words only: people don't link to projects
                      for at, subj in sorted(p["mails"], reverse=True)[:6]]
            for _, subj in p["mails"]:
                p["projects"] |= set(LINKED.findall(link(subj)))
        if p["dm"] or p["groups"]:
            lines.append("## Chat")
            if p["dm"]:
                lines.append("- Direct messages, last %s" % day(p["dm"]))
            if p["groups"]:
                lines.append("- In %s" % ", ".join(called[g] for g in sorted(p["groups"])))
        if p["projects"]:
            # named in words, not linked: a person links only to People, chat groups and meetings (your pick)
            lines += ["## Projects they wrote about", "- " + ", ".join(sorted(p["projects"]))]
        lines.insert(0, "- One of the [[People]]")
        mind.write("people", p["name"], {"email": p["email"], "seen_in": sorted(p["from"])}, lines)

    notepad(mind, names, people)
    learning(mind)
    counts = collections.Counter(n.kind for n in mind.notes())
    summary = ", ".join("%d %s" % (c, k) for k, c in sorted(counts.items()))
    mind.log("rebuilt the map%s: %s" % (" (no Google)" if "--local" in argv else "", summary))
    print("[brain] %s in %s" % (summary, mind.root))


if __name__ == "__main__":
    main(sys.argv[1:])
