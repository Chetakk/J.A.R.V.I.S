"""Downloading from a Drive card ("download the second one", skills/drive.py), then the files skill works on it:
"rename it", "move it to documents", "copy it to desktop". Google is faked as in test_skill_google.py."""
import os

from test_skill_google_find_share import _shared
from test_skill_google import g  # noqa: F401  (the fixture)

PDF = dict(_shared("2", "Invoice.pdf", "Rahul", 5), mimeType="application/pdf")


def card(g):
    g.g.on("drive.files.list", {"files": [_shared("1", "Knowledge Base Schema", "Shubhajit", 1), PDF]})
    g.do("what's been shared with me")


def test_download_the_second_one_then_files_skill(g):
    card(g)
    g.g.on("drive.files.get_media", b"%PDF-1.4 fake")
    g.do("download the second one")
    path = os.path.expanduser("~/Downloads/Invoice.pdf")
    assert open(path, "rb").read() == b"%PDF-1.4 fake" and g.says("Saved Invoice.pdf to Downloads")
    g.do("rename it to october invoice", "yes")
    renamed = os.path.expanduser("~/Downloads/october invoice.pdf")
    assert os.path.exists(renamed) and not os.path.exists(path)
    g.do("copy it to the desktop", "yes")
    assert os.path.exists(os.path.expanduser("~/Desktop/october invoice.pdf")) and os.path.exists(renamed)
    g.do("move it to documents", "yes")
    assert os.path.exists(os.path.expanduser("~/Documents/october invoice.pdf"))


def test_a_google_sheet_downloads_as_xlsx_never_over_a_file(g):
    card(g)
    asked = []
    g.g.on("drive.files.export", lambda a: asked.append(a["mimeType"]) or b"PK sheet")
    g.do("download the first one")
    g.do("download the first one")
    assert asked[0].endswith("spreadsheetml.sheet")
    assert open(os.path.expanduser("~/Downloads/Knowledge Base Schema.xlsx"), "rb").read() == b"PK sheet"
    assert os.path.exists(os.path.expanduser("~/Downloads/Knowledge Base Schema (2).xlsx"))


def test_to_documents_and_out_of_range(g):
    card(g)
    g.g.on("drive.files.get_media", b"x")
    g.do("save the second file to my documents")
    assert os.path.exists(os.path.expanduser("~/Documents/Invoice.pdf"))
    g.do("download the fifth one")
    assert g.says("only 2 on the card")


def test_no_drive_card_is_not_drives(g):
    g.do("download the second one")
    assert not g.g.made("drive.files.get_media") and not g.g.made("drive.files.export")
