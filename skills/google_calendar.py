"""Your Google Calendar (the work account, system/google_account.py).

Asked: "do we have meetings today", "what's on my calendar tomorrow", "when's my next meeting", "am I free at 3",
"when am I free tomorrow afternoon", "join my meeting", "prep me for my next meeting"; "schedule a meeting with
Rahul tomorrow at 3", "accept the invite" / "decline it" (both asked about first). "Connect Google" signs in.

By itself, once a minute from jarvis.watch: a heads-up 5 minutes before each meeting (settings google
heads_up_minutes) with a card of what's linked to it (Join, the event's docs, the last email and chat from the people
in it); someone inviting you, moving a meeting or cancelling one; and meeting mode: while a call you're in is on,
focus mode holds heads-ups and banners and the music is paused ("turn off meeting mode").

Without Google connected, "what's on my calendar" is the Day Tracker's (skills/briefing.py)."""
import datetime
import re
import threading
import time
import uuid

from skills.google_common import (DAYS, GoogleSkill, cell, clock, day_word, find_email, first_name, header, parse_time,
                                  person, rfc, sender, short, when)
from skills.helpers import and_list
from skills.todos import tidy
from system import google_account as google

WATCH_DAYS = 14  # invites, moves and cancellations are noticed this far ahead
NOT_MEETINGS = ("workingLocation", "outOfOffice", "focusTime", "birthday")
LINK = re.compile(r"https://(?:[\w-]+\.)*(?:zoom\.us|teams\.microsoft\.com|teams\.live\.com|meet\.google\.com|"
                  r"webex\.com)/[^\s<>\"')]+")
DAY = r"(today|tonight|tomorrow|this week|next week|(?:on |next |this )?(?:%s))" % "|".join(DAYS)
MEETINGS = r"(?:meetings?|calls?|events?|appointments?)"
PARTS = {"morning": (9, 12), "afternoon": (12, 17), "evening": (17, 21)}
CLOCK = r"\d{1,2}(?:[:.]\d\d)?(?: ?[ap]\.? ?m\.?| o'?clock)?"


