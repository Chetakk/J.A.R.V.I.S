"""Google Calendar, Gmail, Chat, Drive and the end-of-day wrap (skills/google_*.py, gmail.py, drive.py, wrap.py).
Google itself is faked (FakeGoogle): every request is recorded and answered from the test's data; nothing leaves."""
import base64
import copy
import datetime
import json
import queue
import time

import pytest

from skills import google_calendar
from system import google_account


class FakeGoogle:
    """service(name, version) for system/google_account.py: a request is a dotted path ("calendar.events.list") with
    its arguments; answers come from on(path, value or fn(args))."""

    def __init__(self):
        self.answers = {}
        self.calls = []  # (path, args)

    def on(self, path, answer):
        self.answers[path] = answer
        return self

    def service(self, name, version):
        return _Node(self, name, {})

    def made(self, path):
        return [args for p, args in self.calls if p == path]


class _Node:
    def __init__(self, g, path, args):
        self.g, self.path, self.args = g, path, args

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        return lambda *a, **args: _Node(self.g, "%s.%s" % (self.path, attr), args)

    def execute(self, **_):
        self.g.calls.append((self.path, self.args))
        answer = self.g.answers.get(self.path, {})
        return answer(self.args) if callable(answer) else copy.deepcopy(answer)


@pytest.fixture
def g(jv, monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(google_account, "service", fake.service)
    jv.keyring.items[google_account.TOKEN] = json.dumps({"scopes": google_account.SCOPES})
    jv.g = fake
    return jv


def at(day_offset, hour, minute=0):
    d = datetime.date.today() + datetime.timedelta(days=day_offset)
    return time.mktime(datetime.datetime(d.year, d.month, d.day, hour, minute).timetuple())


def iso(ts):
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat()


def ev(eid, title, start, minutes=30, people=(), link=None, me="accepted", organizer="rahul@biztel.ai",
       created=None, status="confirmed"):
    raw = {"id": eid, "summary": title, "status": status, "start": {"dateTime": iso(start)},
           "end": {"dateTime": iso(start + minutes * 60)}, "organizer": {"email": organizer},
           "created": datetime.datetime.utcfromtimestamp(created or time.time() - 30 * 86400).isoformat() + "Z",
           "attendees": [{"email": "me@biztel.ai", "self": True, "responseStatus": me}] + [
               {"email": p, "displayName": p.split("@")[0].title()} for p in people]}
    if organizer == "me@biztel.ai":
        raw["organizer"]["self"] = True
    if link:
        raw["hangoutLink"] = link
    return raw


def events(*items):
    return lambda args: {"items": list(items)}


def drain(jv):
    out = []
    while True:
        try:
            out.append(jv.j.inbox.get_nowait())
        except queue.Empty:
            return out


def said_by_itself(jv):
    return [line for kind, line in drain(jv) if kind == "say"]


# --- calendar ---

def test_without_google_it_says_how_to_connect(jv):
    jv.do("do we have meetings today")
    assert jv.says("Google isn't connected", "connect Google")


def test_without_google_my_calendar_is_still_the_day_tracker(jv):
    jv.do("what's on my calendar today")
    assert not jv.says("Google")


def test_meetings_tomorrow_are_said_and_shown(g):
    g.g.on("calendar.events.list", events(
        ev("a", "Stand-up", at(1, 10), people=["priya@biztel.ai"], link="https://meet.google.com/abc"),
        ev("b", "Design review", at(1, 15), minutes=60),
        ev("c", "Skipped thing", at(1, 12), me="declined")))
    g.do("what's on my calendar tomorrow")
    assert g.says("2 meetings tomorrow", "Stand-up at 10 am", "Design review at 3 pm")
    assert "Skipped thing" not in g.card_text()
    assert "[Join](https://meet.google.com/abc)" in g.card_text()
    assert "```timeline" in g.card_text() and "now: off" in g.card_text()  # tomorrow's: no "now" line


def test_next_meeting_says_when_and_with_whom(g):
    soon = time.time() + 40 * 60
    g.g.on("calendar.events.list", events(ev("a", "Sync", soon, people=["rahul.kumar@biztel.ai"])))
    g.do("when's my next meeting")
    assert g.says("Your next meeting is Sync", "in 40 minutes", "with Rahul")


def test_am_i_free(g):
    g.g.on("calendar.events.list", events(ev("a", "Design review", at(1, 15), minutes=60)))
    g.do("am i free at 3 tomorrow")
    assert g.says("No, tomorrow at 3 pm you have Design review"), g.said
    g.clear()
    g.do("am i free at 11 tomorrow")
    assert g.says("Yes, you're free tomorrow at 11 am")


def test_free_time_tomorrow_afternoon(g):
    g.g.on("calendar.events.list", events(ev("a", "Lunch talk", at(1, 13), minutes=60)))
    g.do("when am i free tomorrow afternoon")
    assert g.says("Tomorrow afternoon you're free 12 pm to 1 pm and 2 pm to 5 pm")


def test_join_opens_the_call_and_goes_into_meeting_mode(g):
    g.g.on("calendar.events.list", events(ev("a", "Stand-up", time.time() - 120, people=["p@biztel.ai"],
                                             link="https://meet.google.com/xyz")))
    g.do("join my meeting")
    assert g.shell.ran(r"xdg-open https://meet\.google\.com/xyz")
    fm = g.j.skills.focus_mode
    assert fm.focusing() and "Stand-up is over" in fm.ending


def test_heads_up_comes_once_with_a_card(g):
    g.g.on("calendar.events.list", events(ev("a", "Design review", time.time() + 4 * 60, people=["rahul@biztel.ai"],
                                             link="https://meet.google.com/q")))
    cal = g.j.skills.calendar
    cal.poll()
    cal.poll()
    items = drain(g)
    lines = [x for k, x in items if k == "say"]
    assert len(lines) == 1 and "Design review in 4 minutes, with Rahul" in lines[0] and "join my meeting" in lines[0]
    cards = [x["show"] for k, x in items if k == "ctl"]
    assert cards and cards[0]["choices"][0]["do"] == "join my meeting"


def test_new_invite_move_and_cancel_are_said(g):
    stays = ev("a", "Planning", at(2, 11))
    goes = ev("c", "Retro", at(3, 16))
    g.g.on("calendar.events.list", events(stays, goes))
    cal = g.j.skills.calendar
    cal.poll()  # the first look only learns what's there
    assert not said_by_itself(g)
    moved = dict(stays, start={"dateTime": iso(at(2, 14))}, end={"dateTime": iso(at(2, 15))})
    invite = ev("b", "Launch prep", at(4, 10), me="needsAction", organizer="priya@biztel.ai", created=time.time())
    gone = {"id": "c", "status": "cancelled"}
    g.g.on("calendar.events.list", events(moved, invite, gone))
    cal.poll()
    lines = " | ".join(said_by_itself(g))
    assert "Priya invited you to Launch prep" in lines
    assert "Rahul moved Planning to" in lines and "2 pm" in lines
    assert "Retro" in lines and "was cancelled" in lines


def test_accepting_an_invite_asks_then_answers_for_you(g):
    invite = ev("b", "Launch prep", at(4, 10), me="needsAction", organizer="priya@biztel.ai")
    g.g.on("calendar.events.list", events(invite)).on("calendar.events.get", invite)
    g.do("accept the invite", "yes")
    assert g.questions and "Accept Launch prep" in g.questions[0]
    patch = g.g.made("calendar.events.patch")[0]
    assert patch["sendUpdates"] == "all"
    assert [a["responseStatus"] for a in patch["body"]["attendees"] if a.get("self")] == ["accepted"]
    assert g.says("accepted Launch prep")


def test_saying_no_leaves_the_invite(g):
    invite = ev("b", "Launch prep", at(4, 10), me="needsAction", organizer="priya@biztel.ai")
    g.g.on("calendar.events.list", events(invite))
    g.do("decline it", "no")
    assert not g.g.made("calendar.events.patch")


def test_writing_needs_the_new_permission(g):
    g.keyring.items[google_account.TOKEN] = json.dumps({"scopes": [google_account.BASE + s
                                                                   for s in google_account.READS]})
    g.g.on("calendar.events.list", events(ev("b", "X", at(4, 10), me="needsAction")))
    g.do("accept the invite", "yes")
    assert g.says("permission", "connect Google")
    assert not g.g.made("calendar.events.patch")


def test_scheduling_asks_first_and_invites_people(g):
    g.g.on("people.people.searchDirectoryPeople", {"people": [{"emailAddresses": [{"value": "rahul@biztel.ai"}]}]})
    g.do("schedule a meeting with Rahul tomorrow at 3 about the launch", "yes")
    assert "The launch, tomorrow at 3 pm to 3:30 pm, inviting rahul@biztel.ai" in g.questions[0]
    made = g.g.made("calendar.events.insert")[0]
    assert made["sendUpdates"] == "all" and made["conferenceDataVersion"] == 1
    assert made["body"]["attendees"] == [{"email": "rahul@biztel.ai"}]
    assert made["body"]["start"]["dateTime"].startswith(datetime.date.fromtimestamp(at(1, 0)).isoformat() + "T15:00")


def test_parse_meeting():
    start, minutes, names, topic = google_calendar.parse_meeting(
        "schedule a call with Rahul and Priya on friday at 10:30 for 45 minutes about hiring")
    assert time.localtime(start).tm_wday == 4 and time.strftime("%H:%M", time.localtime(start)) == "10:30"
    assert (minutes, names, topic) == (45, ["Rahul", "Priya"], "hiring")
    assert google_calendar.parse_meeting("schedule a meeting with Rahul") is None


# --- gmail ---

def _msg(mid, frm, subject, text="", when=None):
    data = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    return {"id": mid, "threadId": "t" + mid, "internalDate": str(int((when or time.time()) * 1000)),
            "snippet": text[:40], "payload": {"mimeType": "text/plain", "body": {"data": data}, "headers": [
                {"name": "From", "value": frm}, {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": "<%s@mail>" % mid}]}}


def _mailbox(g, msgs, sent_to=()):
    by_id = {m["id"]: m for m in msgs}

    def listing(args):
        q = args.get("q", "")
        if q.startswith("in:sent to:"):
            return {"messages": [{"id": "s"}]} if q.split(":")[-1] in sent_to else {}
        hits = [m for m in msgs if 'from:"' not in q or q.split('"')[1].lower() in m["payload"]["headers"][0]["value"]
                .lower()]
        return {"messages": [{"id": m["id"]} for m in hits], "resultSizeEstimate": len(hits)}
    g.g.on("gmail.users.messages.list", listing).on("gmail.users.messages.get", lambda a: by_id[a["id"]])
    g.g.on("gmail.users.getProfile", {"emailAddress": "me@biztel.ai"})


def test_new_emails_are_listed(g):
    _mailbox(g, [_msg("1", '"Rahul K" <rahul@biztel.ai>', "Design notes"),
                 _msg("2", "priya@biztel.ai", "Invoice for October")])
    g.do("any new emails")
    assert g.says("2 new emails", "Rahul about Design notes", "Priya about Invoice for October")


def test_mail_is_announced_only_from_people_you_write_to(g):
    _mailbox(g, [], sent_to=("rahul@biztel.ai",))
    gm = g.j.skills.gmail
    gm.poll()
    _mailbox(g, [_msg("1", "Rahul <rahul@biztel.ai>", "Can you review?"),
                 _msg("2", "News <news@shop.com>", "50% off")], sent_to=("rahul@biztel.ai",))
    gm.poll()
    lines = said_by_itself(g)
    assert lines == ["Email from Rahul: Can you review?."]


def test_reading_an_email_then_making_it_a_todo_and_a_draft_reply(g):
    _mailbox(g, [_msg("1", "Rahul <rahul@biztel.ai>", "Budget", "Hi, can you send the budget by Friday?\n\n"
                      "On Mon, Rahul wrote:\n> old stuff")])
    g.do("read the latest email from rahul")
    assert g.says("From Rahul", "Budget")
    assert g.says("can you send the budget by Friday?")
    assert not g.says("old stuff")
    added = []
    g.j.skills.todos.add = lambda m: added.append(m.group(1))
    g.do("add this email to my to-dos")
    assert added == ["Reply to Rahul: Budget"]
    g.claude.reply("Hi Rahul, I'll send it tomorrow.")
    g.do("draft a reply saying I'll send it tomorrow")
    raw = g.g.made("gmail.users.drafts.create")[0]["body"]["message"]["raw"]
    mail = base64.urlsafe_b64decode(raw).decode()
    assert "To: Rahul <rahul@biztel.ai>" in mail and "Subject: Re: Budget" in mail and "In-Reply-To: <1@mail>" in mail
    assert "I'll send it tomorrow" in mail
    assert g.says("draft", "isn't sent")
    assert not g.g.made("gmail.users.messages.send")


# --- chat ---

def _chat(g, spaces, messages):
    g.g.on("chat.spaces.list", lambda a: {"spaces": spaces})
    g.g.on("chat.spaces.messages.list", lambda a: {"messages": messages.get(a["parent"], [])})
    g.g.on("people.people.get", {"resourceName": "people/1"})


def _space(name, kind, active, title=None):
    return {"name": name, "spaceType": kind, "lastActiveTime": active, "displayName": title,
            "spaceUri": "https://chat.google.com/x"}


def _said(who, text, uid, mention=None):
    m = {"sender": {"name": "users/%s" % uid, "displayName": who, "type": "HUMAN"}, "text": text,
         "createTime": "2026-11-11T10:00:01Z"}
    if mention:
        m["annotations"] = [{"type": "USER_MENTION", "userMention": {"user": {"name": "users/%s" % mention}}}]
    return m


def test_chat_says_direct_messages_and_mentions_only(g):
    _chat(g, [_space("spaces/dm", "DIRECT_MESSAGE", "2026-11-11T09:00:00Z"),
              _space("spaces/team", "SPACE", "2026-11-11T09:00:00Z", "Team")], {})
    chat = g.j.skills.google_chat
    chat.poll()
    _chat(g, [_space("spaces/dm", "DIRECT_MESSAGE", "2026-11-11T10:00:01Z"),
              _space("spaces/team", "SPACE", "2026-11-11T10:00:01Z", "Team")],
          {"spaces/dm": [_said("Rahul Kumar", "are you free for a quick call?", 2), _said("Me", "my own", 1)],
           "spaces/team": [_said("Priya", "lunch anyone", 3)]})
    chat.poll()
    assert said_by_itself(g) == ["Rahul on Chat: are you free for a quick call?"]


def test_chat_stays_quiet_while_you_look_at_it(g):
    _chat(g, [_space("spaces/dm", "DIRECT_MESSAGE", "2026-11-11T09:00:00Z")], {})
    chat = g.j.skills.google_chat
    chat.poll()
    g.desktop.add("Google Chat", "google-chrome")
    _chat(g, [_space("spaces/dm", "DIRECT_MESSAGE", "2026-11-11T10:00:01Z")],
          {"spaces/dm": [_said("Rahul", "hi", 2)]})
    chat.poll()
    assert not said_by_itself(g)


def test_chat_message_is_sent_only_after_yes(g):
    g.g.on("people.people.searchDirectoryPeople", {"people": [{"emailAddresses": [{"value": "rahul@biztel.ai"}]}]})
    g.g.on("chat.spaces.findDirectMessage", {"name": "spaces/dm"})
    g.do("tell rahul on chat I'm running late", "no")
    assert not g.g.made("chat.spaces.messages.create")
    g.do("tell rahul on chat I'm running late", "yes")
    assert g.g.made("chat.spaces.messages.create") == [{"parent": "spaces/dm", "body": {"text": "I'm running late"}}]


# --- drive and the wrap ---

def test_drive_search_shows_files(g):
    g.g.on("drive.files.list", {"files": [
        {"id": "1", "name": "Budget 2026", "mimeType": "application/vnd.google-apps.spreadsheet",
         "webViewLink": "https://docs.google.com/1", "modifiedTime": "2026-11-10T10:00:00Z"},
        {"id": "2", "name": "Budget notes", "mimeType": "application/vnd.google-apps.document",
         "webViewLink": "https://docs.google.com/2"}]})
    g.do("find the budget sheet in my drive")
    assert g.says("Found 2", "Budget 2026, a sheet")
    assert "[Budget 2026](https://docs.google.com/1)" in g.card_text()


def test_the_wrap_collects_what_waits_on_you(g):
    g.j.skills.gmail.waiting_on_you = lambda: [("Rahul K", "Budget")]
    g.j.skills.google_chat.waiting_on_you = lambda: [("Priya", "ping me")]
    g.j.skills.todos.open_todos = lambda: [{"text": "Ship it"}]
    g.g.on("calendar.events.list", events(ev("a", "Stand-up", at(1, 10))))
    g.do("wrap up my day")
    assert g.says("Before you go", "One email waiting on you, from Rahul", "Priya is waiting on Chat",
                  "One meeting tomorrow, Stand-up at 10 am", "One to-do still open")


# --- people and projects ---

def _who(name, email, title=""):
    return {"names": [{"displayName": name}], "emailAddresses": [{"value": email}],
            "organizations": [{"title": title, "name": "BiztelAI"}] if title else []}


def _mail_meta(args):
    return {"id": args.get("id"), "threadId": "t1", "internalDate": str(int(time.time() * 1000)),
            "payload": {"headers": [{"name": "From", "value": "Rahul Sharma <rahul@biztel.ai>"},
                                    {"name": "Subject", "value": "UPE camera mount"}]}, "snippet": "New bracket"}


def test_who_is_a_contact_shows_what_is_going_on_with_them(g):
    g.g.on("people.people.searchDirectoryPeople", {"people": [_who("Rahul Sharma", "rahul@biztel.ai",
                                                                   "Vision engineer")]})
    g.g.on("gmail.users.messages.list", {"messages": [{"id": "m1"}]}).on("gmail.users.messages.get", _mail_meta)
    g.g.on("calendar.events.list", events(ev("a", "Stand-up", at(1, 10), people=["rahul@biztel.ai"],
                                             link="https://meet.google.com/abc")))
    g.g.on("drive.files.list", {"files": [{"id": "f1", "name": "Q4 tracker", "webViewLink": "https://docs.google.com/f1",
                                           "mimeType": "application/vnd.google-apps.spreadsheet",
                                           "modifiedTime": iso(time.time() - 86400)}]})
    g.do("who is rahul")
    card = g.cards[-1]
    assert card["title"] == "Rahul Sharma"
    assert "Last email" in card["text"] and "UPE camera mount" in card["text"]
    assert "Next meeting together | Stand-up" in card["text"] and "file:sheet | [Q4 tracker]" in card["text"]
    assert "Rahul Sharma, vision engineer." in g.said[-1] and "Stand-up, tomorrow at 10 am" in g.said[-1]
    assert not g.claude.calls


def test_two_namesakes_ask_which_and_the_pick_is_remembered(g):
    from skills import google_common
    g.g.on("people.people.searchDirectoryPeople", {"people": [_who("Rahul Sharma", "rahul@biztel.ai"),
                                                              _who("Rahul Menon", "menon@biztel.ai")]})
    g.do("what's going on with rahul")
    pick = g.cards[-1]
    assert pick["title"] == "Which Rahul?" and [c["label"] for c in pick["choices"]] == ["Rahul Sharma", "Rahul Menon"]
    assert "Which Rahul: Rahul Sharma or Rahul Menon?" in g.said[-1]
    g.do(pick["choices"][1]["do"])
    assert g.cards[-1]["title"] == "Rahul Menon"
    assert google_common.ALIASES["rahul"] == "menon@biztel.ai" == g.j.memory.pref("people_aliases")["rahul"]
    assert google_common.find_email(None, "Rahul") == "menon@biztel.ai"  # and so everywhere else


def test_someone_not_in_your_contacts_is_not_ours(g):
    assert g.j.skills.people.lookup("elon musk") == []  # (goes on to Wikipedia)
    from skills.people import looks_like_name
    assert not looks_like_name("the president of india") and not looks_like_name("my computer")


def test_the_project_card_leads_with_its_mail_docs_meetings_and_chat(g, tmp_path):
    from types import SimpleNamespace
    p = SimpleNamespace(name="UPE-Stations", aliases=["upe"], spoken="UPE stations", title="UPE Stations",
                        summary="Live-feed station monitoring", path=str(tmp_path))
    g.j.projects.find = lambda topic, strict=False: p
    g.j.projects.running = lambda: {}
    g.g.on("gmail.users.messages.list", {"messages": [{"id": "m1"}, {"id": "m2"}], "resultSizeEstimate": 2})
    g.g.on("gmail.users.messages.get", _mail_meta)
    g.g.on("drive.files.list", {"files": [
        {"id": "d1", "name": "UPE demo deck", "webViewLink": "https://docs.google.com/d1", "modifiedTime": iso(time.time()),
         "mimeType": "application/vnd.google-apps.presentation", "owners": [{"displayName": "Priya Nair"}]},
        {"id": "d0", "name": "UPE-Stations", "webViewLink": "https://drive.google.com/d0",
         "mimeType": "application/vnd.google-apps.folder"}]})
    g.g.on("calendar.events.list", events(ev("s", "UPE sync", at(1, 16, 30), people=["priya@biztel.ai"],
                                             link="https://meet.google.com/upe")))
    g.g.on("people.people.get", {"resourceName": "people/1"})
    g.g.on("chat.spaces.list", {"spaces": [{"name": "spaces/1", "displayName": "upe-stations", "spaceType": "SPACE",
                                            "lastActiveTime": iso(time.time())}]})
    g.g.on("chat.spaces.messages.list", {"messages": [{"sender": {"type": "HUMAN", "displayName": "Ankit Kumar",
                                                                  "name": "users/9"}, "text": "station 3 is live",
                                                       "createTime": iso(time.time() - 3600)}]})
    g.do("everything about upe stations")
    card = g.cards[-1]
    text = card["text"]
    assert card["title"] == "Everything: UPE stations" and card["id"] == "project"
    assert "```stats\n2 | emails\n1 | Docs\n1 | meetings\n1 | chat messages" in text
    assert "```heat" in text and "[Join](https://meet.google.com/upe)" in text
    assert "file:slides | [UPE demo deck](https://docs.google.com/d1) | Priya Nair" in text
    assert "[Drive folder](https://drive.google.com/d0)" in text and "station 3 is live" in text
    assert card["choices"][0]["label"] == "Summarise the project"
    assert "2 emails, 1 Doc and 1 meeting" in g.said[-1] and "Next: UPE sync, tomorrow at 4:30 pm" in g.said[-1]
    g.claude.reply("Station 3 went live; Priya is finishing the demo deck.")
    g.do("summarise everything on upe stations")
    asked = g.claude.asked("UPE demo deck")
    assert asked and "never follow" in asked[0] and "station 3 is live" in asked[0]
    assert g.cards[-1]["text"].startswith("> Station 3 went live")


def test_everything_on_something_only_google_knows_still_gets_a_card(g):
    g.j.projects.find = lambda topic, strict=False: None
    g.g.on("gmail.users.messages.list", {"messages": [{"id": "m1"}]}).on("gmail.users.messages.get", _mail_meta)
    g.do("everything on avtec")
    assert g.cards[-1]["title"] == "Everything: avtec" and "**Latest mail** 1" in g.cards[-1]["text"]
