"""Copying or moving into a project by part of its name ("the plant intelligence folder": ~/Projects/Plant
Intelligence System), for every project, not one by one; a start several projects share is asked, never made."""
import os

import conftest
import pytest

PROJECTS = ["Plant Intelligence System", "day-tracker", "vertex-camera", "study-budd", "avtec-hs", "avtec-circlip",
            "UPE-Stations", "UPE-blockline"]


@pytest.fixture
def home(jv):
    for name in PROJECTS:
        os.makedirs(os.path.join(conftest.HOME, "Projects", name, ".git"))
    jv.j.projects.refresh()
    os.makedirs(os.path.join(conftest.HOME, "Downloads"), exist_ok=True)
    with open(os.path.join(conftest.HOME, "Downloads", "report.pdf"), "w") as f:
        f.write("pdf")
    return jv


def inside(name, file="report.pdf"):
    return os.path.exists(os.path.join(conftest.HOME, "Projects", name, file))


@pytest.mark.parametrize("said, project", [
    ("plant intelligence", "Plant Intelligence System"),
    ("plant", "Plant Intelligence System"),
    ("the plant intelligence system", "Plant Intelligence System"),
    ("day tracker", "day-tracker"),
    ("vertex", "vertex-camera"),
    ("study", "study-budd"),
    ("avtec hs", "avtec-hs"),
    ("upe stations", "UPE-Stations"),
])
def test_copy_the_latest_download_into_a_project_by_part_of_its_name(home, said, project):
    home.do("copy report.pdf to the %s folder" % said, "yes")
    assert inside(project), home.said
    assert not home.says("Make one")


def test_move_works_the_same(home):
    home.do("move report.pdf to the plant intelligence folder", "yes")
    assert inside("Plant Intelligence System")
    assert not os.path.exists(os.path.join(conftest.HOME, "Downloads", "report.pdf"))


@pytest.mark.parametrize("said, pick, project", [("avtec", "avtec-circlip", "avtec-circlip"),
                                                 ("upe", "UPE-blockline", "UPE-blockline")])
def test_a_start_several_projects_share_is_asked(home, said, pick, project):
    home.do("copy report.pdf to the %s folder" % said, pick, "yes")
    assert any("Which one" in q for q in home.questions), home.questions
    assert inside(project) and not home.says("Make one")


def test_a_folder_that_is_no_project_is_still_offered_to_make(home):
    home.do("copy report.pdf to the zebra notes folder", "no")
    assert home.says("no zebra notes folder")


@pytest.mark.parametrize("said", ["copy the latest download in the plant intelligence folder",
                                  "copy my latest download to plant intelligence"])
def test_the_latest_download_as_said(home, said):
    home.do(said, "yes")
    assert inside("Plant Intelligence System"), home.said