class Calendar(GoogleSkill):
    group = "calendar"
    does = ("Your Google Calendar: meetings on any day, the next one, free time, joining the call, scheduling and "
            "answering invites; heads-ups before meetings and about new, moved or cancelled ones; meeting mode")
    phrases = ("do we have meetings today", "what's on my calendar tomorrow", "when's my next meeting",
               "am I free at 3", "when am I free tomorrow afternoon", "join my meeting",
               "prep me for my next meeting (the docs, last email and chat with the people in it)",
               "schedule a meeting with <name> tomorrow at 3 (about <topic>, for 30 minutes)",
               "accept the invite", "decline the invite", "turn off meeting mode", "connect Google (sign in)")
    gives = "speech and a card; heads-ups by themselves; joining opens the call in the browser"
    permission = "confirm"
    asks = "before scheduling a meeting or answering an invite (both email the other people)"
    checks = "reads Google Calendar itself; says when Google isn't connected, is unreachable or lacks a permission"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self._snap = None  # id -> event at the last poll (None until the first one)
        self._since = 0.0  # when watching began: older events aren't new invites
        self._warned = set()  # (id, start) of meetings already given their heads-up
        self._in_call = None  # the id of the meeting meeting mode is on for
        self.last_invite = None  # (event, when said): what "accept the invite" means
        self._signing_in = False

    def patterns(self):
        return [
            (r"(?:connect|link|reconnect|sign (?:in|into)|log ?in(?: to)?) (?:to )?(?:my )?(?:google|gmail|google "
             r"calendar)(?: account)?(?: again)?", self.connect),
            (r"(?:schedule|set up|book|arrange|organi[sz]e) (?:a |an )?(?:new )?(?:quick |short )?"
             r"(?:meeting|call|sync|catch[- ]?up|one on one|1:1)(?: .*)?|(?:create|set|put|add) (?:a |an )(?:new )?"
             r"(?:quick |short )?(?:meeting|call|sync|catch[- ]?up|one on one)(?: .*)?|book (?:some )?time with .+",
             self.schedule),
            (r"(accept|decline|reject|say yes to|say no to|tentatively accept|maybe) (?:(?:the |that |this |my )?"
             r"(?:last |latest |new )?(?:invite|invitation|meeting(?: invite)?)(?: from (.+))?|it|that(?: one)?)",
             self.respond),
            (r"join (?:my |the |this |our )?(?:next |current )?(?:meeting|call|meet|zoom|video call)(?: call| link)?|"
             r"open (?:the |my )?(?:meeting|call|meet|zoom) link|join the (?:google )?meet", self.join),
            (r"(?:turn|switch) (on|off) meeting mode|meeting mode (on|off)|(stop|start) (?:going quiet|meeting mode)"
             r"(?: (?:in|during) (?:meetings|calls))?", self.meeting_mode),
            (r"(?:prep|prepare|brief) me (?:for|on) (?:my |the )?(?:next )?(?:meeting|call)|what'?s my next "
             r"(?:meeting|call) about", self.prep),
            (r"when(?:'s| is) my next %s|what(?:'s| is) (?:my )?next (?:%s|on my calendar)|(?:do i have|is there|have "
             r"i got) (?:a |any )?%s (?:now|right now|soon|next|coming up)|(?:what|which) %s (?:am i in|is on) (?:right )?"
             r"now|what'?s my next %s" % (MEETINGS, MEETINGS, MEETINGS, MEETINGS, MEETINGS), self.next_meeting),
            (r"am i (free|busy|available) (?:at|around|by) (.+?)(?: (today|tomorrow|on \w+day))?", self.free_at),
            (r"(?:do i have|have i got|is there) anything (?:on )?(?:at|around) (.+?)(?: (today|tomorrow|on \w+day))?|"
             r"is (?:my calendar |my schedule )?(?:free|open|clear) (?:at|around) (.+?)(?: (today|tomorrow|on \w+day))?|"
             r"is (%s)(?: (today|tomorrow|on \w+day))? (?:free|open|ok)|can i do (%s)(?: (today|tomorrow|on \w+day))?"
             % (CLOCK, CLOCK), self.free_at_other),
            (r"when am i (?:free|available)(.*)|(?:find|do i have|is there|have i got|any) (?:some |any )?(?:free "
             r"time|time free|free slots?|gaps?)(.*)|find me (?:some )?(?:free )?time(.*)|when (?:do|will|would) i have "
             r"(?:some )?(?:free )?time(.*)|when(?:'s| is) my next free (?:slot|time|gap)(.*)|where are the gaps in my "
             r"(?:day|calendar|schedule)(.*)|when can i fit (?:something|a meeting|it|this) in(.*)", self.free_time),
            (r"(?:do|have) (?:we|i) (?:have|got) (?:any )?%s(?: (?:on|for))?(?: %s)?|(?:any|how many|what|which) "
             r"%s(?: (?:do|have) (?:i|we)(?: (?:got|have))?)?(?: (?:on|for))?(?: %s)?|(?:my |the )?%s %s|"
             r"what(?:'s| is) on (?:my |the )?(?:google )?calendar(?: (?:on|for))?(?: %s)?|(?:show|check|read)(?: me)? "
             r"(?:my |the )?(?:google )?calendar(?: (?:on|for))?(?: %s)?|what does (?:my )?%s look like"
             % (MEETINGS, DAY, MEETINGS, DAY, MEETINGS, DAY, DAY, DAY, DAY), self.day),
        ]

    # --- reading the calendar ---

    def fetch(self, start, end, deleted=False):
        cal = self.api("calendar", "v3")
        got = google.run(cal.events().list(
            calendarId="primary", timeMin=rfc(start), timeMax=rfc(end), singleEvents=True, orderBy="startTime",
            showDeleted=deleted, maxResults=250))
        # shortcut: one page of 250 events; a busier two weeks would need nextPageToken
        return [_event(r) for r in got.get("items") or []]

    def meetings(self, start, end):
        """The events in [start, end) you're going to (not declined, cancelled or an out-of-office block)."""
        return [e for e in self.fetch(start, end) if attending(e)]

    def day(self, m):
        said = next((g for g in m.groups() if g), None)
        if not google.connected():
            if re.search(r"calendar|schedule", m.group(0), re.I):
                return False  # the Day Tracker's reminders (skills/briefing.py)
            google.api("calendar", "v3")  # says Google isn't connected
        start, end, label = span(said)
        events = self.meetings(start, end)
        now = time.time()
        timed = [e for e in events if not e["all_day"]]
        all_day = [e for e in events if e["all_day"]]
        single = end - start <= 86400 + 3600
        lines = []
        if single:
            reminders = self._reminders(start, end)
            lo, hi = self.cfg("workday", [9, 18])
            busy = [(e["start"], e["end"]) for e in timed if not e["free"]]
            lines = _timeline(timed, _gaps(busy, max(start + lo * 3600, now), start + hi * 3600), reminders) + \
                _rows(events, now)
            if reminders:
                lines += ["", "**Reminders**"] + ["- %s %s" % (clock(t), text) for t, text in reminders]
        else:
            for day in sorted({day_word(e["start"]) for e in events}, key=lambda d: next(
                    e["start"] for e in events if day_word(e["start"]) == d)):
                lines += ["", "**%s**" % day.capitalize()] + _rows([e for e in events if day_word(e["start"]) == day],
                                                                   now)
        title = "%s: %s" % (label.capitalize(), time.strftime("%A %-d %B", time.localtime(start))) if single else \
            label.capitalize()
        if events:
            self.j.show({"id": "calendar", "title": title, "text": "\n".join(lines).strip()})
        self.say(_day_line(timed, all_day, label, now, single))

    def _reminders(self, start, end):
        """[(when, text)] of the Day Tracker's reminders in [start, end); [] when it isn't running."""
        try:
            state = self.j.skills.briefing.hub._state()
        except Exception:
            return []
        return sorted((r["at"] / 1000, r["text"]) for r in state.get("reminders") or []
                      if start * 1000 <= r["at"] < end * 1000)

    def next_meeting(self, m):
        now = time.time()
        events = [e for e in self.meetings(now - 12 * 3600, now + 7 * 86400) if not e["all_day"] and e["end"] > now]
        if not events:
            self.say("Nothing on your calendar for the next week.")
            return
        on = [e for e in events if e["start"] <= now]
        upcoming = [e for e in events if e["start"] > now]
        said = ["You're in %s now, until %s." % (e["title"], clock(e["end"])) for e in on[:1]]
        if upcoming:
            n = upcoming[0]
            said.append("%s %s, %s, %s%s." % ("After that it's" if on else "Your next meeting is", n["title"],
                                              when(n["start"]), _in(n["start"] - now),
                                              ", with %s" % _people(n) if n["with"] else ""))
        self.say(" ".join(said))
        if upcoming:
            self.j.show(self.prep_card(upcoming[0]))  # (after speaking: its email and chat take a moment)

    def free_at(self, m):
        return self._free(m.group(1).lower(), m.group(2), m.group(3))

    def free_at_other(self, m):
        """"do I have anything at 3", "is 3 o'clock free", "can I do 3 pm tomorrow", "is my calendar free at 4"."""
        g = m.groups()
        at, day = next((g[i], g[i + 1]) for i in range(0, len(g), 2) if g[i])
        return self._free("free", at, day)

    def _free(self, busy_word, at, day):
        at = re.sub(r"\s*o'?clock", "", at, flags=re.I)
        from skills.todos import parse_when
        ts = parse_when(("%s at %s" % (day, at)) if day in ("today", "tomorrow") else "at " + at)
        if ts is None:
            return False  # "am I free at the weekend": not a clock time
        if not re.search(r"[ap]\.? ?m\b", at, re.I) and 1 <= time.localtime(ts).tm_hour <= 7:
            ts += 12 * 3600  # "at 3" about meetings is the afternoon
        if day and day not in ("today", "tomorrow"):
            ts = _on_day(day, ts)
        events = [e for e in self.meetings(ts - 86400, ts + 86400) if not e["all_day"] and not e["free"]]
        clash = [e for e in events if e["start"] <= ts < e["end"]]
        day0 = time.mktime(time.localtime(ts)[:3] + (0, 0, 0, 0, 0, -1))
        lo, hi = self.cfg("workday", [9, 18])
        today = [e for e in events if day0 <= e["start"] < day0 + 86400]
        picture = _timeline(today, _gaps([(e["start"], e["end"]) for e in today], day0 + min(lo, time.localtime(
            ts).tm_hour) * 3600, day0 + max(hi, time.localtime(ts).tm_hour + 1) * 3600))
        if clash:
            e = clash[0]
            self.j.show({"id": "calendar", "title": "Free %s?" % when(ts), "text": "\n".join(
                ["{warn} **No: %s, %s to %s.**" % (e["title"], clock(e["start"]), clock(e["end"]))] + picture)})
            self.say("No, %s you have %s, from %s to %s." % (when(ts), e["title"], clock(e["start"]), clock(e["end"])))
            return
        after = [e for e in events if ts < e["start"] <= ts + 3 * 3600]
        self.j.show({"id": "calendar", "title": "Free %s?" % when(ts), "text": "\n".join(
            ["{ok} **Yes, you're free at %s.**%s" % (clock(ts), " Next: %s at %s." % (
                after[0]["title"], clock(after[0]["start"])) if after else "")] + picture)})
        self.say("%s, you're free %s.%s" % ("No" if busy_word == "busy" else "Yes", when(ts), (
            " Next after that is %s at %s." % (after[0]["title"], clock(after[0]["start"]))) if after else ""))

    def free_time(self, m):
        said = (next((g for g in m.groups() if g), "") or "").lower()
        day = re.search(r"today|tonight|tomorrow|%s" % "|".join(DAYS), said)
        part = re.search(r"morning|afternoon|evening", said)
        start, end, label = span(day.group(0) if day else "today")
        lo, hi = PARTS[part.group(0)] if part else tuple(self.cfg("workday", [9, 18]))
        start, end = start + lo * 3600, start + hi * 3600
        now = time.time()
        if end <= now:
            self.say("That's already over.")
            return
        start = max(start, now - now % 900 + 900)  # from the next quarter hour
        booked = [e for e in self.meetings(start, end) if not e["all_day"] and not e["free"]]
        gaps = _gaps([(e["start"], e["end"]) for e in booked], start, end)
        where = "%s%s" % (label, " " + part.group(0) if part else "")
        self.j.show({"id": "calendar", "title": "Free %s" % where, "text": "\n".join(_timeline(booked, gaps) + [
            "- {ok} %s – %s · %s" % (clock(a), clock(b), _length(b - a)) for a, b in gaps])})
        if not gaps:
            self.say("You're booked solid %s." % where)
        elif gaps == [(start, end)]:
            self.say("You're free all %s." % where if part else "Nothing on %s from %s to %s." % (
                where, clock(start), clock(end)))
        else:
            self.say("%s you're free %s." % (where.capitalize(), and_list("%s to %s" % (clock(a), clock(b))
                                                                        for a, b in gaps[:4])))

    def join(self, m):
        now = time.time()
        events = [e for e in self.meetings(now - 12 * 3600, now + 86400) if not e["all_day"] and e["end"] > now]
        live = sorted((e for e in events if e["link"] and e["start"] - 600 <= now), key=lambda e: -e["start"])
        if not live:
            later = next((e for e in events if e["link"]), None)
            self.say("Your next call is %s %s, %s. Say join my meeting nearer the time." % (
                later["title"], when(later["start"]), _in(later["start"] - now)) if later else
                "There's no meeting with a call link coming up today.")
            return
        e = live[0]
        self.open_url(e["link"], reply="Joining %s." % e["title"])
        self._start_meeting_mode(e)

    def prep(self, m):
        now = time.time()
        nxt = next((e for e in self.meetings(now - 600, now + 86400) if not e["all_day"]), None)
        if not nxt:
            self.say("No meetings in the next day.")
            return
        card = self.prep_card(nxt)
        self.j.show(card)
        found = [w for w, k in (("the attached docs", "**Attached**"), ("their last email", "**Last email"),
                                ("your last chat", "**Last chat")) if k in card["text"]]
        self.say("%s %s%s.%s" % (nxt["title"], when(nxt["start"]), ", with %s" % _people(nxt) if nxt["with"] else "",
                                 " I've put %s on the card." % and_list(found) if found else ""))

    def prep_card(self, e, heading=None):
        """A meeting's card: time, people, Join, its docs, the last email and chat from the people in it."""
        rows = []
        left = e["start"] - time.time()
        if 0 < left <= 900:  # about to start: a ring that empties until it does
            rows += ["```countdown", "end: %d" % e["start"], "total: %d" % max(60, left), "label: until it starts",
                     "```"]
        rows.append("**%s – %s** %s" % (clock(e["start"]), clock(e["end"]), day_word(e["start"])))
        if e["with"]:
            rows += ["```people"] + e["with"][:8] + (["+%d" % (len(e["with"]) - 8)] if len(e["with"]) > 8 else []) + \
                ["```"]
        if e["link"]:
            rows.append("[Join the call](%s)" % e["link"])
        if e["docs"]:
            rows += ["", "**Attached**", "```rows"] + ["file:%s | [%s](%s)" % (_doc_kind(url), cell(t), url)
                                                       for t, url in e["docs"][:5]] + ["```"]
        mail = self._last_mail(e["emails"])
        if mail:
            rows += ["", "**Last email from them**", "```rows", "%s | [%s](%s) | %s" % (cell(mail[2]), cell(mail[0]),
                                                                                     mail[1], mail[3]), "```"]
        chat = self._last_chat(e["emails"]) if len(e["emails"]) == 1 else None
        if chat:
            rows += ["", "**Last chat**", "```rows", "%s | %s" % tuple(cell(c) for c in chat), "```"]
        if e["notes"]:
            rows += ["", "**Notes**", e["notes"][:600]]
        card = {"id": "meeting", "title": ("%s: " % heading if heading else "") + e["title"], "text": "\n".join(rows)}
        if e["link"]:
            card["choices"] = [{"label": "Join", "do": "join my meeting"}]
        return card

    def _last_mail(self, emails):
        if not emails:
            return None
        try:
            gm = self.api("gmail", "v1")
            hits = google.run(gm.users().messages().list(
                userId="me", q="from:(%s) newer_than:60d" % " OR ".join(emails[:10]), maxResults=1)).get("messages")
            if not hits:
                return None
            msg = google.run(gm.users().messages().get(userId="me", id=hits[0]["id"], format="metadata",
                                                       metadataHeaders=["From", "Subject"]))
        except google.GoogleError:
            return None
        return (header(msg, "Subject") or "(no subject)", "https://mail.google.com/mail/u/0/#all/%s" % msg["threadId"],
                sender(header(msg, "From"))[0], day_word(int(msg.get("internalDate", 0)) / 1000))

    def _last_chat(self, emails):
        try:
            chat = self.api("chat", "v1")
            dm = google.run(chat.spaces().findDirectMessage(name="users/%s" % emails[0]))
            if not dm.get("name"):
                return None
            msgs = google.run(chat.spaces().messages().list(parent=dm["name"], pageSize=1,
                                                             orderBy="createTime desc")).get("messages")
        except google.GoogleError:
            return None
        if not msgs:
            return None
        return person(msgs[0].get("sender", {}).get("displayName")), short(msgs[0].get("text"), 30)

    # --- by itself: heads-ups, invites, meeting mode ---

    def poll(self):
        now = time.time()
        events = self.fetch(now - 12 * 3600, now + WATCH_DAYS * 86400, deleted=True)
        self._changes(events, now)
        upcoming = [e for e in events if attending(e)]
        self._heads_up(upcoming, now)
        self._meeting_mode(upcoming, now)

    def _changes(self, events, now):
        """New invites, moved and cancelled meetings since the last poll (not the ones you organise)."""
        old, snap = self._snap, {}
        for e in events:
            was = (old or {}).get(e["id"])
            snap[e["id"]] = dict(was, cancelled=True) if e["cancelled"] and was else e
        self._snap = snap
        if old is None:
            self._since = now
            return
        tell = self.j.skills.proactive
        for e in events:
            was = old.get(e["id"])
            if e["cancelled"]:
                if was and not was["cancelled"] and not was["mine"] and attending(was) and (was["start"] or 0) > now:
                    if tell.tell("calendar", "%s, %s, was cancelled." % (was["title"], when(was["start"])),
                                 "%s cancelled it on Google Calendar." % was["organizer"]):
                        self.j.inbox.put(("ctl", {"show": {"id": "cal-" + e["id"], "title": "Meeting cancelled",
                                                           "text": "{bad} **%s**\n%s · cancelled by %s" % (
                                                               was["title"], when(was["start"]), was["organizer"])}}))
                continue
            if e["mine"] or not e["start"] or e["start"] < now or e["kind"] in NOT_MEETINGS:
                continue
            if not was and e["response"] == "needsAction" and (e["created"] or 0) >= self._since - 300:
                self.last_invite = (e, now)
                line = "%s invited you to %s, %s." % (e["organizer"], e["title"], when(e["start"]))
                if tell.tell("calendar", line, "A new invite on your Google Calendar. Say accept the invite or "
                                               "decline it."):
                    card = self.prep_card(e, heading="Invite")
                    card["choices"] = [{"label": "Accept", "do": "accept the invite"},
                                       {"label": "Decline", "do": "decline the invite"}]
                    self.j.inbox.put(("ctl", {"show": card}))
            elif was and not was["cancelled"] and was["start"] != e["start"] and not e["declined"]:
                if tell.tell("calendar", "%s moved %s to %s." % (e["organizer"], e["title"], when(e["start"])),
                             "It was %s; %s moved it on Google Calendar." % (when(was["start"]), e["organizer"])):
                    self.j.inbox.put(("ctl", {"show": {
                        "id": "cal-" + e["id"], "title": "Meeting moved", "text": "{warn} **%s**\nNow **%s**, was %s"
                        " · by %s" % (e["title"], when(e["start"]), when(was["start"]), e["organizer"]),
                        "choices": [{"label": "Show my day", "do": "what's on my calendar %s" % day_word(
                            e["start"])}]}}))

    def _heads_up(self, events, now):
        lead = self.cfg("heads_up_minutes", 5) * 60
        for e in events:
            key = (e["id"], e["start"])
            if e["all_day"] or key in self._warned or not 0 < e["start"] - now <= lead + 30:
                continue
            self._warned.add(key)
            minutes = max(1, round((e["start"] - now) / 60))
            line = "%s in %d minute%s%s.%s" % (e["title"], minutes, "" if minutes == 1 else "s",
                                               ", with %s" % _people(e) if e["with"] else "",
                                               " Say join my meeting to join." if e["link"] else "")
            if self.j.skills.proactive.tell("meeting", line, "It starts at %s on your Google Calendar, and I give a "
                                            "heads-up %d minutes before (google heads_up_minutes in settings)." % (
                                                clock(e["start"]), lead // 60), hold=False):
                self.j.inbox.put(("ctl", {"show": self.prep_card(e, heading="In %d min" % minutes)}))

    def _meeting_mode(self, events, now):
        call = next((e for e in events if is_call(e) and e["start"] <= now < e["end"] - 60), None)
        if call:
            self._start_meeting_mode(call)

    def _start_meeting_mode(self, e):
        """Focus mode until the call ends, and the music paused; once per meeting, never over your own focus."""
        fm = self.j.skills.focus_mode
        if not self.j.memory.pref("meeting_mode", self.cfg("meeting_mode", True)) or self._in_call == e["id"] or \
                fm.focusing():
            return
        self._in_call = e["id"]
        fm.ending = "%s is over, so I'm out of meeting mode." % e["title"]
        fm.since = time.time()
        fm._start_focus(e["end"])
        _pause_music()
        self.j.inbox.put(("ctl", {"show": {
            "id": "meeting", "title": "In %s" % e["title"], "text": "```progress\nMeeting: %d/%d playing\n```\n"
            "{check} Quiet until %s · music paused" % (max(0, time.time() - e["start"]), e["end"] - e["start"],
                                                        clock(e["end"])),
            "choices": [{"label": "Rejoin", "do": "join my meeting"}, {"label": "I'm out", "do": "stop focusing"}]}}))
        self.j.skills.proactive.note("meeting", "Meeting mode for %s." % e["title"],
                                     "%s is on until %s, so I hold heads-ups and banners and paused the music. Say "
                                     "turn off meeting mode to stop that." % (e["title"], clock(e["end"])))

    def meeting_mode(self, m):
        on = any(w in m.group(0).lower().split() for w in ("on", "start"))
        self.j.memory.set_pref("meeting_mode", on)
        self.say("Okay, during calls I'll hold heads-ups and pause the music." if on else
                 "Okay, no more meeting mode. I'll still tell you before meetings.")

    # --- writing: asked about first ---

    def respond(self, m):
        verb = m.group(1).lower()
        status = "declined" if verb in ("decline", "reject", "say no to") else \
            "tentative" if verb in ("tentatively accept", "maybe") else "accepted"
        e = self._invite(m.group(2))
        if not e:
            self.say("You have no invites waiting.")
            return
        cal = self.api("calendar", "v3", "calendar.events")
        ask = {"accepted": "Accept", "declined": "Decline", "tentative": "Say maybe to"}[status]
        if not self.j.ask("%s %s, %s, from %s?" % (ask, e["title"], when(e["start"]), e["organizer"])):
            self.say("Okay, I've left it.")
            return
        raw = google.run(cal.events().get(calendarId="primary", eventId=e["id"]))
        attendees = raw.get("attendees") or []
        for a in attendees:
            if a.get("self"):
                a["responseStatus"] = status
        google.run(cal.events().patch(calendarId="primary", eventId=e["id"], body={"attendees": attendees},
                                      sendUpdates="all"))
        self.say("Done, %s %s. %s will see it." % ({"accepted": "accepted", "declined": "declined",
                                                    "tentative": "said maybe to"}[status], e["title"],
                                                   first_name(e["organizer"])))

    def _invite(self, organizer=None):
        """The invite "accept it" means: the one just announced, else the next one you haven't answered."""
        if self.last_invite and time.time() - self.last_invite[1] < 7200 and not organizer:
            return self.last_invite[0]
        now = time.time()
        waiting = [e for e in self.fetch(now, now + WATCH_DAYS * 86400) if e["response"] == "needsAction"
                   and not e["cancelled"] and e["kind"] not in NOT_MEETINGS]
        if organizer:
            waiting = [e for e in waiting if organizer.lower() in e["organizer"].lower()]
        return waiting[0] if waiting else None

    def schedule(self, m):
        text = tidy(m.group(0))
        meet = parse_meeting(text)
        if not meet:
            self.say("When should it be? Say it like: schedule a meeting with Rahul tomorrow at 3.")
            return
        start, minutes, names, topic = meet
        if start < time.time():
            self.say("That time's already passed.")
            return
        cal = self.api("calendar", "v3", "calendar.events")
        emails = []
        for name in names:
            email = find_email(self.api, name)
            if not email:
                self.say("I couldn't find an email address for %s." % name)
                return
            emails.append(email)
        end = start + minutes * 60
        title = topic[:1].upper() + topic[1:] if topic else "Meeting with %s" % and_list(
            first_name(n) for n in names) if names else "Meeting"
        clash = [e for e in self.meetings(start, end) if not e["all_day"] and not e["free"]]
        question = "Schedule %s, %s to %s%s, with a Meet link?%s" % (
            title, when(start), clock(end), ", inviting %s" % and_list(emails) if emails else "",
            " You already have %s then." % clash[0]["title"] if clash else "")
        if not self.j.ask(question):
            self.say("Okay, not scheduled.")
            return
        body = {"summary": title, "start": {"dateTime": _local_iso(start)}, "end": {"dateTime": _local_iso(end)},
                "attendees": [{"email": a} for a in emails],
                "conferenceData": {"createRequest": {"requestId": uuid.uuid4().hex,
                                                     "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}
        made = google.run(cal.events().insert(calendarId="primary", body=body, conferenceDataVersion=1,
                                              sendUpdates="all"))
        self.j.show(self.prep_card(_event(made), heading="Scheduled"))
        self.say("Done. %s is on your calendar for %s%s." % (
            title, when(start), ", and the invite went to %s" % and_list(first_name(person("", a)) for a in emails)
            if emails else ""))

    def connect(self, m):
        if self._signing_in:
            self.say("Google's sign-in is already open in your browser.")
            return
        self._signing_in = True
        self.say("Opening Google's sign-in in your browser. Pick your work account and allow it.")

        def go():
            try:
                google.sign_in()
                self.j.inbox.put(("say", "Google's connected."))
            except Exception as e:
                self.j.inbox.put(("say", "Google's sign-in didn't finish: %s." % str(e).rstrip(".")))
            finally:
                self._signing_in = False

        threading.Thread(target=go, daemon=True).start()

    # --- for the briefing and the wrap ---

    def for_briefing(self, day_offset=0):
        """(card lines, spoken sentence) for a day's meetings; ([], "") when Google isn't connected."""
        if not google.connected():
            return [], ""
        start, end, label = span("tomorrow" if day_offset else "today")
        try:
            events = [e for e in self.meetings(start, end) if not e["all_day"]]
        except google.GoogleError as e:
            return ["**Meetings**", "- %s" % e], ""
        if not events:
            return ["**Meetings %s**" % label, "- None"], "No meetings %s." % label
        lines = ["**Meetings %s**" % label] + (_timeline(events) if not day_offset else []) + [
            "- %s %s%s" % (clock(e["start"]), e["title"], " · with %s" % ", ".join(e["with"][:3]) if e["with"] else "")
            for e in events]
        first = events[0]
        said = "%s, %s at %s." % ("One meeting %s" % label if len(events) == 1 else "%d meetings %s, first" % (
            len(events), label), first["title"], clock(first["start"]))
        return lines, said


def _event(raw):
    s, e = raw.get("start") or {}, raw.get("end") or {}
    attendees = raw.get("attendees") or []
    me = next((a for a in attendees if a.get("self")), {})
    others = [a for a in attendees if not a.get("self") and not a.get("resource") and a.get("email")]
    org = raw.get("organizer") or {}
    notes = re.sub(r"<[^>]+>", " ", raw.get("description") or "")
    return {"id": raw.get("id"), "title": raw.get("summary") or "(no title)",
            "start": parse_time(s.get("dateTime") or s.get("date")), "end": parse_time(e.get("dateTime") or e.get("date")),
            "all_day": bool(s.get("date")) and not s.get("dateTime"), "cancelled": raw.get("status") == "cancelled",
            "response": me.get("responseStatus") or ("accepted" if org.get("self") else None),
            "declined": me.get("responseStatus") == "declined", "mine": bool(org.get("self")),
            "organizer": person(org.get("displayName"), org.get("email")),
            "with": [person(a.get("displayName"), a["email"]) for a in others],
            "emails": [a["email"] for a in others], "link": join_link(raw), "html": raw.get("htmlLink"),
            "created": parse_time(raw.get("created")), "free": raw.get("transparency") == "transparent",
            "kind": raw.get("eventType") or "default",
            "docs": [(a.get("title") or "file", a["fileUrl"]) for a in raw.get("attachments") or [] if a.get("fileUrl")],
            "notes": " ".join(notes.split())}


def join_link(raw):
    if raw.get("hangoutLink"):
        return raw["hangoutLink"]
    for p in (raw.get("conferenceData") or {}).get("entryPoints") or []:
        if p.get("entryPointType") == "video" and p.get("uri"):
            return p["uri"]
    m = LINK.search("%s %s" % (raw.get("location") or "", raw.get("description") or ""))
    return m.group(0) if m else None


def attending(e):
    return not (e["cancelled"] or e["declined"] or e["kind"] in NOT_MEETINGS) and e["start"] is not None


def is_call(e):
    """A meeting worth going quiet for: you're in it, with other people or a call link."""
    return attending(e) and not e["all_day"] and not e["free"] and (e["link"] or e["with"]) and \
        (e["mine"] or e["response"] in ("accepted", "tentative"))


def span(said):
    """"today" / "tomorrow" / "thursday" / "this week" -> (start, end, label)."""
    said = (said or "today").lower().strip()
    today = datetime.date.today()
    midnight = lambda d: time.mktime(d.timetuple())  # noqa: E731
    if said in ("today", "tonight"):
        return midnight(today), midnight(today + datetime.timedelta(days=1)), "today"
    if said == "tomorrow":
        d = today + datetime.timedelta(days=1)
        return midnight(d), midnight(d + datetime.timedelta(days=1)), "tomorrow"
    if said in ("this week", "next week"):
        monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(
            days=7 if said == "next week" else 0)
        return max(time.time(), midnight(monday)), midnight(monday + datetime.timedelta(days=7)), said
    day = said.split()[-1]
    if day in DAYS:
        ahead = (DAYS.index(day) - today.weekday()) % 7 or (7 if said.startswith("next") else 0)
        d = today + datetime.timedelta(days=ahead)
        return midnight(d), midnight(d + datetime.timedelta(days=1)), day_word(midnight(d))
    return midnight(today), midnight(today + datetime.timedelta(days=1)), "today"


def _on_day(day, ts):
    """The clock time of `ts` on the named weekday ("on thursday")."""
    start = span(day.replace("on ", ""))[0]
    t = time.localtime(ts)
    return start + t.tm_hour * 3600 + t.tm_min * 60


def _gaps(busy, start, end, least=1800):
    """The free stretches of at least `least` seconds in [start, end), around the busy (start, end) spans."""
    gaps, at = [], start
    for b0, b1 in sorted(busy):
        if b0 - at >= least:
            gaps.append((at, min(b0, end)))
        at = max(at, b1)
    if end - at >= least:
        gaps.append((at, end))
    return [(a, b) for a, b in gaps if b - a >= least]


def _hm(ts):
    t = time.localtime(ts)
    return "%d:%02d" % (t.tm_hour, t.tm_min)


def _timeline(events, gaps=(), reminders=()):
    """A ```timeline block (ui/blocks.py): each timed meeting as long as it runs, the free stretches dashed, and
    reminders as dots; no "now" line on another day's. [] when there's nothing to draw."""
    timed = [e for e in events if not e["all_day"] and e["start"] and e["end"]]
    lines = ["%s-%s %s" % (_hm(e["start"]), _hm(e["end"]), e["title"]) for e in timed]
    marks = [e["start"] for e in timed] + [a for a, _b in gaps] + [at for at, _t in reminders]
    if marks and day_word(marks[0]) != "today":
        lines.append("now: off")
    lines += ["%s %s" % (_hm(at), text) for at, text in reminders]
    lines += ["free: %s-%s %s" % (_hm(a), _hm(b), _length(b - a)) for a, b in gaps]
    return ["```timeline"] + lines[:14] + ["```"] if lines else []


def _length(seconds):
    """"30 min", "1 h", "1 h 30"."""
    h, m = divmod(round(seconds / 60), 60)
    return "%d min" % m if not h else "%d h" % h + (" %d" % m if m else "")


def _doc_kind(url):
    """The badge for an attached file's link: doc, sheet, slides, pdf or file."""
    return next((k for w, k in (("document", "doc"), ("spreadsheets", "sheet"), ("presentation", "slides"),
                                (".pdf", "pdf"), ("forms", "form")) if w in url), "file")


def _rows(events, now):
    return ["- %s %s%s%s" % ("{off}" if e["end"] and e["end"] <= now else "{check}",
                             "all day" if e["all_day"] else "%s–%s" % (clock(e["start"]), clock(e["end"])),
                             "  **%s**" % e["title"], (" · with %s" % ", ".join(e["with"][:3]) if e["with"] else "")
                             + (" · [Join](%s)" % e["link"] if e["link"] and not e["all_day"] else ""))
            for e in events]


def _day_line(timed, all_day, label, now, single):
    if not timed:
        extra = " Just %s, all day." % and_list(e["title"] for e in all_day[:2]) if all_day else ""
        return "No meetings %s.%s" % (label, extra)
    if not single:
        nxt = next((e for e in timed if e["start"] > now), timed[0])
        return "%d meeting%s %s. The next is %s, %s. They're on the card." % (
            len(timed), "" if len(timed) == 1 else "s", label, nxt["title"], when(nxt["start"]))
    left = [e for e in timed if e["end"] > now]
    if not left:
        return "That's all for %s: you had %d meeting%s." % (label, len(timed), "" if len(timed) == 1 else "s")
    now_on = [e for e in left if e["start"] <= now]
    later = [e for e in left if e["start"] > now]
    head = "You're in %s now, until %s. " % (now_on[0]["title"], clock(now_on[0]["end"])) if now_on else ""
    if not later:
        return head + "Nothing after that %s." % label
    word = "left" if len(later) < len(timed) else ""
    count = "%d meeting%s%s %s" % (len(later), "" if len(later) == 1 else "s", " " + word if word else "", label)
    if len(later) <= 3:
        return head + "%s: %s." % (count.capitalize() if not head else count,
                                   and_list("%s at %s" % (e["title"], clock(e["start"])) for e in later))
    return head + "%s. Next: %s at %s. They're on the card." % (count.capitalize() if not head else count,
                                                               later[0]["title"], clock(later[0]["start"]))


def _people(e):
    names = [first_name(n) for n in e["with"]]
    return and_list(names) if len(names) <= 3 else "%s and %d others" % (", ".join(names[:2]), len(names) - 2)


def _in(seconds):
    minutes = round(seconds / 60)
    if minutes < 60:
        return "in %d minute%s" % (minutes, "" if minutes == 1 else "s")
    hours = round(seconds / 3600)
    return "in %d hour%s" % (hours, "" if hours == 1 else "s") if hours < 24 else "in %d days" % round(hours / 24)


def _local_iso(ts):
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat()


def _pause_music():
    """Pause a player that's playing (through its MPRIS Pause, never by pressing keys in a window)."""
    try:
        from skills.media import _gdbus, playing_player
        name = playing_player()
        if name and name.startswith("org.mpris."):
            _gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                   "org.mpris.MediaPlayer2.Player.Pause")
    except Exception as e:
        print("[calendar] couldn't pause the music: %r" % e)


DURATION = {"half an hour": 30, "an hour": 60, "one hour": 60, "an hour and a half": 90, "a quick": 15}


def parse_meeting(text):
    """"schedule a meeting with Rahul and Priya tomorrow at 3 about the launch for 45 minutes" ->
    (start, minutes, ["Rahul", "Priya"], "the launch"), or None without a clock time."""
    t = " %s " % text.lower()
    stop = r"(?= about | to discuss | regarding | (?:in|to|on|into) my | on (?:%s)| today| tonight| tomorrow| next | at | for |\s*$)" % \
        "|".join(DAYS)
    who = re.search(r" with (.+?)%s" % stop, t)
    topic = re.search(r" (?:about|to discuss|regarding) (.+?)(?= with | on (?:%s)| today| tomorrow| at | for "
                      r"\d| for (?:half|an|one) |\s*$)" % "|".join(DAYS), t)
    at = re.search(r" at (\d{1,2})(?:[:.](\d{2}))? ?([ap])?\.? ?m?\.?(?=\s|$)", t)
    if not at:
        return None
    hour, minute = int(at.group(1)), int(at.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    if at.group(3) == "p" and hour < 12:
        hour += 12
    elif at.group(3) == "a" and hour == 12:
        hour = 0
    elif not at.group(3) and 1 <= hour <= 7:
        hour += 12  # "at 3" for a meeting is the afternoon
    day = re.search(r" (today|tonight|tomorrow|(?:on |next )?(?:%s))(?= )" % "|".join(DAYS), t)
    start = span(day.group(1) if day else "today")[0] + hour * 3600 + minute * 60
    if not day and start < time.time():
        start = span("tomorrow")[0] + hour * 3600 + minute * 60  # "at 9" said in the evening: tomorrow
    minutes = 30
    dur = re.search(r" for (\d+) ?(minutes?|mins?|hours?|hrs?)| for (half an hour|an hour and a half|an hour|one hour)",
                    t)
    if dur:
        minutes = DURATION[dur.group(3)] if dur.group(3) else int(dur.group(1)) * (60 if dur.group(2).startswith("h")
                                                                                   else 1)
    names = [n.strip().title() for n in re.split(r",| and ", who.group(1))] if who else []
    return start, minutes, [n for n in names if n and n.lower() not in ("me", "myself")], \
        topic.group(1).strip() if topic else ""
