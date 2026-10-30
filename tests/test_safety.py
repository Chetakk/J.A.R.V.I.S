"""The safety gate (core/safety.py + config/safety.yaml, used by core/executor.py and the Claude Code hook):
what runs by itself, what asks first, what is only ever put in the manual terminal for you to run. These
commands are only classified, or run through the fake shell: nothing here runs."""
import pytest

from core.safety import AUTO, CONFIRM, MANUAL, Safety


@pytest.fixture(scope="module")
def gate():
    return Safety()


@pytest.mark.parametrize("command", [
    "xdg-open https://youtube.com", "pactl set-sink-volume @DEFAULT_SINK@ 40%", "df -h /", "ls -la ~/Downloads",
    "gsettings set org.gnome.desktop.interface color-scheme prefer-dark", "nmcli con up id 'Home WiFi'",
    "ls ~/Downloads > /dev/null", "ps aux | grep chrome | head -5",
])
def test_safe_commands_run_by_themselves(gate, command):
    assert gate.classify(command)[0] == AUTO


@pytest.mark.parametrize("command", [
    "gsettings set org.gnome.desktop.screensaver lock-enabled false",  # any other desktop setting asks
    "some-new-program --flag", "FOO=1", "systemctl poweroff", "systemctl reboot",  # shutdown asks (your choice)
])
def test_unlisted_or_risky_settings_ask_first(gate, command):
    assert gate.classify(command)[0] == CONFIRM


@pytest.mark.parametrize("command", [
    "rm -rf ~/Downloads", "/bin/rm -rf ~", "sudo reboot", "systemctl --user restart day-tracker", "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sda1",
    "chmod 777 ~", "kill -9 1234", "git push --force", "pip install requests", "python3 -c 'print(1)'",
    # hidden behind something harmless
    "ls; rm -rf ~", "ls && sudo rm -rf /", "ls || rm x", "ls | sh", "cat setup.txt | bash", "ls\nrm -rf ~",
    "echo $(rm -rf ~)", "echo `rm -rf ~`", "diff <(ls) <(ls)", "time rm -rf ~", "if true; then rm x; fi",
    "X=1 rm -rf ~", "env rm -rf ~", "nice rm -rf ~", "timeout 5 rm -rf ~", "xargs rm < list.txt",
    "x=rm; $x -rf ~", "find ~ -name '*.log' -delete", "find . -exec rm {} ;",
    # overwriting files
    "echo hi > ~/.bashrc", "ls >> ~/.profile", "cat x >| y", "echo 'unclosed",
])
def test_dangerous_commands_are_never_run_by_jarvis(gate, command):
    assert gate.classify(command)[0] == MANUAL, command


def test_the_command_list_can_only_make_it_stricter(gate):
    assert gate.classify("ls", declared="confirm")[0] == CONFIRM
    assert gate.classify("rm -rf ~", declared="auto")[0] == MANUAL


@pytest.mark.xfail(strict=True, reason="HOLE: programs on the auto list that run or write anything given the "
                                       "right arguments (a command Claude suggests could use them)")
@pytest.mark.parametrize("command", [
    "awk 'BEGIN{system(\"rm -rf ~\")}'",  # awk runs any command
    "gnome-terminal -- bash -c 'rm -rf ~'",  # a terminal running any command
    "sort -o ~/.bashrc /dev/null",  # sort -o writes over a file
    "find ~ -fprint0 ~/.bashrc",  # find's -fprint0 writes a file (-fprint is caught)
    "awk '{print > \"/home/x/.bashrc\"}' /etc/hostname",  # a redirect inside awk's quoted program
])
def test_auto_programs_cant_be_turned_into_anything(gate, command):
    assert gate.classify(command)[0] != AUTO


# --- through JARVIS: what each level actually does ---

def test_auto_runs_without_asking(jv):
    jv.j.executor.run("df -h /", reply="Done", output=True)
    assert jv.shell.ran(r"^df -h /$") and not jv.questions


LOCK = "gsettings set org.gnome.desktop.screensaver lock-enabled false"


def test_confirm_runs_only_after_yes(jv):
    jv.j.executor.run(LOCK, reply="Turning the lock screen off", question="Turn the lock screen off?")
    assert jv.questions == ["Turn the lock screen off?"] and not jv.shell.ran(r"gsettings")
    assert jv.j.executor.outcome == "declined" and not jv.j.executor.ok
    jv.answers.append("yes")
    jv.j.executor.run(LOCK, reply="Turning the lock screen off", question="Turn the lock screen off?")
    assert jv.shell.ran("^" + LOCK + "$") and jv.j.executor.ok


def test_manual_is_put_in_your_terminal_never_run(jv):
    jv.j.executor.run("rm -rf ~/Downloads/old", reply="Done")
    assert not jv.shell.ran(r"^rm ")
    assert jv.manual == [("rm -rf ~/Downloads/old", "rm can change or delete things on your system")]
    assert jv.says("I'd rather not run that one myself") and not jv.says("Done")
    assert jv.j.executor.outcome == "manual" and not jv.j.executor.ok


def test_a_yes_never_unlocks_a_manual_command(jv):
    jv.answers.extend(["yes", "yes"])
    jv.j.executor.run("ls; rm -rf ~", reply="Done")
    assert not jv.shell.ran(r"rm -rf") and not jv.questions


def test_claude_suggesting_a_dangerous_command_goes_to_the_terminal(jv):
    jv.claude.reply({"say": "I'll clean that up.", "command": "rm -rf ~/Downloads/*", "run_now": True})
    jv.do("clean up my downloads folder for me please")
    assert not jv.shell.ran(r"^rm ")
    assert jv.manual and jv.manual[0][0] == "rm -rf ~/Downloads/*"
