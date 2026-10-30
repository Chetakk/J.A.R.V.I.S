"""Decides whether a shell command runs automatically, needs a yes/no, or must be run by the user.

Kept dependency-free (stdlib + yaml) so the Claude Code hook can import it quickly.
"""
import os
import re
import shlex

import yaml

AUTO, CONFIRM, MANUAL = "auto", "confirm", "manual"
_RANK = {AUTO: 0, CONFIRM: 1, MANUAL: 2}

# Programs that are always manual, whatever safety.yaml says.
HARD_MANUAL = {
    "rm", "rmdir", "unlink", "shred", "truncate", "mv", "dd", "fdisk", "sfdisk", "parted",
    "wipefs", "mkswap", "sudo", "su", "doas", "pkexec", "chmod", "chown", "chgrp", "chattr",
    "apt", "apt-get", "aptitude", "dpkg", "snap", "pip", "pip3", "kill", "killall",
    "crontab", "useradd", "userdel", "usermod", "passwd", "mount", "umount", "iptables",
    "ufw", "eval", "exec", "source", ".", "xargs", "env", "nohup", "sh", "bash", "zsh",
    "dash", "python", "python3", "perl", "ruby", "node",
    # Wrappers that run another program.
    "command", "builtin", "nice", "ionice", "timeout", "stdbuf", "watch", "script", "setsid",
    "flock", "chroot", "nsenter", "busybox", "unshare",
}
# Shell keywords that can sit in front of the real program.
SHELL_KEYWORDS = {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done", "while",
                  "until", "time", "case", "esac", "in"}
HARD_MANUAL_PREFIXES = ("mkfs",)
# Shell syntax that can overwrite files or hide another command.
HARD_MANUAL_TOKENS = {">", ">>", ">|", "&>", "<<<"}
SEPARATORS = {";", "&&", "||", "|", "&", "|&", "\n", "(", ")"}
# find flags that change files.
FIND_DANGEROUS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}

# Programs that are fine on the safe list until their arguments make them run something or write a file.
AWKS = {"awk", "gawk", "mawk", "nawk"}
AWK_UNSAFE = re.compile(r"system|getline|>|\|")  # run a command, read from one, write or pipe from the program
AWK_FLAGS = {"-f", "--file", "-i", "--include", "-E", "--exec", "-l", "--load"}  # programs/extensions from files
TERMINAL_RUNS = {"-e", "-x", "--", "--command", "--execute"}  # gnome-terminal running another command


# git options that run other programs or write files, and subcommand flags that destroy work.
GIT_RUNS = {"-c", "--config-env", "--exec-path", "--upload-pack", "--receive-pack", "--ext-cmd"}
GIT_DESTROYS = {"--force", "-f", "--force-with-lease", "--hard", "--delete", "-D", "--mirror", "--output"}
GIT_NEVER = {"clean", "filter-branch", "gc", "prune", "reflog"}


def _git_words(words):
    """`git -C dir status` -> ('git', 'status'), so rules match the subcommand; or (None, reason)."""
    out, i = ["git"], 1
    while i < len(words) and words[i].startswith("-") and len(out) == 1:
        if words[i] == "-C" and i + 1 < len(words):
            i += 2
        elif words[i] in GIT_RUNS or words[i].startswith(("-c", "--config-env", "--exec-path")):
            return None, "git %s can run other programs" % words[i]
        else:
            i += 1
    out += words[i:]
    return out, None


def _risky_args(prog, words):
    """Why `prog` with these arguments isn't safe to run by itself, or None."""
    args = words[1:]
    if prog == "git":
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub in GIT_NEVER or GIT_DESTROYS.intersection(args) or any(a.startswith("--output") for a in args):
            return "git %s can destroy work or overwrite files" % sub
    if prog in AWKS and (AWK_FLAGS.intersection(args) or any(AWK_UNSAFE.search(a) for a in args if a[:1] != "-")):
        return "%s can run commands or write files from its program" % prog
    if prog in ("gnome-terminal", "xterm", "x-terminal-emulator") and (
            TERMINAL_RUNS.intersection(args) or any(a.startswith(("--command=", "--execute=")) for a in args)):
        return "%s would run another command" % prog
    if prog == "sort" and any(a == "--output" or a.startswith("--output=") or (
            a[:1] == "-" and a[1:2] != "-" and "o" in a[1:]) for a in args):
        return "sort -o writes over a file"
    return None


_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(_HERE, "..", "config", "safety.yaml")


def stricter(a, b):
    return a if _RANK[a] >= _RANK[b] else b


class Safety:
    def __init__(self, path=DEFAULT_RULES):
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        # Prefix (as a word tuple) -> level; longest prefix wins.
        self.rules = {}
        for level in (AUTO, CONFIRM, MANUAL):
            for entry in data.get(level) or []:
                self.rules[tuple(str(entry).split())] = level

    def classify(self, command, declared=None):
        """Return (level, reason) for a full shell command string."""
        level, reason = self._classify(command)
        if declared in _RANK and _RANK[declared] > _RANK[level]:
            return declared, "marked %s in the command list" % declared
        return level, reason

    def _classify(self, command):
        if "`" in command or "$(" in command or "<(" in command or ">(" in command:
            return MANUAL, "it contains a hidden sub-command"
        try:
            # Newlines separate commands, but shlex treats them as plain whitespace.
            lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:
            return MANUAL, "I couldn't parse it safely"
        if not tokens:
            return MANUAL, "it is empty"

        overall, why = AUTO, "it is on the safe list"
        for segment in _segments(tokens):
            level, reason = self._segment_level(segment)
            if _RANK[level] > _RANK[overall]:
                overall, why = level, reason
        return overall, why

    def _segment_level(self, words):
        for i, w in enumerate(words):
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if (w in HARD_MANUAL_TOKENS or (w.startswith(">") and w != ">&")) and nxt != "/dev/null":
                return MANUAL, "it writes over a file"
        # Skip shell keywords and leading VAR=value assignments.
        i = 0
        while i < len(words) and (words[i] in SHELL_KEYWORDS
                                  or ("=" in words[i] and not words[i].startswith("="))):
            i += 1
        words = words[i:]
        if not words:
            return CONFIRM, "it only sets variables"
        prog = os.path.basename(words[0])
        if "$" in prog:
            return MANUAL, "the program name is hidden in a variable"
        if prog in HARD_MANUAL or prog.startswith(HARD_MANUAL_PREFIXES):
            return MANUAL, "%s can change or delete things on your system" % prog
        if prog == "find" and (FIND_DANGEROUS.intersection(words) or any(w.startswith("-fprint") for w in words)):
            bad = FIND_DANGEROUS.intersection(words) or {w for w in words if w.startswith("-fprint")}
            return MANUAL, "find with %s can change files" % "/".join(sorted(bad))
        if prog == "git":
            words, why = _git_words(words)
            if words is None:
                return MANUAL, why
        risky = _risky_args(prog, words)
        if risky:
            return MANUAL, risky

        key = (prog,) + tuple(words[1:])
        for n in range(len(key), 0, -1):
            level = self.rules.get(key[:n])
            if level:
                reasons = {AUTO: "it is on the safe list",
                           CONFIRM: "%s needs your okay" % " ".join(key[:n]),
                           MANUAL: "%s is on the manual list" % " ".join(key[:n])}
                return level, reasons[level]
        return CONFIRM, "%s is not on any list yet" % prog


def _segments(tokens):
    seg = []
    for t in tokens:
        if t in SEPARATORS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(t)
    if seg:
        yield seg
