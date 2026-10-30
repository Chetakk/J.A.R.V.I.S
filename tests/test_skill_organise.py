"""Organising files (skills/organise.py, system/fileops.py): folders, renames, moves, the Trash, tidying,
duplicates. Nothing is ever overwritten or deleted for good, guesses are checked with you first, and
"undo that" puts it all back."""
import os

import pytest

import conftest

H = conftest.HOME


def make(rel, text="x"):
    path = os.path.join(H, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


def read(rel):
    with open(os.path.join(H, rel)) as f:
        return f.read()


def exists(rel):
    return os.path.exists(os.path.join(H, rel))


def test_make_a_folder_and_undo(jv):
    jv.do("make a folder called invoices in documents")
    assert os.path.isdir(os.path.join(H, "Documents/invoices"))
    assert jv.says("Made invoices in Documents.")
    jv.do("undo that")
    assert not exists("Documents/invoices")


def test_rename_a_named_file_keeps_its_extension(jv):
    make("Documents/report.pdf", "the report")
    jv.do("rename report.pdf to final report")
    assert read("Documents/final report.pdf") == "the report" and not exists("Documents/report.pdf")
    assert not jv.questions  # named outright: nothing to check
    jv.do("undo that")
    assert exists("Documents/report.pdf") and not exists("Documents/final report.pdf")


def test_rename_never_overwrites(jv):
    make("Documents/report.pdf", "new one")
    make("Documents/final.pdf", "keep me")
    jv.do("rename report.pdf to final")
    assert read("Documents/final.pdf") == "keep me"
    assert read("Documents/final (2).pdf") == "new one"


def test_move_a_file_to_a_folder(jv):
    make("Downloads/resume.pdf")
    jv.do("move resume.pdf to documents")
    assert exists("Documents/resume.pdf") and not exists("Downloads/resume.pdf")
    jv.do("undo that")
    assert exists("Downloads/resume.pdf")


def test_trash_asks_first_and_undo_restores(jv):
    make("Downloads/old.txt", "old stuff")
    jv.do("delete old.txt", "yes")
    assert jv.questions == ["Put old.txt in the Trash?"]
    assert not exists("Downloads/old.txt") and exists(".local/share/Trash/files/old.txt")
    jv.do("undo that")
    assert read("Downloads/old.txt") == "old stuff"


@pytest.mark.parametrize("answers", [("no",), ()])
def test_trash_without_a_yes_keeps_it(jv, answers):
    make("Downloads/old.txt")
    jv.do("delete old.txt", *answers)
    assert exists("Downloads/old.txt") and not jv.trashed


def test_trashing_a_folder_says_everything_in_it_goes(jv):
    make("Documents/Old Stuff/a.txt")
    jv.do("delete ~/Documents/Old Stuff", "no")
    assert jv.questions == ["Put the Old Stuff folder and everything in it in the Trash?"]
    assert exists("Documents/Old Stuff/a.txt")


def test_a_folder_by_name_alone_isnt_looked_up_so_nothing_goes(jv):
    make("Documents/Old Stuff/a.txt")
    jv.do("delete the old stuff folder", "yes")  # (only files are found by name; this goes on to Claude)
    assert exists("Documents/Old Stuff/a.txt") and not jv.trashed


def test_tidy_downloads(jv):
    for name in ("cat.png", "talk.mp4", "setup.deb", "paper.pdf", "model.onnx", "notes.xyz", "big.iso.crdownload"):
        make("Downloads/" + name)
    jv.do("tidy my downloads", "yes")
    assert jv.questions[0].startswith("Sort 5 files into")
    for rel in ("Images/cat.png", "Videos/talk.mp4", "Installers/setup.deb", "Documents/paper.pdf", "Models/model.onnx"):
        assert exists("Downloads/" + rel), rel
    assert exists("Downloads/notes.xyz") and exists("Downloads/big.iso.crdownload")  # unknown and unfinished: left
    jv.do("undo that")
    assert exists("Downloads/cat.png") and not exists("Downloads/Images")


def test_tidy_declined_moves_nothing(jv):
    make("Downloads/cat.png")
    jv.do("tidy my downloads", "no")
    assert exists("Downloads/cat.png") and not exists("Downloads/Images")


def test_duplicates_keep_the_original(jv):
    make("Downloads/photo.jpg", "same picture")
    make("Downloads/photo (1).jpg", "same picture")
    make("Downloads/Images/photo copy.jpg", "same picture")
    make("Downloads/other.jpg", "different")
    jv.do("find duplicates in my downloads", "yes")
    assert exists("Downloads/photo.jpg") and exists("Downloads/other.jpg")
    assert not exists("Downloads/photo (1).jpg") and not exists("Downloads/Images/photo copy.jpg")
    assert jv.says("Put 2 copies in the Trash")


def test_never_a_permanent_delete(jv):
    make("Downloads/old.txt")
    jv.do("delete old.txt", "yes")
    assert not jv.shell.ran(r"^rm |shred|unlink")


# --- failure cases ---

def test_the_trash_refusing_is_said_and_the_file_stays(jv):
    make("Downloads/old.txt")
    jv.trash_fails = True
    jv.do("delete old.txt", "yes")
    assert jv.says("I couldn't")
    assert exists("Downloads/old.txt") and not jv.says("It's in the Trash")


def test_undo_after_the_file_changed_since_leaves_it(jv):
    make("Downloads/resume.pdf", "v1")
    jv.do("move resume.pdf to documents")
    make("Downloads/resume.pdf", "a new download with the same name")
    jv.do("undo that")
    assert jv.says("Mostly put back")
    assert read("Downloads/resume.pdf") == "a new download with the same name"  # not overwritten
    assert read("Documents/resume.pdf") == "v1"


def test_a_todo_or_reminder_is_not_a_file(jv):
    jv.do("delete the milk todo")
    assert not any("Trash" in q for q in jv.questions)


def test_a_file_that_isnt_there(jv):
    jv.do("delete taxes2019.pdf")
    assert jv.says("I can't find taxes2019.pdf")
    assert not jv.questions


def test_slashes_in_a_name_are_refused(jv):
    make("Documents/report.pdf")
    jv.do("rename report.pdf to ../../etc/passwd")
    assert jv.said == ["A name can't have a slash in it."]
    assert exists("Documents/report.pdf")


def test_tidy_when_already_tidy(jv):
    jv.do("tidy my downloads")
    assert jv.said == ["Downloads is already tidy: no loose files to sort."]
