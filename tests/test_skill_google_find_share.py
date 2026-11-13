"""Finding email and saving its attachments (skills/gmail.py), and files shared with you or Doc comments said
(skills/drive.py, skills/gmail.py). Google is faked as in test_skill_google.py; nothing leaves."""
import base64
import datetime
import os
import time

from skills.gmail import search_query
from test_skill_google import _msg, drain, g  # noqa: F401  (g: the fixture)


def _with_file(msg, name, content):
    msg["payload"] = {"mimeType": "multipart/mixed", "headers": msg["payload"]["headers"], "parts": [
        msg["payload"], {"filename": name, "mimeType": "application/pdf", "body": {"attachmentId": "att1"}}]}
    data = base64.urlsafe_b64encode(content).decode()
    return msg, data


def test_search_query():
    day = datetime.date(2026, 11, 11)
    assert search_query("invoice from last month", day) == "invoice after:2026/10/01 before:2026/11/01"
    assert search_query("from rahul about the budget with a pdf", day) == \
        'budget has:attachment filename:pdf from:"rahul"'
    assert search_query("sent to sam yesterday", day) == 'after:2026/11/10 before:2026/11/11 to:"sam"'
    assert search_query("the one from priya in october", day) == 'after:2026/10/01 before:2026/11/01 from:"priya"'
    assert search_query("the", day) == ""


def test_find_an_email_then_save_its_attachment(g):
    msg, data = _with_file(_msg("1", "Rahul <rahul@biztel.ai>", "Invoice October", "Attached."), "invoice.pdf",
                           b"%PDF-1.4 fake")
    asked = []
    g.g.on("gmail.users.messages.list", lambda a: asked.append(a["q"]) or {"messages": [{"id": "1"}]})
    g.g.on("gmail.users.messages.get", lambda a: msg)
    g.g.on("gmail.users.messages.attachments.get", {"data": data})
    g.do("find the invoice email from last month")
    assert asked[0].startswith("invoice after:")
    assert g.says("Found one", "Rahul", "Invoice October", "invoice.pdf", "save the attachment")
    g.do("save the attachment")
    path = os.path.expanduser("~/Downloads/invoice.pdf")
    assert open(path, "rb").read() == b"%PDF-1.4 fake"
    assert g.says("Saved invoice.pdf to Downloads")
    g.do("save the attachment")  # again: next to it, never over it
    assert open(os.path.expanduser("~/Downloads/invoice (2).pdf"), "rb").read() == b"%PDF-1.4 fake"


def test_an_attachment_name_cannot_leave_the_folder(g):
    msg, data = _with_file(_msg("1", "x@y.com", "Hi"), "../../.bashrc", b"echo pwned")
    g.g.on("gmail.users.messages.list", {"messages": [{"id": "1"}]}).on("gmail.users.messages.get", lambda a: msg)
    g.g.on("gmail.users.messages.attachments.get", {"data": data})
    g.do("search my email for hi")
    g.do("save the attachment")
    assert os.path.exists(os.path.expanduser("~/Downloads/bashrc"))
    assert not os.path.exists(os.path.expanduser("~/.bashrc")) or b"pwned" not in open(
        os.path.expanduser("~/.bashrc"), "rb").read()


def test_save_the_file_without_an_email_is_not_gmails(g):
    g.do("save the attachment")
    assert not g.g.made("gmail.users.messages.get")


def _shared(fid, name, who, minutes_ago):
    at = datetime.datetime.utcfromtimestamp(time.time() - minutes_ago * 60).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"id": fid, "name": name, "mimeType": "application/vnd.google-apps.spreadsheet", "sharedWithMeTime": at,
            "webViewLink": "https://docs.google.com/%s" % fid,
            "sharingUser": {"displayName": who, "emailAddress": "%s@biztel.ai" % who.split()[0].lower()}}


def test_a_newly_shared_file_is_said_once(g):
    old = _shared("1", "Old plan", "Priya", 600)
    g.g.on("drive.files.list", {"files": [old]})
    drive = g.j.skills.drive
    drive.poll()
    assert not drain(g)
    g.g.on("drive.files.list", {"files": [_shared("2", "Budget 2026", "Rahul Kumar", 1), old]})
    drive.poll()
    drive.poll()
    items = drain(g)
    assert [x for k, x in items if k == "say"] == ["Rahul shared Budget 2026, a sheet, with you."]
    card = [x["show"] for k, x in items if k == "ctl"][0]
    assert "[Budget 2026](https://docs.google.com/2)" in card["text"]


def test_whats_been_shared_with_me(g):
    g.g.on("drive.files.list", {"files": [_shared("2", "Budget 2026", "Rahul Kumar", 60)]})
    g.do("what's been shared with me")
    assert g.says("One file this week", "Rahul shared Budget 2026")


def test_a_doc_comment_is_said(g):
    comment = _msg("9", '"Rahul Kumar (Google Docs)" <comments-noreply@docs.google.com>', "Budget 2026",
                   "Rahul mentioned you")
    seen = [{"messages": []}, {"messages": [{"id": "9"}]}]
    g.g.on("gmail.users.messages.list", lambda a: seen.pop(0) if seen else {"messages": [{"id": "9"}]})
    g.g.on("gmail.users.messages.get", lambda a: comment).on("gmail.users.getProfile", {"emailAddress": "me@b.ai"})
    gm = g.j.skills.gmail
    gm.poll()
    gm.poll()
    assert [x for k, x in drain(g) if k == "say"] == ["Rahul on Google Docs: Budget 2026."]


def test_a_file_shared_while_jarvis_was_off_is_still_said(g):
    old = _shared("1", "Old plan", "Priya", 600)
    g.g.on("drive.files.list", {"files": [old]})
    g.j.skills.drive.poll()
    g.g.on("drive.files.list", {"files": [_shared("2", "Budget 2026", "Rahul Kumar", 1), old]})
    type(g.j.skills.drive)(g.j).poll()  # a fresh start reads the last poll from disk
    assert [x for k, x in drain(g) if k == "say"] == ["Rahul shared Budget 2026, a sheet, with you."]
