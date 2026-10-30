"""Your projects: what they are, notes for them, running them with biztel run (in a terminal on
JARVIS's workspace) and their links."""
import os
import re
import shlex
import shutil
import subprocess
import time

from skills.base import Skill
from skills.helpers import HOME, PROJECTS, and_list, or_list
from skills.notes import load_notes, save_notes
from system import desktop


class Projects(Skill):
    group = "projects"
    does = "Your ~/Projects: what they are, notes, running them with biztel run, their links"
    phrases = ("run <project>", "stop <project>", "what's running", "links for <project>",
               "open <project> in the browser (its running link)", "what's the <project> project about", "note for <project>: <x>",
               "what's pending")
    gives = "speech; a terminal running it"
    permission = "auto"
    asks = "before stopping everything"
    checks = "says the links once biztel run reports them, or that it didn't start"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.projects = jarvis.projects

    def patterns(self):
        return [
            # system/projects.py knows the projects; — running them is biztel run, in a terminal on JARVIS's workspace
            (r"biztel build|(?:build|make|create|start) (?:a )?new (?:biztel |station )?(?:project|station)",
             lambda m: self.biztel_build()),
            (r"biztel status|(?:what'?s|what is|which (?:projects?|stations?|apps?) (?:is |are )?|is anything) "
             r"running(?: now| right now)?", lambda m: self.whats_running()),
            (r"stop (?:all|every ?thing|all (?:the |my )?(?:projects|stations|apps))(?: that'?s running)?",
             lambda m: self.stop_all()),
            (r"(?:biztel )?(?:run|start|launch|fire up|spin up|bring up) (?:up )?(?:the )?(.+?)"
             r"(?: project| station| app| frontend| backend)?", self.run_project),
            (r"(?:biztel )?(?:stop|shut down|kill) (?!(?:the )?(?:music|song|video|youtube|playback|sound)$)(?:the )?(.+?)"
             r"(?: project| station| app)?", self.stop_project),
            (r"(?:what are |give me |show me |tell me |read me |what'?s )?(?:the |its )?(?:links?|ports?|urls?)"
             r"(?: (?:for|of|to) (?:the )?(.+?)(?: project| station)?)?", self.project_links),
            (r"open (?:the )?(.+?) (?:in (?:the )?browser|frontend|page|ui)|open it in (?:the )?browser", self.open_frontend),
            (r"(?:list|what are|show me|tell me) (?:all )?my projects|what projects do i have|which projects do i have",
             lambda m: self.list_projects()),
            (r"what was i (?:working on|doing)(?: lately| last)?|(?:my )?recent projects|what did i work on(?: last)?",
             lambda m: self.recent_projects()),
            (r"(?:refresh|rescan|reload|update) (?:my |the )?projects(?: list)?", lambda m: self.refresh_projects()),
            (r"(?:what'?s|what is|tell me about|explain|describe) (?:the )?(.+?) (?:project|station)(?: about)?|"
             r"what (?:is|does) (?:the )?(.+?) (?:about|do)|what'?s (?:the )?(.+?) about|tell me about (?:the )?(.+)",
             self.about_project),
            # notes for a project ("note for avtec hs: camera 2 needs recalibrating")
            (r"(?:add a |make a |take a )?note (?:for|on|about|in) (?:the )?(.+?)(?: project| station)?(?:[:,]| that) ?(.+)",
             self.project_note),
            (r"(?:what are |read |show me |list |what'?s in )?(?:my |the )?notes (?:for|on|about|in) (?:the )?(.+?)"
             r"(?: project| station)?|what did i note (?:for|about|on) (?:the )?(.+?)", self.project_notes),
            (r"what'?s pending|what is pending|(?:what'?s|what is) on my (?:to ?do|todo)(?: list)?|"
             r"(?:my )?pending (?:things|tasks|notes|work)|what do i (?:still )?(?:have|need) to do",
             lambda m: self.pending()),
        ]

    def _find(self, name, strict=False):
        """The project `name` means; asks "which one?" when it could be several. None: not a project."""
        p = self.projects.find(name, strict=strict)
        if p:
            return p
        many = self.projects.candidates(name) if not strict else []
        if len(many) > 1:
            self.say("Which one: %s?" % or_list([c.spoken for c in many[:4]]))
            return "asked"
        return None

    def _spoken(self, folder):
        p = self.projects.get(folder)
        return p.spoken if p else folder

    def run_project(self, m):
        p = self._find(m.group(1))
        if p == "asked":
            return
        if not p:
            return False  # "start the timer", "run ls": not a project
        st = self.projects.running().get(p.name)
        if st:
            self.projects.show(p)
            self.say("%s is already running. %s" % (p.spoken, _ports(st["links"])))
            return
        plan = subprocess.run([_biztel(), "run", p.path, "--plan"], capture_output=True, text=True, timeout=10)
        if plan.returncode != 0 or "nothing to run" in plan.stdout:
            self.j.chat("sys", (plan.stdout + plan.stderr).strip())
            self.say("I don't know how to run %s yet. Tell biztel how in its run.json; the example's in the chat." % p.spoken)
            self.j.chat("sys", 'Add to ~/.config/biztel/run.json:\n{"%s": {"services": [{"name": "backend", "cwd": "backend", '
                               '"cmd": "python3 app.py"}, {"name": "frontend", "cwd": "frontend", "cmd": "npm run dev"}]}}'
                        % p.name)
            return
        self.j.chat("sys", "biztel run %s\n%s" % (p.name, plan.stdout.strip()))
        self.say(["Starting %s." % p.spoken, "Okay, running %s." % p.spoken])
        self.projects.run(p, on_up=lambda links: self._project_up(p, links))

    def _project_up(self, p, links):
        """From the watcher thread: say where it's running (once its links are in)."""
        why = "You asked me to run %s, and I watch biztel run until its links are in." % p.spoken
        if links is None:
            line = "%s didn't start. Have a look at its terminal." % p.spoken
        else:
            if links:
                self.j.chat("sys", "%s is running:\n%s" % (p.name, _link_lines(links)))
            line = "%s is up. %s" % (p.spoken, _ports(links)) if links \
                else "%s is running, but I haven't seen a link yet. It's in the terminal." % p.spoken
        self.j.skills.proactive.note("project", line, why)
        self.j.inbox.put(("say", line))

    def stop_project(self, m):
        name = m.group(1)
        live = self.projects.running()
        p = self._find(name) if not re.match(r"^(?:it|that|this|the project)$", name, re.IGNORECASE) else (
            self.projects.current or (self.projects.get(next(iter(live))) if len(live) == 1 else None))
        if p == "asked":
            return
        if not p:
            return False  # "stop the timer"
        st = live.get(p.name)
        if not st:
            self.say("%s isn't running." % p.spoken)
            return
        self.say("Stopping %s." % p.spoken)
        self.projects.stop(st)
        self.say("%s is stopped." % p.spoken)

    def stop_all(self):
        live = self.projects.running()
        if not live:
            return False  # nothing of biztel's: maybe "stop everything" meant the music
        if not self.j.ask("Stop %s?" % and_list([self._spoken(n) for n in live])):
            return
        for st in live.values():
            self.projects.stop(st)
        self.say("All stopped.")

    def whats_running(self):
        live = self.projects.running()
        if not live:
            self.say("Nothing's running right now.")
            return
        self.j.chat("sys", "\n\n".join("%s\n%s" % (n, _link_lines(st["links"])) for n, st in live.items()))
        self.say("%s %s running. %s" % (and_list([self._spoken(n) for n in live]),
                                        "is" if len(live) == 1 else "are",
                                        "The links are in the chat." if len(live) > 1 else _ports(next(iter(live.values()))["links"])))

    def _target(self, name):
        """The project for "links for X" / "its links": named, current, or the only one running."""
        if name:
            return self._find(name)
        live = self.projects.running()
        if self.projects.current and self.projects.current.name in live:
            return self.projects.current
        return self.projects.get(next(iter(live))) if len(live) == 1 else self.projects.current

    def project_links(self, m):
        p = self._target(m.group(1))
        if p == "asked":
            return
        if not p:
            if m.group(1):
                return False
            self.say("Nothing's running, so there are no links yet.")
            return
        st = self.projects.running().get(p.name)
        if not st:
            self.say("%s isn't running. Say run %s and I'll start it." % (p.spoken, p.spoken))
            return
        self.j.chat("sys", "%s\n%s" % (p.name, _link_lines(st["links"])))
        self.say(_ports(st["links"]) + " The links are in the chat.")

    def open_frontend(self, m):
        p = self._target(m.group(1) if m.lastindex else None)
        if p == "asked":
            return
        if not p:
            return False
        st = self.projects.running().get(p.name)
        if not st or not st["links"]:
            self.say("%s isn't running yet. Say run %s first." % (p.spoken, p.spoken))
            return
        url = st["links"][0]["url"]
        self.j.executor.run("xdg-open %s" % shlex.quote(url), reply="Opening %s." % p.spoken, window=True)

    def biztel_build(self):
        inner = "cd %s && %s build; echo; read -rp 'Press Enter to close. '" % (
            shlex.quote(PROJECTS), shlex.quote(_biztel()))
        desktop.go_to_jarvis_workspace()
        subprocess.Popen(["gnome-terminal", "--title", "biztel build", "--", "bash", "-c", inner],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.say("Opening the project builder. Answer its questions in the terminal.")

    def list_projects(self):
        items, live = self.projects.items, self.projects.running()
        self.j.chat("sys", "projects:\n" + "\n".join(
            "%s %s%s" % ("\u25cf" if p.name in live else "-", p.name, " \u2014 " + p.summary if p.summary else "")
            for p in items))
        names = [self._spoken(n) for n in live]
        self.say("You have %d projects; they're in the chat.%s" % (
            len(items), " %s %s running." % (and_list(names), "is" if len(names) == 1 else "are") if names else ""))

    def recent_projects(self):
        recent = self.projects.recent(3)
        self.projects.current = recent[0]
        self.say("Lately you've been on %s." % and_list([p.spoken for p in recent]))

    def refresh_projects(self):
        self.projects.refresh()
        self.say("Done, I know %d projects." % len(self.projects.items))

    def about_project(self, m):
        name = next((g for g in m.groups() if g), "")
        p = self.projects.find(name, strict=True)
        if not p:
            return False  # "what's the weather about": a question for Claude
        notes = [n for n in load_notes() if n.get("project") == p.name]
        running = p.name in self.projects.running()
        self.j.chat("sys", "%s  (%s)\n%s%s%s" % (p.title or p.name, p.path, p.summary or "no README",
                                               "\nrunning" if running else "",
                                               "".join("\n- " + n["text"] for n in notes)))
        extra = (" It's running now." if running else "") + (
            " You have %d note%s for it." % (len(notes), "" if len(notes) == 1 else "s") if notes else "")
        self.say(("%s: %s%s" % (p.spoken, p.summary.replace(" \u00b7 ", ", "), extra)) if p.summary
                 else "%s has no README, so I don't know much about it.%s" % (p.spoken, extra))

    def project_note(self, m):
        p = self.projects.find(m.group(1), strict=True)
        if not p:
            return False  # "note that ...", or not about a project: the plain notes take it
        notes = load_notes()
        notes.append({"text": m.group(2).strip(), "at": time.time(), "project": p.name})
        save_notes(notes)
        self.say(["Noted for %s." % p.spoken, "Okay, added to %s's notes." % p.spoken])

    def project_notes(self, m):
        name = m.group(1) or m.group(2)
        p = self._find(name)
        if p == "asked":
            return
        if not p:
            return False
        notes = [n for n in load_notes() if n.get("project") == p.name]
        if not notes:
            self.say("No notes for %s yet." % p.spoken)
            return
        self.j.chat("sys", "notes for %s:\n%s" % (p.name, "\n".join("- " + n["text"] for n in notes)))
        self.say("For %s: %s." % (p.spoken, "; ".join(n["text"] for n in notes[-4:])))

    def pending(self):
        notes = [n for n in load_notes() if n.get("project")]
        if not notes:
            self.say("Nothing pending. No project has notes.")
            return
        by = {}
        for n in notes:
            by.setdefault(n["project"], []).append(n["text"])
        self.j.chat("sys", "pending:\n" + "\n".join("%s\n%s" % (k, "\n".join("- " + t for t in v)) for k, v in by.items()))
        spoken = []
        for k, v in by.items():
            spoken.append("%s: %s" % (self._spoken(k), "; ".join(v[-2:])))
        self.say("%d note%s across %d project%s. %s." % (len(notes), "" if len(notes) == 1 else "s", len(by),
                                                       "" if len(by) == 1 else "s", ". ".join(spoken)))


def _biztel():
    return shutil.which("biztel") or os.path.join(HOME, ".npm-global/bin/biztel")


def _ports(links):
    """Spoken: "The frontend's on port 5173, plus 3 more." (local links only)."""
    local = [l for l in links or [] if "(network)" not in l["name"]]
    if not local:
        return ""
    first = local[0]
    port = re.search(r":(\d+)", first["url"])
    more = len(local) - 1
    return "The %s's on port %s%s." % (first["name"], port.group(1) if port else first["url"],
                                       ", plus %d more" % more if more else "")


def _link_lines(links):
    width = max([len(l["name"]) for l in links] or [0])
    return "\n".join("  %s  %s" % (l["name"].ljust(width), l["url"]) for l in links) or "  (no links yet)"
