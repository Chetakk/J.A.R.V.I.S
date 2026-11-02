"""50 ways to say "commit and push": each goes to Claude Code in the focused project (skills/dev.py claude_commit),
not a GitHub search, nor "sync contributions"."""
import pytest
from test_skill_dev import project, focus_vscode
PHRASES = """commit everything from this project and push it on github
commit everything and push it
commit and push
commit all my changes and push them
commit this project and push it to github
commit the changes and push
commit my work and push to github
commit everything
commit all changes
make a commit and push it
make a commit
create a commit and push
do a commit and push
git commit and push
git add everything commit and push
stage everything commit and push
add everything commit and push it to github
save my work to github
save everything to github
save this project to github
upload this project to github
upload my changes to github
upload everything to github
put this project on github
put my changes on github
send my changes to github
sync this project with github
sync my changes to github
back up this project to github
push everything to github
push all my changes to github
push this project to github
push my code to github
push the latest changes to github
push everything
commit it
commit this
commit these changes
commit what i've done and push it
commit everything i did today and push it
can you commit everything and push it
please commit and push this project
could you commit my changes and push them to github
i want to commit and push my changes
let's commit and push
go ahead and commit and push
commit everything to git
commit and push to the repo
ship it to github
check in my changes and push them""".splitlines()

@pytest.mark.parametrize("said", PHRASES)
def test_commit_and_push_goes_to_claude_code(jv, said):
    project(jv, "avtec-hs")
    focus_vscode(jv, "avtec-hs")
    jv.do(said, "no")
    assert any(m == "open_terminal" for m, p in jv.claude.calls) and not jv.shell.calls
