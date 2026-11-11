"""People from your Contacts and the company directory, on one card: "who is Rahul", "what's going on with Rahul",
"catch me up on Rahul". Who they are, the last email and chat from them, your next meeting together and the files
they shared lately.

Two people with that name and nothing lately settling it -> "Which Rahul?" with a button each. The one you pick is
remembered (pref people_aliases), so "Rahul" means that person everywhere: Chat, invites, mail (find_email in
skills/google_common.py). Someone who isn't in your contacts or directory ("who is Elon Musk") goes on to Wikipedia."""
import concurrent.futures
import re
import time

from skills import google_common
from skills.google_calendar import _event
from skills.google_common import GoogleSkill, cell, clock, day_word, first_name, parse_time, person, short, when
from system import google_account as google

NOT_NAMES = {"the", "a", "an", "my", "your", "our", "their", "this", "that", "it", "you", "me", "i", "he", "she",
             "they", "we", "everyone", "everybody", "jarvis", "claude", "there", "here", "up", "today", "next", "on",
             "what", "who", "him", "her", "them", "us", "going", "new", "happening"}
READ_MASK = "names,emailAddresses,organizations"
RECENT = 30 * 86400  # mail with someone this recent settles which of two namesakes you mean
PICKED = 600  # a "Which Rahul?" answered within this long is remembered
FILES = "files(id,name,mimeType,webViewLink,modifiedTime)"


