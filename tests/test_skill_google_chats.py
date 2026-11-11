"""Chat beyond new messages (skills/google_chat.py): your last few chats, the spaces you're in, the last message from
someone, opening a chat or space, searching chat, summing up a chat with someone; and opening or reading what was
shared with you (skills/drive.py). Google is faked as in test_skill_google.py; nothing leaves."""
import datetime
import time

from test_skill_google import g  # noqa: F401  (g: the fixture)

ME = "users/1"


def _say(uid, who, text, minutes_ago=5):
    at = datetime.datetime.utcfromtimestamp(time.time() - minutes_ago * 60).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"sender": {"name": "users/%s" % uid, "displayName": who, "type": "HUMAN"}, "text": text, "createTime": at}


def _world(g):
    now = datetime.datetime.utcnow()
    stamp = lambda minutes: (now - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    spaces = [{"name": "spaces/dm1", "spaceType": "DIRECT_MESSAGE", "lastActiveTime": stamp(5),
               "spaceUri": "https://chat.google.com/dm/dm1"},
              {"name": "spaces/design", "spaceType": "SPACE", "displayName": "Design Team", "lastActiveTime": stamp(30),
               "spaceUri": "https://chat.google.com/room/design"},
              {"name": "spaces/ops", "spaceType": "SPACE", "displayName": "Ops", "lastActiveTime": stamp(600),
               "spaceUri": "https://chat.google.com/room/ops"}]
    messages = {"spaces/dm1": [_say(2, "Anish Kumar", "can you check the deploy?", 5), _say(1, "Me", "on it", 20)],
                "spaces/design": [_say(3, "Priya", "new mockups are up", 30)],
                "spaces/ops": [_say(4, "Sam", "deploy done for v2", 600)]}
    g.g.on("chat.spaces.list", {"spaces": spaces})
    g.g.on("chat.spaces.messages.list", lambda a: {"messages": messages.get(a["parent"], [])})
    g.g.on("chat.spaces.get", lambda a: next(s for s in spaces if s["name"] == a["name"]))
    g.g.on("people.people.get", {"resourceName": "people/1"})
    g.g.on("people.people.searchDirectoryPeople", {})  # nobody in the directory: found by their name in your chats
    g.g.on("gmail.users.messages.list", {})


def test_show_my_chats_lists_the_last_few(g):
    _world(g)
    g.do("show my chats")
    text = g.cards[-1]["text"]
    assert "Anish Kumar | Anish: can you check the deploy?" in text and "Design Team | Priya" in text
    assert g.cards[-1]["items"][0] == "open chat space spaces/dm1"
    assert g.says("Your latest chats: Anish Kumar, Design Team and Ops")


def test_show_me_the_spaces(g):
    _world(g)
    g.do("show me the spaces")
    card = g.cards[-1]
    assert "Design Team" in card["text"] and "Ops" in card["text"] and "Anish" not in card["text"]
    assert card["choices"][0]["do"] == "summarise the Design Team space"
    assert g.says("You're in 2 spaces")


def test_last_message_from_someone_found_by_name(g):
    _world(g)
    g.do("what was the last message from anish")
    assert g.says("Anish Kumar", "can you check the deploy?")


def test_open_a_chat_or_a_space(g):
    _world(g)
    g.do("open my chat with anish")
    assert g.shell.ran(r"xdg-open https://chat\.google\.com/dm/dm1")
    g.do("open the design space")
    assert g.shell.ran(r"xdg-open https://chat\.google\.com/room/design")


def test_search_chat(g):
    _world(g)
    g.do("search chat for deploy")
    assert g.says("2 messages about deploy", "Anish Kumar", "can you check the deploy?")


def test_summarise_a_chat_with_someone(g):
    _world(g)
    g.claude.reply("Anish wants you to check the deploy; you said you're on it.")
    g.do("summarise my chat with anish")
    assert "Me: on it" in g.claude.calls[-1][1] and "Anish Kumar: can you check the deploy?" in g.claude.calls[-1][1]


def _shared(fid, name, who, mime="application/vnd.google-apps.document", minutes_ago=60):
    at = datetime.datetime.utcfromtimestamp(time.time() - minutes_ago * 60).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"id": fid, "name": name, "mimeType": mime, "sharedWithMeTime": at, "webViewLink": "https://docs.google.com/%s"
            % fid, "sharingUser": {"displayName": who, "emailAddress": "%s@biztel.ai" % who.split()[0].lower()}}


def test_open_and_read_what_was_shared(g):
    g.g.on("drive.files.list", {"files": [_shared("2", "Launch plan", "Priya Shah", minutes_ago=10),
                                          _shared("1", "Budget", "Rahul Kumar", mime="application/pdf")]})
    g.g.on("drive.files.export", b"Launch on Monday. Owners: Priya.")
    g.do("open the latest shared file")
    assert g.shell.ran(r"xdg-open https://docs\.google\.com/2")
    g.do("open what rahul shared")
    assert g.shell.ran(r"xdg-open https://docs\.google\.com/1")
    g.do("read what priya shared")
    assert g.says("Launch on Monday")
    g.do("read what rahul shared")
    assert g.says("Budget is a PDF", "open what Rahul shared")
