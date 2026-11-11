"""Other ways of asking the Google skills that carry a value (a time, a day, a name): "is 3 o'clock free", "can I do
3 pm tomorrow", "book time with Rahul tomorrow at 3", "did Rahul message me". Google is faked as in
test_skill_google.py; nothing leaves."""
import datetime

from test_skill_google import at, ev, events, g  # noqa: F401  (g: the fixture)


def test_other_ways_to_ask_if_youre_free(g):
    g.g.on("calendar.events.list", events(ev("a", "Design review", at(1, 15), minutes=60)))
    for asked in ("is 3 o'clock tomorrow free", "can i do 3 pm tomorrow", "do i have anything at 3 tomorrow",
                  "is my calendar free at 3 tomorrow"):
        g.clear()
        g.do(asked)
        assert g.says("No, tomorrow at 3 pm you have Design review"), (asked, g.said)
    g.clear()
    g.do("can i do 11 am tomorrow")
    assert g.says("Yes, you're free tomorrow at 11 am")


def test_free_time_in_other_words_keeps_the_day_and_part(g):
    g.g.on("calendar.events.list", events(ev("a", "Lunch talk", at(1, 10), minutes=60)))
    g.do("when do i have time tomorrow morning")
    assert g.says("Tomorrow morning you're free 9 am to 10 am and 11 am to 12 pm"), g.said


def test_booking_time_in_other_words(g):
    g.g.on("people.people.searchDirectoryPeople", {"people": [{"emailAddresses": [{"value": "rahul@biztel.ai"}]}]})
    g.do("put a meeting with rahul in my calendar tomorrow at 3", "yes")
    made = g.g.made("calendar.events.insert")[0]
    assert made["body"]["attendees"] == [{"email": "rahul@biztel.ai"}]
    assert made["body"]["summary"] == "Meeting with Rahul"
    assert made["body"]["start"]["dateTime"].startswith(datetime.date.fromtimestamp(at(1, 0)).isoformat() + "T15:00")


def test_did_someone_message_me_on_chat(g):
    g.g.on("people.people.searchDirectoryPeople", {"people": [{"emailAddresses": [{"value": "rahul@biztel.ai"}]}]})
    g.g.on("chat.spaces.findDirectMessage", {"name": "spaces/dm"})
    g.g.on("people.people.get", {"resourceName": "people/1"})
    g.g.on("chat.spaces.messages.list", {"messages": [{"sender": {"name": "users/2", "displayName": "Rahul",
                                                                  "type": "HUMAN"}, "text": "ship it",
                                                       "createTime": "2026-11-11T10:00:00Z"}]})
    g.do("did rahul message me")
    assert g.says("Rahul", "ship it")


def test_what_is_it_saying_is_not_a_person_on_chat(g):
    g.do("what is it saying")
    assert not g.g.made("chat.spaces.findDirectMessage")