class People(GoogleSkill):
    group = "people"
    does = "A person from your contacts on one card: their last email and chat, your next meeting together, " \
           "files they shared"
    phrases = ("who is <name> (someone in your contacts)", "what's going on with <name>", "catch me up on <name>")
    gives = "a card and a short spoken line; a choice of people when two share the name"
    permission = "auto"
    checks = "answers only for people in your contacts or company directory; anyone else goes on to Wikipedia"
    every = float("inf")  # nothing to watch

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self._asked = None  # (name said, when) of the last "Which Rahul?"
        self._loaded = False
        google_common.ALIASES.clear()

    def watch(self):
        """The names you picked, once memory is up (it isn't yet while the skills are built)."""
        if not self._loaded:
            self._loaded = True
            google_common.ALIASES.update(self.j.memory.pref("people_aliases") or {})

    def patterns(self):
        return [
            (r"(?:who(?:'s| is) |what'?s (?:going on|new|up|happening) with |catch me up (?:on|with|about) |"
             r"(?:show me )?(?:everything|anything) (?:new )?(?:from|with) )"
             r"(?!(?:the|a|an|my|your|our|their|this|that|it|you|me|him|her|them|us)\b)(?![a-z]+ing\b)"
             r"([a-z][a-z.@'_ -]{1,60}?)"
             r"(?: lately| these days| today| recently)?\??", self.card),
        ]

    def card(self, m):
        self.watch()
        name = m.group(1).strip(" ?.'")
        if not google.connected() or not looks_like_name(name):
            return False
        found = self.lookup(name)
        if not found:
            return False  # not someone you know: Wikipedia, then Claude
        if len(found) > 1:
            self.pick(name, found)
            return
        self.show(found[0])

    # --- who ---

    def lookup(self, name):
        """[{"name", "email", "title", "org"}] for `name`: an address, a remembered pick, or a Contacts / directory
        search. Namesakes are cut down to the one you've mailed lately when only one has."""
        if "@" in name:
            return [self._by_email(name)]
        said = name.lower()
        if said in google_common.ALIASES:
            return [self._by_email(google_common.ALIASES[said])]
        found = {}
        api = self.api("people", "v1")
        for search in (lambda: api.people().searchDirectoryPeople(
                query=name, readMask=READ_MASK, sources=["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"], pageSize=6),
                lambda: api.people().searchContacts(query=name, readMask=READ_MASK, pageSize=6)):
            try:
                got = google.run(search())
            except google.GoogleError:
                continue  # no directory (a personal account) or no contacts permission: the other one
            for p in got.get("people") or [r.get("person") or {} for r in got.get("results") or []]:
                who = _person(p)
                if who and _same_name(name, who["name"]):
                    found.setdefault(who["email"], who)
        people = list(found.values())
        if len(people) > 1:
            people = self._settle(people)
        return people

    def _by_email(self, email):
        return self._looked_up(email) or {"name": person("", email), "email": email.lower(), "title": "", "org": ""}

    def _looked_up(self, email):
        """Their directory or contact entry, for the name and job; None if neither knows the address."""
        try:
            got = google.run(self.api("people", "v1").people().searchDirectoryPeople(
                query=email, readMask=READ_MASK, sources=["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"], pageSize=1))
        except google.GoogleError:
            return None
        return next((w for w in map(_person, got.get("people") or []) if w and w["email"] == email.lower()), None)

    def _settle(self, people):
        """Namesakes with when you last mailed each other; just the one if only one is recent."""
        gm = self.api("gmail", "v1")
        for p in people:
            try:
                hit = google.run(gm.users().messages().list(userId="me", q="from:%s OR to:%s" % (p["email"], p["email"]),
                                                            maxResults=1)).get("messages")
                p["last"] = int(google.run(gm.users().messages().get(
                    userId="me", id=hit[0]["id"], format="minimal")).get("internalDate", 0)) / 1000 if hit else 0
            except google.GoogleError:
                p["last"] = 0
        people.sort(key=lambda p: -p["last"])
        recent = [p for p in people if p["last"] > time.time() - RECENT]
        return recent if len(recent) == 1 else people[:4]

    def pick(self, name, people):
        self._asked = (name.lower(), time.time())
        rows = ["%s | %s | %s" % (cell(p["name"]), cell(_job(p) or p["email"]), "mailed %s" % day_word(p["last"])
                                  if p.get("last") else "") for p in people]
        self.j.show({"id": "people", "title": "Which %s?" % name.title(),
                     "text": "```rows\n%s\n```\nNext time \"%s\" means the one you pick, unless you say the surname." % (
                         "\n".join(rows), name.title()),
                     "choices": [{"label": p["name"], "do": "catch me up on %s" % p["email"]} for p in people]})
        names = [p["name"] for p in people]
        self.say("Which %s: %s?" % (name.title(), ", ".join(names[:-1]) + " or " + names[-1]))

    # --- what's going on with them ---

    def show(self, p):
        if self._asked and time.time() - self._asked[1] < PICKED and self._asked[0] in p["name"].lower():
            google_common.ALIASES[self._asked[0]] = p["email"]  # "Rahul" is this Rahul from now on
            self.j.memory.set_pref("people_aliases", dict(google_common.ALIASES))
            self._asked = None
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            mail, chat, meet, files = [f.result() for f in [pool.submit(self._safely, fn, p["email"]) for fn in (
                self._last_mail, self._last_chat, self._next_meeting, self._their_files)]]
        rows = []
        if mail:
            rows.append("✉ | Last email | [%s](https://mail.google.com/mail/u/0/#all/%s) | %s" % (
                cell(mail["subject"]), mail["thread"], _ago(mail["at"])))
        if chat:
            rows.append("# | Last chat | %s | %s" % (cell(short(chat["text"], 16)), _ago(chat["at"])))
        if meet:
            rows.append("◷ | Next meeting together | %s%s | %s" % (cell(meet["title"]), " · [Join](%s)" % meet["link"]
                                                                  if meet["link"] else "", _when(meet["start"])))
        for f in files[:2]:
            rows.append("file:%s | [%s](%s) | Their file | %s" % (_badge(f), cell(f["name"]), f.get("webViewLink", ""),
                                                                 _ago(parse_time(f["modifiedTime"]))))
        head = "%s | %s | %s" % (cell(p["name"]), cell(_job(p) or "In your contacts"), p["email"])
        choices = []
        if mail:
            choices.append({"label": "Read the email", "do": "read the latest email from %s" % p["email"]})
        if chat:
            choices.append({"label": "Read the chat", "do": "what did %s say on chat" % p["name"]})
        if meet and meet["link"]:
            choices.append({"label": "Meeting prep", "do": "prep me for my next meeting"})
        self.j.show({"id": "people", "title": p["name"], "text": "```rows\n%s\n```\n%s" % (head, "```rows\n%s\n```" % (
            "\n".join(rows)) if rows else "Nothing from them lately: no email, chat or meeting in the last weeks."),
            "choices": choices})
        job = p.get("title") or p.get("org")
        said = ["%s%s." % (p["name"], ", %s" % job.lower() if job else "")]
        if mail:
            said.append("Last email %s: %s." % (day_word(mail["at"]), short(mail["subject"], 10)))
        elif chat:
            said.append("Last chat %s: %s" % (day_word(chat["at"]), short(chat["text"], 12)))
        if meet:
            said.append("Next meeting together: %s, %s." % (meet["title"], when(meet["start"])))
        if not (mail or chat or meet):
            said.append("Nothing from %s lately." % first_name(p["name"]))
        self.say(" ".join(said))

    def _safely(self, fn, email):
        try:
            return fn(email)
        except google.GoogleError:
            return [] if fn == self._their_files else None

    def _last_mail(self, email):
        gm = self.api("gmail", "v1")
        hit = google.run(gm.users().messages().list(userId="me", q="from:%s" % email, maxResults=1)).get("messages")
        return self.j.skills.gmail._meta(gm, hit[0]["id"]) if hit else None

    def _last_chat(self, email):
        chats = self.j.skills.google_chat
        api = self.api("chat", "v1")
        dm = chats._dm(api, email)
        if not dm:
            return None
        msgs = google.run(api.spaces().messages().list(parent=dm["name"], pageSize=5,
                                                       orderBy="createTime desc")).get("messages") or []
        x = next((x for x in msgs if (x.get("sender") or {}).get("name") != chats.me()), None)
        return {"text": x.get("text") or "(an attachment)", "at": parse_time(x["createTime"])} if x else None

    def _next_meeting(self, email):
        now = time.time()
        got = google.run(self.api("calendar", "v3").events().list(
            calendarId="primary", q=email, timeMin=google_common.rfc(now), timeMax=google_common.rfc(now + 14 * 86400),
            singleEvents=True, orderBy="startTime", maxResults=5))
        for raw in got.get("items") or []:
            if any((a.get("email") or "").lower() == email for a in raw.get("attendees") or []):
                e = _event(raw)
                if not e["all_day"] and e["end"] > now:
                    return e
        return None

    def _their_files(self, email):
        return google.run(self.api("drive", "v3").files().list(
            q="'%s' in owners and trashed=false" % email.replace("'", ""), orderBy="modifiedTime desc", pageSize=3,
            fields=FILES)).get("files") or []


