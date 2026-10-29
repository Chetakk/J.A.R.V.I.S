""""Why isn't my app running?" and "what's using port 3000": from what's on this computer, before any Claude.

For a project it looks at whether biztel run is running it (its state file), the ports its folder's programs
listen on, the log biztel run keeps (~/.local/state/biztel/runs/<name>.log) and the visible text of its
terminal. A clear answer (not started, a port taken, the last error before it stopped) is said straight
away; anything murkier goes to Claude with all of that."""
import os
import re
import time

from skills.base import Skill
from skills.helpers import HOME, and_list
from system import accessibility, desktop, ports

GENERIC = r"(?:app|it|this|that|project|server|site|website|web ?site|page|frontend|backend|api|station|thing|code)"
ERROR = re.compile(r"error|exception|traceback|failed|fatal|cannot|can'?t|not found|no such|eaddrinuse|"
                   r"address already in use|permission denied|refused|killed|segmentation", re.IGNORECASE)
TROUBLE = (r"run|running|work|working|start|starting|load|loading|open|opening|come up|coming up|go|going|connect"
           r"|connecting")
STOPPED = (r"crash|crashed|crashing|stop|stopped|stopping|fail|failed|failing|die|died|dying|quit|exit|exited|"
           r"break|broke|broken|go down|went down|down")


