"""Notes, preferences and secrets (skills/notes.py): what you tell JARVIS is kept, found again, changed and
forgotten on request; secrets go to the keyring, never into notes or to Claude; private things never reach Claude."""
import json
import os

import pytest

from skills import notes


def saved_notes():
    return notes.load_notes()


def test_remember_and_recall_a_note(jv):
    jv.do("remember that the spare key is under the blue pot")
    assert jv.says("I'll remember that") or jv.says("noted")
    assert [n["text"] for n in saved_notes()] == ["the spare key is under the blue pot"]
    jv.clear()
    jv.do("what do you remember about the spare key")
    assert jv.said == ["You told me the spare key is under the blue pot."]
    assert not jv.claude.calls


def test_a_note_answers_a_question_without_claude(jv):
    jv.do("remember that my car is parked on level 2")
    jv.clear()
    jv.do("where did I park my car")
    assert jv.says("level 2")
    assert not jv.claude.calls


def test_list_notes(jv):
    for n in ("the wifi box is in the hall", "raj's birthday is in june"):
        jv.do("remember that " + n)
    jv.clear()
    jv.do("list my notes")
    assert jv.said[0].startswith("You've asked me to remember 2 things.")
    assert "the wifi box is in the hall" in jv.card_text()


def test_undo_forgets_what_was_just_saved(jv):
    jv.do("remember that the meeting moved to room 4")
    jv.do("undo that")
    assert saved_notes() == []


def test_remember_to_is_a_reminder_not_a_note(jv):
    jv.do("remember to call mom in 10 minutes")
    assert saved_notes() == []


def test_preference_with_a_topic_is_replaced_not_duplicated(jv):
    jv.do("remember that my favourite editor is vim")
    jv.do("remember that my favourite editor is emacs")
    prefs = jv.j.memory.preferences()
    assert [p["value"] for p in prefs] == ["my favourite editor is emacs"]
    assert jv.says("I've updated that", "your favourite editor is emacs")


def test_said_in_passing_asks_before_saving(jv):
    jv.do("I prefer battery life over performance", "yes")
    assert jv.questions[0] in ("Want me to remember that you prefer battery life over performance?",
                               "Should I remember that you prefer battery life over performance?")
    assert [p["value"] for p in jv.j.memory.preferences()] == ["I prefer battery life over performance"]


@pytest.mark.parametrize("answer", ["no", None])
def test_said_in_passing_and_not_confirmed_is_not_saved(jv, answer):
    jv.do("I prefer battery life over performance", *([answer] if answer else []))
    assert jv.j.memory.preferences() == []


def test_forget_a_note_after_yes(jv):
    jv.do("remember that the spare key is under the blue pot")
    jv.do("forget the spare key", "yes")
    assert saved_notes() == []


def test_forget_a_note_kept_after_no(jv):
    jv.do("remember that the spare key is under the blue pot")
    jv.do("forget the spare key", "no")
    assert len(saved_notes()) == 1


def test_forget_everything_needs_a_yes(jv):
    jv.do("remember that a is 1")
    jv.do("remember that b is 2")
    jv.do("forget everything")  # no answer
    assert len(saved_notes()) == 2
    jv.do("forget everything", "yes")
    assert saved_notes() == []


def test_about_me_when_empty(jv):
    jv.do("what do you know about me")
    assert jv.said == ["I don't know much about you yet. Tell me what you prefer, or say “remember that ...”."]


# --- secrets ---

def test_a_secret_goes_to_the_keyring_not_notes(jv):
    jv.do("remember my locker code is 4512")
    assert jv.keyring.items == {"locker code": "4512"}
    assert saved_notes() == []
    assert jv.says("locked your locker code in your keyring")
    assert not any("4512" in line for line in jv.said)


def test_a_secret_is_shown_not_spoken(jv):
    jv.keyring.items["locker code"] = "4512"
    jv.do("what's my locker code")
    assert "4512" in jv.card_text()
    assert not any("4512" in line for line in jv.said)
    assert jv.says("on your screen for 30 seconds")


def test_secrets_never_reach_claude(jv):
    jv.do("remember my locker code is 4512")
    jv.do("what's my locker code")
    jv.do("write me a poem about lockers")
    assert jv.claude.calls
    assert not any("4512" in prompt for _, prompt in jv.claude.calls)


def test_without_a_keyring_a_secret_is_a_private_note_and_says_so(jv):
    jv.keyring.available = False
    jv.do("remember my locker code is 4512")
    assert jv.says("isn't encrypted")
    assert saved_notes()[0].get("private") is True


def test_a_zip_code_question_isnt_a_secret(jv):
    jv.keyring.items["locker code"] = "4512"
    jv.do("what's my zip code")
    assert "4512" not in jv.card_text()


# --- private ---

def test_private_notes_never_reach_claude(jv):
    jv.do("remember privately that I see the doctor on friday about my back")
    assert saved_notes()[0]["private"] is True
    assert jv.says("kept private")
    jv.do("what should I do this weekend")
    assert jv.claude.calls
    assert not any("doctor" in prompt for _, prompt in jv.claude.calls)


def test_public_notes_that_matter_are_passed_to_claude(jv):
    jv.do("remember that I'm vegetarian")
    jv.do("what should I cook for dinner tonight")
    assert any("vegetarian" in prompt for _, prompt in jv.claude.calls)


# --- failure cases ---

def test_unreadable_notes_file_doesnt_crash(jv):
    with open(notes.NOTES_FILE, "w") as f:
        f.write("{not json")
    jv.do("list my notes")
    assert jv.said == ["You haven't asked me to remember anything yet."]


@pytest.mark.xfail(strict=True, reason="DATA LOSS: a notes.json that fails to parse is read as empty, and the "
                                       "next save overwrites it with just the new note")
def test_a_damaged_notes_file_is_not_overwritten(jv):
    with open(notes.NOTES_FILE, "w") as f:
        f.write('[{"text": "old note 1", "at": 1}, {"text": "old note 2", "at": 2},')  # cut off mid-write
    jv.do("remember that the new note is here")
    with open(notes.NOTES_FILE) as f:
        assert "old note 1" in f.read() or any(
            "old note" in open(os.path.join(os.path.dirname(notes.NOTES_FILE), n)).read()
            for n in os.listdir(os.path.dirname(notes.NOTES_FILE)) if n.startswith("notes") and n != "notes.json")


def test_recall_of_something_never_said_goes_to_claude(jv):
    jv.do("remember that the spare key is under the blue pot")
    jv.clear()
    jv.do("do you remember the french revolution")
    assert not jv.says("You told me")