def looks_like_name(name):
    """"Rahul", "rahul sharma", "priya@biztel.ai": yes. "the president of india", "my computer", "that": no."""
    if "@" in name:
        return bool(re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", name))
    words = name.lower().split()
    return 1 <= len(words) <= 3 and words[0] not in NOT_NAMES and not any(w in NOT_NAMES - {"up"} for w in words) \
        and all(re.fullmatch(r"[a-z][a-z'.-]*", w) for w in words)


def _person(p):
    """A People API person -> {"name", "email", "title", "org"}, or None without an address."""
    email = next((e.get("value") for e in p.get("emailAddresses") or [] if e.get("value")), None)
    if not email:
        return None
    name = next((n.get("displayName") for n in p.get("names") or [] if n.get("displayName")), None)
    org = next(iter(p.get("organizations") or []), {})
    return {"name": person(name or "", email), "email": email.lower(), "title": org.get("title") or "",
            "org": org.get("name") or ""}


def _same_name(said, name):
    """Every word said starts a word of the name: "rahul" ~ "Rahul Sharma", "rahul s" ~ "Rahul Sharma"."""
    words = name.lower().split()
    return all(any(w.startswith(s) for w in words) for s in said.lower().split())


def _job(p):
    return " · ".join(x for x in (p.get("title"), p.get("org")) if x)


def _ago(ts):
    return clock(ts) if day_word(ts) == "today" else day_word(ts)


def _when(ts):
    return "%s %s" % (day_word(ts), clock(ts))


def _badge(f):
    mime = f.get("mimeType", "")
    return next((k for w, k in (("document", "doc"), ("spreadsheet", "sheet"), ("presentation", "slides"),
                                ("pdf", "pdf"), ("folder", "folder")) if w in mime), "file")