class Troubleshoot(Skill):
    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.projects = jarvis.projects

    def patterns(self):
        return [
            (r"(?:what'?s|what is|who'?s|who is|which (?:app|program|process)(?: is)?) (?:using|on|running on|"
             r"listening on|taking|holding|blocking) port (\d+)|is port (\d+) (?:free|in use|taken|open|busy)|"
             r"is (?:anything|something) (?:running |listening )?on port (\d+)", self.port),
            (r"(?:what|which) ports are (?:open|in use|taken|listening|being used)(?: (?:now|right now))?|"
             r"what'?s listening on (?:which|what) ports?", lambda m: self.all_ports()),
            (r"why (?:isn'?t|is not|won'?t|wont|doesn'?t|does not|didn'?t|did not|can'?t|cannot) (?:my |the )?(.+?) "
             r"(?:%s)(?: anymore| any more| properly| now)?|why (?:did|does|is|has|was|do|does) (?:my |the )?(.+?) "
             r"(?:keep )?(?:%s|not working|not running)|what'?s wrong with (?:my |the )?(.+?)|"
             r"(?:is|did|has) (?:my |the )?(.+?) (?:crash|crashed|die|died|fail|failed|go down|gone down|stopped)"
             % (TROUBLE, STOPPED), self.why),
        ]

    def port(self, m):
        number = int(next(g for g in m.groups() if g))
        found = ports.on_port(number)
        if not found:
            self.say("Nothing's using port %d; it's free." % number)
            return
        self.say("Port %d is %s." % (number, and_list(self._describe(l) for l in found)))

    def all_ports(self):
        found = [l for l in ports.listening() if l.program]
        if not found:
            self.say("I can't see any program of yours listening on a port.")
            return
        self.j.show({"title": "Ports in use", "text": "\n".join(
            "- **%d** %s%s" % (l.port, l.program, " · %s" % _short(l.folder) if l.folder else "") for l in found)})
        self.say("%d ports are in use, like %s. They're on the card." % (
            len(found), and_list("%d by %s" % (l.port, l.program) for l in found[:3])))

    def _describe(self, listener):
        """"node, running study buddy" / "whisper-server" / "something I can't see (another user's)"."""
        if not listener.program:
            return "taken by a program I can't see, probably the system's"
        p = self._project_of(listener.folder)
        return "used by %s%s" % (listener.program, ", running %s" % p.spoken if p else
                                 " in %s" % _short(listener.folder) if listener.folder else "")

    def _project_of(self, folder):
        return next((p for p in self.projects.items if folder == p.path or folder.startswith(p.path + "/")), None)

    def why(self, m):
        name = next(g for g in m.groups() if g).strip()
        p = self._target(name)
        if p == "asked":
            return
        if not p:
            return False  # "why isn't my wifi working": not a project, Claude takes it
        facts, quick = self._look(p)
        if quick:
            self.say(quick)
            return
        if self.j.mode == "sys":
            self.say("I can't tell why from here, and Claude's off in system mode. The details are in the chat.")
            self.j.chat("sys", facts)
            return
        self.j._ask_claude("Why isn't %s working? Here's what I found on this computer:\n\n%s\n\nIn \"say\", give "
                           "the likely cause in one or two short sentences and what to do about it; put any longer "
                           "explanation in \"details\"." % (p.spoken, facts))

    def _target(self, name):
        """The project: named, or for "my app" / "it" the one you last named, ran or logged."""
        if not re.fullmatch(GENERIC, name, re.IGNORECASE):
            return self.j.skills.projects._find(name)
        live = self.projects.running()
        if self.projects.current:
            return self.projects.current
        if len(live) == 1:
            return self.projects.get(next(iter(live)))
        logged = [(self.projects.log(p, 1)[1] or 0, p) for p in self.projects.items]
        latest = max(logged, key=lambda t: t[0], default=(0, None))
        return latest[1] if latest[0] else None

    def _look(self, p):
        """(facts for Claude, a clear spoken answer or None)."""
        state = self.projects.running().get(p.name)
        log, written = self.projects.log(p)
        log = _plain(log)
        mine = [l for l in ports.listening() if self._project_of(l.folder) is p]
        screen = self._terminal_text(p)
        facts = ["Project: %s (%s)" % (p.spoken, p.path)]
        if state:
            alive = [s for s in state.get("services") or [] if _alive(s.get("pid"))]
            facts.append("biztel run: running since %s; services %s" % (
                _ago(state.get("started", 0) / 1000),
                ", ".join("%s (%s)" % (s["name"], "up" if s in alive else "exited")
                          for s in state.get("services") or []) or "none"))
            facts.append("Links: %s" % (", ".join(l["url"] for l in state.get("links") or []) or "none yet"))
        else:
            facts.append("biztel run: not running it")
        facts.append("Its programs listening: %s" % (", ".join("%s on %d" % (l.program, l.port) for l in mine)
                                                     or "none"))
        if log:
            facts.append("Its log (last lines, written %s):\n%s" % (_ago(written), log[-4000:]))
        if screen:
            facts.append("Its terminal shows:\n%s" % screen[-2000:])
        facts = "\n".join(facts)
        if log or screen:
            self.j.show({"title": "%s: log" % p.spoken, "text": "```\n%s\n```" % (log or screen)[-3000:]})

        # The clear cases, said without Claude.
        if not state and not log and not screen:
            return facts, "%s isn't running, and I have no log of it. Say run %s to start it." % (p.spoken, p.spoken)
        taken = re.search(r"(?:eaddrinuse|address already in use|port (\d+) is (?:already )?in use)[^\n]*", log + screen,
                          re.IGNORECASE)
        if taken:
            number = taken.group(1) or next(iter(re.findall(r":(\d{2,5})\b", taken.group(0))), None)
            holder = ports.on_port(int(number)) if number else []
            if holder:
                return facts, "Its port %s is already taken: it's %s. Stop that and run %s again." % (
                    number, self._describe(holder[0]), p.spoken)
        if state and all(_alive(s.get("pid")) for s in state.get("services") or []) and mine \
                and not ERROR.search(log[-1500:]):
            return facts, "%s looks fine: it's running, listening on %s, and nothing in its log looks wrong." % (
                p.spoken, and_list("port %d" % l.port for l in mine))
        if not state and log:
            stop = _last(r"(\S+) │ exited \((\w+)\)", log)
            error = _last_error(log)
            if stop and error:
                return facts, "%s stopped: its %s exited with code %s. The last error was: %s" % (
                    p.spoken, stop.group(1), stop.group(2), error.rstrip(".") + ".")
        return facts, None

    def _terminal_text(self, p):
        win = self.projects.terminal(p)
        if not win:
            return ""
        text, _ = accessibility.window_text(desktop.pid(win), win.title)
        return "\n".join(l.rstrip() for l in text.splitlines()[-60:] if l.strip())


def _last(pattern, text):
    found = list(re.finditer(pattern, text))
    return found[-1] if found else None


def _last_error(log):
    """The last line that reads like an error (the service name stripped), or ""."""
    for line in reversed(log.splitlines()):
        line = line.split(" │ ", 1)[-1].strip()
        if ERROR.search(line) and not re.match(r"exited \(", line):
            return line[:200]
    return ""


def _plain(text):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text or "")


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _ago(when):
    if not when:
        return "at some point"
    minutes = int((time.time() - when) / 60)
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return "%d minute%s ago" % (minutes, "" if minutes == 1 else "s")
    return "%d hours ago" % (minutes // 60) if minutes < 48 * 60 else "%d days ago" % (minutes // 1440)


def _short(folder):
    return "~" + folder[len(HOME):] if folder.startswith(HOME) else folder
