"""Installing software (skills/install.py, system/packages.py): looked up read-only, then the sudo apt / pip
command is only ever pre-typed in your terminal; nothing installs or uninstalls by itself."""
import pytest

VLC = ("Package: vlc\nVersion: 3.0.9.2-1\nDescription-en: multimedia player and streamer\n"
       " VLC is the VideoLAN project's media player.\n")


@pytest.fixture
def apt(jv):
    jv.shell.on(r"^dpkg-query ", code=1)  # nothing installed...
    jv.shell.missing.update({"vlc", "gimp", "requests", "obs-studio", "ffmpeg", "blender", "zoom", "photo-editor"})  # ...or on the PATH
    jv.shell.on(r"^apt-cache show ", code=100)  # (rules added later win: the general ones go first)
    jv.shell.on(r"^apt-cache search ", out="")
    jv.shell.on(r"^apt-cache show --no-all-versions vlc$", out=VLC)
    jv.shell.on(r"^pip3 show ", code=1)
    return jv


def installs_run(jv):
    return jv.shell.ran(r"^(sudo|apt|apt-get|dpkg) |^pip3? (install|uninstall)")


def test_install_is_pretyped_in_your_terminal_never_run(jv, apt):
    jv.do("install vlc")
    assert jv.says("That's vlc, version 3.0.9.2: multimedia player and streamer.")
    assert jv.manual[0][0] == "sudo apt install vlc"
    assert not installs_run(jv)
    assert "sudo apt install vlc" in jv.card_text()


def test_already_installed(jv, apt):
    jv.shell.on(r"^dpkg-query .* vlc$", out="install ok installed 3.0.9.2-1")
    jv.do("install vlc")
    assert jv.said == ["vlc is already installed, version 3.0.9.2."]
    assert not jv.manual


def test_several_matches_ask_which(jv, apt):
    jv.shell.on(r"^apt-cache search --names-only .*photo",
                out="photo-editor-pro - edits photos\nphoto-editor - a photo editor\nphoto-editor-data - its data\n")
    jv.shell.on(r"^apt-cache show --no-all-versions photo-editor-pro$", out="Version: 1.2\nDescription-en: pro\n")
    jv.do("install photo editor", "the second one")
    assert jv.questions[0] == "I found photo-editor or photo-editor-pro. Which one?"  # (-data: a part, left out)
    assert jv.manual[0][0] == "sudo apt install photo-editor-pro"


def test_several_matches_and_no_answer_installs_nothing(jv, apt):
    jv.shell.on(r"^apt-cache search --names-only .*photo", out="photo-editor-pro - x\nphoto-editor-lite - y\n")
    jv.do("install photo editor")
    assert jv.says("Okay, I won't install anything.") and not jv.manual


def test_not_in_apt_offers_ubuntu_software_first(jv, apt):
    jv.do("install zoom", "yes")
    assert jv.questions == ["zoom isn't in the Ubuntu packages. Look for it in Ubuntu Software?"]
    assert jv.shell.ran(r"^gnome-software --search=zoom$")


def test_python_packages_go_in_for_you_only(jv, apt):
    jv.do("pip install requests")
    assert jv.manual[0][0] == "pip3 install --user requests"
    assert not installs_run(jv)


def test_uninstall_is_pretyped_too(jv, apt):
    jv.shell.on(r"^dpkg-query .* gimp$", out="install ok installed 2.10.18-1")
    jv.do("uninstall gimp")
    assert jv.manual[0][0] == "sudo apt remove gimp"
    assert jv.says("your own settings for it are kept")


def test_is_it_installed(jv, apt):
    jv.shell.on(r"^dpkg-query .* ffmpeg$", out="install ok installed 7:4.2.7-0ubuntu0.1")
    jv.do("is ffmpeg installed")
    assert jv.said == ["Yes, ffmpeg is installed, version 4.2.7."]
    jv.do("is gimp installed")
    assert jv.says("No, gimp isn't installed.")


# --- failure cases ---

def test_uninstalling_something_not_installed(jv, apt):
    jv.do("uninstall gimp")
    assert jv.said == ["I can't find an installed package called gimp."]
    assert not jv.manual


@pytest.mark.parametrize("text", ["install vlc; rm -rf ~", "install $(rm -rf ~)", "install vlc && sudo reboot"])
def test_a_name_with_shell_in_it_never_runs_it(jv, apt, text):
    jv.do(text, "yes")
    assert not jv.shell.ran(r"(^|[;&|] *)(rm|reboot|sudo)\b") or all(
        "'" in c for c in jv.shell.ran(r"rm|reboot"))  # only ever inside quotes
    assert not installs_run(jv)


def test_a_bad_pip_name(jv, apt):
    jv.do("pip install requests==2; rm")
    assert not jv.manual or not any("rm" in c for c, _ in jv.manual)


def test_install_the_dependencies_is_a_projects_not_the_system(jv, apt):
    jv.do("install the dependencies")
    assert not jv.manual and not jv.shell.ran(r"^apt-cache")


def test_apt_cache_missing_doesnt_crash(jv):
    jv.shell.on(r"^(dpkg-query|apt-cache) ", raises=FileNotFoundError)
    jv.shell.missing.add("vlc")
    jv.do("install vlc")
    assert jv.questions and "Ubuntu Software" in jv.questions[0]
