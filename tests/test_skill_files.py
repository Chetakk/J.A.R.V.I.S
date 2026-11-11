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


def test_say_the_number_of_a_file_past_the_buttons(jv):
    for n in range(1, 10):
        make("Documents/Stuff/file%d.txt" % n)
    jv.do("what's in my stuff folder")
    assert "9. {file:txt} **file9.txt**" in jv.card_text()
    jv.do("eight")
    assert opened(jv) and "file8.txt" in opened(jv)[-1]


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


def test_copy_the_last_download_and_paste_it_here(jv):
    H = conftest.HOME
    """Seen in the log: "copy the last download" opened it instead (it only knew "open my latest download")."""
    os.makedirs(os.path.join(H, "Downloads"), exist_ok=True)
    with open(os.path.join(H, "Downloads/design notes.txt"), "w") as f:
        f.write("x")
    jv.desktop.add("AI-Process-Engineer", "org.gnome.Nautilus Org.gnome.Nautilus")
    jv.do("copy the last download and paste it here")
    copied = [t for line, t in jv.shell.inputs if "gnome-copied-files" in line]
    assert copied == ["copy\nfile://%s/Downloads/design%%20notes.txt" % H]
    assert jv.desktop.did("keys") == ["ctrl+v"] and not jv.shell.ran("xdg-open")
    assert jv.says("Pasted design notes.txt")


# --- the file manager: open, where, which, how big -----------------------------------------------------------

def test_open_a_file_by_its_name(jv):
    cv = make("Documents/Work/resume.pdf")
    jv.do("open resume.pdf")
    assert opened(jv) == [cv]
    assert not jv.shell.ran("https://")  # (it once went to the browser as https://resume.pdf)


def test_open_my_resume_opens_the_newest(jv):
    make("Documents/resume_old.pdf", time.time() - 9000)
    new = make("Documents/resume_2026.pdf", time.time() - 60)
    jv.do("open my resume")
    assert opened(jv) == [new]


def test_open_it_after_finding(jv):
    cv = make("Documents/Work/Chetak_Resume_2026.pdf")
    jv.do("find my resume", "no")
    jv.do("open it")
    assert opened(jv) == [cv]


def test_open_a_name_just_listed(jv):
    make("Projects/AI-Supervisor/a.txt")
    jv.do("open the projects folder")
    jv.clear()
    jv.do("open ai supervisor")
    assert opened(jv) == [os.path.join(conftest.HOME, "Projects/AI-Supervisor")]


def test_open_a_file_that_isnt_there(jv):
    jv.do("open taxes2019.pdf")
    assert jv.said == ["I can't find taxes2019.pdf."] and not opened(jv)


def test_where_is_a_folder(jv):
    folder = make("Projects/AI-Supervisor/a.txt").rsplit("/", 1)[0]
    jv.do("where is the ai supervisor folder", "yes")
    assert jv.questions == ["AI-Supervisor is in Projects. Want me to open it?"]
    assert opened(jv) == [folder]


def test_which_folder_is_a_file_in(jv):
    make("Documents/Work/resume.pdf")
    jv.do("which folder is resume.pdf in")
    assert jv.said == ["resume.pdf is in Documents, Work."]


def test_how_big_is_a_file_and_a_folder(jv):
    make("Documents/report.pdf", text="x" * 2048)
    make("Documents/Work/a.txt", text="y" * 1024)
    jv.do("how big is report.pdf")
    assert jv.says("report.pdf is 2.0 KB, last changed today")
    jv.do("how big is the work folder")
    assert jv.says("Work has 1 file, 1.0 KB in all")


def test_how_big_is_the_sun_is_not_a_file(jv):
    jv.do("how big is the sun")
    assert not jv.says("KB") and jv.claude.asked("sun")
