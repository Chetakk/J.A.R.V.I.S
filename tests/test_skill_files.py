"""Files (skills/files.py): downloads by when, the latest one, finding by name, opening; file names are
shell-quoted, so a strange name can never run as a command."""
import datetime
import os
import shlex
import time

import pytest

import conftest

DL = os.path.join(conftest.HOME, "Downloads")
DOCS = os.path.join(conftest.HOME, "Documents")


def make(path, when=None, text="x"):
    """A file at `path` (under the temp HOME), last modified at `when` (epoch seconds; default now)."""
    path = os.path.join(conftest.HOME, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if when:
        os.utime(path, (when, when))
    return path


def today_at(hour):
    """Today at that hour, but never in the future (the tests may run just after midnight)."""
    t = datetime.datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()
    return min(t, time.time() - 60)


YESTERDAY = (datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
             - datetime.timedelta(days=1)).timestamp()


def opened(jv):
    return [shlex.split(c)[1] for c in jv.shell.ran(r"^xdg-open ")]


def test_what_did_i_download_today(jv):
    make("Downloads/invoice-oct.pdf", today_at(1))
    make("Downloads/Pictures/cat.png", time.time() - 30)  # sorted into a folder: still counts
    make("Downloads/old-thing.zip", YESTERDAY)
    jv.do("what did I download today")
    assert jv.said == ["Today you downloaded cat and invoice oct."] or (
        jv.says("Today you downloaded") and not jv.says("old"))
    assert "old-thing.zip" not in jv.card_text()


def test_downloads_from_yesterday(jv):
    make("Downloads/invoice-oct.pdf", time.time() - 30)
    make("Downloads/old-thing.zip", YESTERDAY)
    jv.do("show me my downloads from yesterday")
    assert "old-thing.zip" in jv.card_text() and "invoice" not in jv.card_text()


def test_open_the_pdf_i_downloaded(jv):
    pdf = make("Downloads/boarding pass.pdf", time.time() - 60)
    make("Downloads/song.mp3", time.time() - 30)
    jv.do("open the pdf I downloaded")
    assert opened(jv) == [pdf]


def test_open_my_latest_download(jv):
    make("Downloads/a.txt", time.time() - 600)
    newest = make("Downloads/b.txt", time.time() - 5)
    jv.do("open my latest download")
    assert opened(jv) == [newest]
    assert jv.says("Opening b.txt.")


def test_find_a_file_by_name_and_open_after_yes(jv):
    cv = make("Documents/Work/Chetak_Resume_2026.pdf")
    jv.do("find my resume", "yes")
    assert jv.questions[0] == "I found Chetak_Resume_2026.pdf in Documents, Work. Want me to open it?"
    assert opened(jv) == [cv]


def test_find_then_no_opens_nothing(jv):
    make("Documents/Work/Chetak_Resume_2026.pdf")
    jv.do("find my resume", "no")
    assert not opened(jv)


def test_list_files_by_kind(jv):
    make("Documents/notes.md")
    make("Projects/x/README.md")
    make("Documents/budget.xlsx")
    jv.do("find my markdown files")
    assert "notes.md" in jv.card_text() and "README.md" in jv.card_text() and "budget" not in jv.card_text()


# --- failure cases ---

def test_nothing_downloaded_today(jv):
    make("Downloads/old-thing.zip", YESTERDAY)
    jv.do("what did I download today")
    assert jv.said == ["You didn't download anything today."]


def test_empty_downloads_folder(jv):
    jv.do("open my latest download")
    assert jv.said == ["There's nothing in Downloads."]
    assert not opened(jv)


def test_a_file_that_is_gone(jv):
    jv.do("open file ~/Downloads/gone.pdf")
    assert jv.said == ["That file isn't there any more."]
    assert not opened(jv)


def test_unfinished_downloads_are_not_offered(jv):
    make("Downloads/big.iso.crdownload", time.time() - 5)
    jv.do("what did I download today")
    assert jv.said == ["You didn't download anything today."]


@pytest.mark.parametrize("name", ["it's $(touch HACKED) a test.pdf", "x; rm -rf ~.pdf", "`id`.pdf"])
def test_strange_file_names_are_quoted_not_run(jv, name):
    path = make("Downloads/" + name, time.time() - 5)
    jv.do("open my latest download")
    ran = jv.shell.ran(r"^xdg-open ")
    # Either opened with the name quoted, or (for $( and backticks) left in the manual terminal for you.
    assert ran == ["xdg-open " + shlex.quote(path)] or (not ran and jv.manual[0][0] == "xdg-open " + shlex.quote(path))
    assert not jv.shell.ran(r"^touch|^id$|^rm ")


def test_where_is_a_place_is_not_a_disk_search(jv):
    jv.do("where is the eiffel tower")
    assert not jv.questions and not jv.cards
    assert jv.claude.asked("eiffel tower")


def test_a_name_with_no_file_hands_on(jv):
    make("Documents/budget.xlsx")
    jv.do("find my passport scan")
    assert not opened(jv)
    assert not jv.says("I found")
