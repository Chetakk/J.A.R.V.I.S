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


def test_a_folder_by_name_goes_to_the_trash_after_a_yes(jv):
    make("Documents/Old Stuff/a.txt")
    jv.do("delete the old stuff folder", "yes")
    assert jv.questions == ["Put the Old Stuff folder and everything in it in the Trash?"]
    assert not exists("Documents/Old Stuff") and exists(".local/share/Trash/files/Old Stuff/a.txt")
    jv.do("undo that")
    assert exists("Documents/Old Stuff/a.txt")


@pytest.mark.parametrize("said", ["move the projects folder to documents", "delete the downloads folder",
                                  "rename the documents folder to papers"])
def test_your_main_folders_are_never_moved_or_trashed(jv, said):
    make("Projects/x.txt")
    make("Downloads/y.txt")
    make("Documents/z.txt")
    jv.do(said, "yes")
    assert jv.says("one of your main folders")
    assert exists("Projects/x.txt") and exists("Downloads/y.txt") and exists("Documents/z.txt") and not jv.trashed


def test_copy_a_file_to_a_folder_and_undo(jv):
    make("Downloads/resume.pdf", "cv")
    jv.do("copy resume.pdf to documents")
    assert read("Documents/resume.pdf") == "cv" and read("Downloads/resume.pdf") == "cv"
    assert not jv.questions and jv.says("Copied resume to Documents.")
    jv.do("undo that")
    assert not exists("Documents/resume.pdf") and exists("Downloads/resume.pdf")
    assert jv.trashed == [os.path.join(H, "Documents/resume.pdf")]  # the copy goes to the Trash, never rm
    assert not jv.shell.ran(r"^rm ")


def test_duplicate_makes_a_copy_beside_it(jv):
    make("Documents/report.pdf", "r")
    jv.do("duplicate report.pdf")
    assert read("Documents/report (2).pdf") == "r" and read("Documents/report.pdf") == "r"
    assert jv.says("Made a copy: report (2).pdf.")


def test_copy_a_folder_by_name_asks_first(jv):
    make("Projects/AI-Supervisor/plan.md", "plan")
    jv.do("copy the ai supervisor folder to documents", "yes")
    assert jv.questions == ["Copy AI-Supervisor to Documents?"]
    assert read("Documents/AI-Supervisor/plan.md") == "plan" and exists("Projects/AI-Supervisor/plan.md")


def test_a_folder_is_never_copied_into_itself(jv):
    make("Documents/Work/Sub/x.txt")
    jv.do("copy the work folder to the sub folder", "yes")
    assert jv.says("I can't put a folder inside itself.")
    assert not exists("Documents/Work/Sub/Work")


def test_the_file_manager_flow(jv):
    """The flow asked for: "open projects", "create ai supervisor", "open it", then a folder made inside it, and
    "move ai supervisor to documents" by the name just heard."""
    os.makedirs(os.path.join(H, "Projects"), exist_ok=True)
    jv.do("open the projects folder")
    jv.do("create ai supervisor")
    assert os.path.isdir(os.path.join(H, "Projects/ai supervisor"))
    assert jv.says("Made ai supervisor in Projects.")
    jv.clear()
    jv.do("open it")
    assert jv.shell.ran(r"^xdg-open .*Projects/ai\\? supervisor")
    jv.do("make a folder called notes")
    assert os.path.isdir(os.path.join(H, "Projects/ai supervisor/notes"))
    jv.do("open the projects folder")
    jv.clear()
    jv.do("move ai supervisor to documents")
    assert not jv.questions  # picked by the name JARVIS just read out: nothing to check
    assert os.path.isdir(os.path.join(H, "Documents/ai supervisor/notes")) and not exists("Projects/ai supervisor")


def test_create_without_an_open_folder_is_not_a_folder(jv):
    jv.do("create ai supervisor")
    jv.do("create a summary of this page")
    assert not jv.says("Made") and not exists("Documents/ai supervisor")


def test_move_it_here_goes_to_the_folder_files_shows(jv):
    make("Downloads/report.pdf")
    os.makedirs(os.path.join(H, "Projects/AI-Process-Engineer"))
    jv.desktop.add("AI-Process-Engineer", "org.gnome.Nautilus", url=os.path.join(H, "Projects/AI-Process-Engineer"))
    jv.do("move report.pdf here")
    assert exists("Projects/AI-Process-Engineer/report.pdf") and not exists("Downloads/report.pdf")
    jv.do("copy the latest download here")  # (now the one just moved, so a copy beside it)
    jv.do("make a folder called drafts")
    assert os.path.isdir(os.path.join(H, "Projects/AI-Process-Engineer/drafts"))


def test_move_all_pdfs_in_downloads_and_undo(jv):
    make("Downloads/a.pdf")
    make("Downloads/Docs/b.pdf")
    make("Downloads/c.png")
    jv.do("move all pdfs in downloads to documents", "yes")
    assert jv.questions == ["Move 2 pdfs from Downloads to Documents?"]
    assert exists("Documents/a.pdf") and exists("Documents/b.pdf") and exists("Downloads/c.png")
    jv.do("undo that")
    assert exists("Downloads/a.pdf") and exists("Downloads/Docs/b.pdf") and not exists("Documents/a.pdf")


def test_delete_all_zips_declined_trashes_nothing(jv):
    make("Downloads/a.zip")
    make("Downloads/b.zip")
    jv.do("delete all zips in downloads", "no")
    assert jv.questions == ["Put 2 zips from Downloads in the Trash?"]
    assert exists("Downloads/a.zip") and not jv.trashed


def test_delete_the_second_one_can_be_a_folder(jv):
    make("Documents/Alpha/x.txt")
    make("Documents/Beta/y.txt")
    jv.do("what's in documents")
    jv.do("delete the second one", "yes")
    assert jv.questions == ["Put the Beta folder and everything in it in the Trash?"]
    assert not exists("Documents/Beta") and exists("Documents/Alpha")


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


def test_folders_from_the_log_go_where_you_are(jv):
    """Seen in the log: after "open projects folder", "make a new folder here" made it in Documents, "put it in
    the projects folder" opened Projects instead of moving it, and "make a new folder in projects folder which is
    called ai supervisor" went to Claude and nothing was made."""
    os.makedirs(os.path.join(H, "Projects"), exist_ok=True)
    jv.do("open the projects folder")
    jv.do("make a new folder here", "call it process engineer")
    assert os.path.isdir(os.path.join(H, "Projects/process engineer")) and not exists("here")
    jv.do("make a folder called notes")  # still in the folder you opened
    assert os.path.isdir(os.path.join(H, "Projects/notes"))
    jv.do("make a new folder in projects folder which is called ai supervisor")
    assert os.path.isdir(os.path.join(H, "Projects/ai supervisor"))


def test_put_it_in_a_folder_moves_what_was_just_made(jv):
    jv.do("make a folder called invoices in documents")
    jv.do("put it in the projects folder", "yes")
    assert jv.questions == ["Move invoices to Projects?"]
    assert os.path.isdir(os.path.join(H, "Projects/invoices")) and not exists("Documents/invoices")
