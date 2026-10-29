"""What JARVIS brings up by itself, and why.

Every heads-up goes through tell(): it keeps the reason ("why did you say that", "show my recent alerts"),
drops the kinds you muted ("don't warn me about that again") and, during focus mode and quiet hours, holds
events until they're over ("While you were focusing: ..."). Checks that repeat (disk, CPU) are simply skipped
then and come up at the next check after.

Also here: resource alerts (CPU, memory, temperature; thresholds in settings heads_up), a project crashing
under biztel run (its log says "exited (1)"; one you stop says "stopped"), "tell me when this finishes" for a
command in a terminal, and quiet hours by voice ("quiet hours from 10 pm to 6 am")."""
import glob
import os
import re
import threading
import time
from collections import deque

from core import persona
from skills.base import Skill
from skills.helpers import and_list, or_list
from skills.slow_computer import _meminfo, top_processes
from system import jobs
from system.projects import RUNS

KINDS = {"cpu": "cpu", "processor": "cpu", "memory": "memory", "ram": "memory", "temperature": "temperature",
         "temp": "temperature", "heat": "temperature", "overheating": "temperature", "disk": "disk",
         "disk space": "disk", "storage": "disk", "battery": "battery", "batteries": "battery",
         "crash": "crash", "crashes": "crash", "project": "crash", "projects": "crash",
         "habit": "habit", "habits": "habit", "suggestion": "habit", "suggestions": "habit", "offer": "habit",
         "offers": "habit"}
NOUN = {"cpu": "CPU", "memory": "memory", "temperature": "temperature", "disk": "disk space", "battery": "battery",
        "crash": "project crash", "habit": "habit offer"}
ALWAYS = ("health", "reminder", "job", "timer", "focus", "project")  # you asked for these, or JARVIS can't hear you
HOURS = {"midnight": 0, "noon": 12, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
HOUR = r"(\d{1,2}|%s)(?:[:.]00)? ?(?:o'?clock )?([ap])?\.? ?m?\.?" % "|".join(HOURS)
ALERTS = r"(?:alerts?|heads[- ]?ups?|warnings?)"  # not "notifications": "mute notifications" is a command
POPUP = r"(?:alerts?|heads[- ]?ups?|warnings?|notifications?|messages?|pop-?ups?)"
SOME = r"(?:the |my |your |these |those |this |that |all )?"
GENERIC = {"it", "this", "that", "this one", "that one", "the command", "this command", "my command", "the job",
           "this job", "my job", "the terminal", "my terminal", "this terminal", "whatever's running", "everything"}


class Proactive(Skill):
    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.log = deque(maxlen=30)  # {"at", "kind", "line", "why"}: what JARVIS brought up and why
        self.held = []  # (when, line, why, during) events kept back during focus mode / quiet hours
        self._cpu = None  # (total, idle, when) from /proc/stat at the last check
        self._logs = None  # biztel run log -> (inode, bytes read); None until the first look
        self.waiting = []  # jobs.Job you asked to be told about ("tell me when this finishes")
        self.lock = threading.Lock()

    def patterns(self):
        return [
            (r"why (?:did|do|would|are|were) you (?:say|said|tell(?:ing)? me|mention(?:ing)?|bring(?:ing)? up|"
             r"warn(?:ing)? me about|remind(?:ing)? me (?:of|about)|offer(?:ing)?|ask(?:ing)?)(?: me)? (?:that|this|it)"
             r"(?: now)?|why did (?:that|this|the) %s (?:appear|show up|pop up|come up|come)|"
             r"why (?:that|this|the) %s|what (?:was|is) (?:that|this) (?:%s )?(?:about|for)|"
             r"why are you telling me (?:this|that)" % (POPUP, POPUP, POPUP), self.why),
            (r"(?:show |list |read )?(?:me )?(?:my |the |your )?(?:recent |last |latest |today'?s )?%s(?: (?:today|"
             r"from today|i missed))?|what did (?:you tell me|i miss)(?: (?:today|earlier|while i was (?:away|out|"
             r"focusing|busy)))?|what (?:alerts|heads[- ]?ups|warnings) did (?:i miss|you give me)" % ALERTS,
             lambda m: self.recent()),
            (r"(?:don'?t|do not|never) (?:warn|tell|remind|bother) me about (%s) (?:again|any ?more)|"
             r"stop (?:warning|telling|reminding|bothering) me about (%s)|(?:stop|mute|turn off|disable|silence|"
             r"no more) %s(cpu|processor|memory|ram|temperature|temp|heat|overheating|disk(?: space)?|storage|"
             r"battery|crash|project|habit|suggestion|offer)? ?%s|"
             r"(?:stop|quit) (offering|suggesting) (?:me )?(?:that|this|it|things)(?: again)?"
             % (r"(?:that|this|it|the \w+(?: \w+)?)", r"(?:that|this|it|the \w+(?: \w+)?)", SOME, ALERTS), self.mute),
            (r"(?:turn (?:on )?|unmute |enable )?%s(cpu|processor|memory|ram|temperature|temp|disk(?: space)?|"
             r"battery|crash|project|habit)? ?%s (?:back on|on again)|(?:unmute|enable|turn on) %s(cpu|memory|"
             r"temperature|disk|battery|crash|habit)? ?%s|warn me about (?:the )?(\w+(?: \w+)?) again"
             % (SOME, ALERTS, SOME, ALERTS), self.unmute),
            (r"(?:tell me|let me know|notify me|ping me|alert me|give me a shout|shout|say something) (?:when|once|"
             r"as soon as|after) (?:(?:the|my) )?(.+?)(?:'s|'re| is| are| has| have)? (?:finished|finishes|finish|"
             r"done|completes?|completed|ends?|over|stops?|exits?)(?: running)?", self.when_done),
            (r"(?:set |make |change )?(?:my |the )?quiet hours (?:to |from |are |be |as |between )?%s"
             r" ?(?:to|till|until|-|and) ?%s" % (HOUR, HOUR), self.set_quiet),
            (r"(?:turn |switch )(off|on) (?:my |the )?quiet hours|(?:my |the )?quiet hours (off|on)|"
             r"(?:no|disable|enable) quiet hours", self.toggle_quiet),
            (r"(?:what|when) are (?:my |the |your )?quiet hours|(?:is it|are we in) quiet hours(?: now)?",
             lambda m: self.quiet_status()),
        ]

    def apply(self):
        """At startup: quiet hours set by voice last time (over settings.yaml)."""
        q = self.j.memory.pref("quiet_hours")
        if isinstance(q, dict):
            self.j.settings["quiet_hours"] = dict(self.j.settings.get("quiet_hours") or {}, **q)

    # --- telling, holding, remembering why ---

    def holding(self):
        """Focus mode, or quiet hours (unless settings quiet_hours hold: false): heads-ups wait."""
        q = self.j.settings.get("quiet_hours") or {}
        return self.j.skills.focusing() or (q.get("hold", True) and persona.quiet_hours(self.j.settings))

    def muted(self, kind):
        off = self.j.memory.pref("muted_alerts") or []
        return kind in off or kind.split(":")[0] in off

    def note(self, kind, line, why):
        """Remember something JARVIS said by itself, and why (it's said elsewhere)."""
        with self.lock:
            self.log.append({"at": time.time(), "kind": kind, "line": line, "why": why})

    def tell(self, kind, line, why, hold=True):
        """Say a heads-up from a background thread. During focus / quiet hours it's kept for later (hold=False:
        you asked for it, so it's said anyway). False if it wasn't said now."""
        if self.muted(kind):
            return False
        self.note(kind, line, why)
        if hold and self.holding():
            with self.lock:
                self.held.append((time.time(), line, why, "focus" if self.j.skills.focusing() else "quiet"))
            print("[proactive] held: %s" % line)
            return False
        self.j.inbox.put(("say", line))
        return True

    def watch(self):
        """Every 10 s from jarvis.watch: crashed projects, then anything held once focus / quiet hours end."""
        self._check_runs()
        with self.lock:
            if not self.held or self.holding():
                return
            seen = self.j.memory.data.get("last_seen", 0)
            if any(h[3] == "quiet" for h in self.held) and time.time() - seen > 1800:
                return  # quiet hours ended, but you're not back yet: wait for your first request
            held, self.held = self.held, []
        during = "while you were focusing" if all(h[3] == "focus" for h in held) else "during quiet hours"
        lines = [re.sub(r"^Heads up, (\w)", lambda m: m.group(1).upper(), h[1]) for h in held[-3:]]
        more = " And %d more on the card." % (len(held) - 3) if len(held) > 3 else ""
        if len(held) > 1:  # shown from the main loop
            self.j.inbox.put(("ctl", {"show": {"title": "Held %s" % during, "text": "\n".join(
                "- %s %s" % (time.strftime("%H:%M", time.localtime(h[0])), h[1]) for h in held)}}))
        self.j.inbox.put(("say", "%s: %s%s" % (during.capitalize(), " ".join(lines), more)))

    def why(self, m):
        """The reason for the last thing JARVIS brought up by itself. "Why did you say that" after an ordinary
        answer goes on to Claude."""
        with self.lock:
            last = self.log[-1] if self.log else None
        said = next((r[len("JARVIS said: "):] for r in reversed(self.j.recent) if r.startswith("JARVIS said: ")), "")
        about_alert = re.search(r"notification|alert|warn|heads|remind|offer", m.group(0), re.I)
        if not last or time.time() - last["at"] > 3600:
            if about_alert:
                self.say("I haven't brought anything up in the last hour.")
                return
            return False
        if not about_alert and last["line"][:30].lower() not in said.lower():
            return False  # the last thing said was an answer, not a heads-up
        self.say(last["why"])

    def recent(self):
        with self.lock:
            today = [e for e in self.log if time.time() - e["at"] < 86400]
            held = list(self.held)
        if not today:
            self.say("I haven't brought anything up today.")
            return
        rows = ["- **%s** %s  \n  _%s_" % (time.strftime("%H:%M", time.localtime(e["at"])), e["line"], e["why"])
                for e in reversed(today)]
        self.j.show({"title": "What I brought up today", "text": "\n".join(rows)})
        self.say("%d thing%s today%s. The latest: %s" % (
            len(today), "" if len(today) == 1 else "s", ", %d held for later" % len(held) if held else "",
            today[-1]["line"]))

    # --- muting kinds of heads-up ---

    def mute(self, m):
        named = next((g for g in m.groups() if g), "") or ""
        kind = self._kind(named)
        if not kind:
            self.say("Which alerts? CPU, memory, temperature, disk, battery, project crashes or habit offers.")
            return
        if kind.split(":")[0] in ALWAYS:
            self.say("I'll keep telling you about that one: you asked for it, or it means I can't hear you.")
            return
        off = set(self.j.memory.pref("muted_alerts") or [])
        off.add(kind)
        self.j.memory.set_pref("muted_alerts", sorted(off))
        what = ("offering to %s" % self._habit_say(kind[6:])) if kind.startswith("habit:") else \
            "%s alerts" % NOUN.get(kind, kind)
        base = kind.split(":")[0]
        self.say("Okay, no more %s. Say turn the %s alerts back on if you want them." % (
            what, base if base in ("habit", "crash", "disk") else NOUN.get(base, base)))

    def unmute(self, m):
        named = next((g for g in m.groups() if g), "")
        off = self.j.memory.pref("muted_alerts") or []
        kind = KINDS.get((named or "").lower().replace(" space", ""))
        if not off:
            self.say("All my alerts are already on.")
            return
        keep = [k for k in off if kind and k.split(":")[0] != kind]
        self.j.memory.set_pref("muted_alerts", keep)
        self.say("Okay, %s alerts are back on." % (NOUN.get(kind, kind) if kind else "all my"))

    def _kind(self, named):
        """"the cpu" -> cpu; "that" -> the kind of the last heads-up (a habit offer: just that one)."""
        named = re.sub(r"^the ", "", (named or "").lower()).strip()
        if named in KINDS:
            return KINDS[named]
        if named in ("offering", "suggesting") or not named or named in ("that", "this", "it"):
            with self.lock:
                last = self.log[-1] if self.log and time.time() - self.log[-1]["at"] < 3600 else None
            if named in ("offering", "suggesting"):
                return last["kind"] if last and last["kind"].startswith("habit:") else "habit"
            return last and last["kind"]
        return None

    def _habit_say(self, name):
        cmd = next((c for c in self.j.registry.commands if c["name"] == name), None)
        return cmd["say"][0] if cmd and cmd.get("say") else name.replace("_", " ")

    # --- resource alerts (from jarvis.watch, every 2 minutes) ---

    def resources(self):
        """[(key, line, why)] for a CPU busy since the last check, memory nearly full, or a hot computer."""
        cfg, ups = self.j.settings.get("heads_up") or {}, []
        cpu, minutes = self._cpu_since()
        busiest = None
        if cfg.get("cpu", 90) and cpu is not None and cpu >= cfg.get("cpu", 90):
            busiest = (top_processes(0.5) or [["something"]])[0]
            ups.append(("cpu", "Heads up, the CPU has been at %d percent for the last %d minutes, mostly %s."
                        % (cpu, minutes, busiest[0]),
                        "The CPU averaged %d%% over the %d minutes since my last check, and I mention it at %d%% or "
                        "more (heads_up cpu in settings). %s was the busiest. Say what's slowing my computer for "
                        "more." % (cpu, minutes, cfg.get("cpu", 90), busiest[0])))
        total, free = _meminfo("MemTotal"), _meminfo("MemAvailable")
        used = 100 - 100 * free // total if total else 0
        if cfg.get("memory", 90) and used >= cfg.get("memory", 90):
            heavy = max(top_processes(0.1), key=lambda p: p[2])
            ups.append(("memory", "Heads up, memory is nearly full, %d percent used. %s is using the most." % (
                used, heavy[0]), "%.1f of %.1f GB of memory is in use (%d%%), and I mention it at %d%% (heads_up "
                                 "memory in settings). %s uses %d MB." % (
                (total - free) / 2 ** 20, total / 2 ** 20, used, cfg.get("memory", 90), heavy[0], heavy[2])))
        hot, sensor = temperature()
        if cfg.get("temperature", 85) and hot >= cfg.get("temperature", 85):
            ups.append(("temperature", "Heads up, the computer is running hot, %d degrees%s." % (
                hot, ". %s is working it hardest" % busiest[0] if busiest else ""),
                "The %s sensor reads %d°C, and I mention it at %d°C (heads_up temperature in settings). The Jetson "
                "slows itself down if it keeps climbing." % (sensor, hot, cfg.get("temperature", 85))))
        return ups

    def _cpu_since(self):
        """(average CPU % since the last call, minutes), or (None, 0) the first time."""
        with open("/proc/stat") as f:
            ticks = [int(x) for x in f.readline().split()[1:]]
        now, (total, idle) = time.time(), (sum(ticks), ticks[3] + ticks[4])
        last, self._cpu = self._cpu, (total, idle, now)
        if not last or now - last[2] < 60 or total <= last[0]:
            return None, 0
        return round(100 * (1 - (idle - last[1]) / (total - last[0]))), round((now - last[2]) / 60)

    # --- projects crashing under biztel run ---

    def _check_runs(self):
        """New lines in biztel run's logs: a service that "exited (code)" by itself crashed (one you stop says
        "stopped"). Logs already there when JARVIS starts are read from their end."""
        first, self._logs = self._logs is None, self._logs or {}
        for path in glob.glob(os.path.join(RUNS, "*.log")):
            try:
                st = os.stat(path)
            except OSError:
                continue
            inode, read = self._logs.get(path, (st.st_ino, st.st_size if first else 0))
            if inode != st.st_ino or st.st_size < read:
                read = 0  # a new run (the log starts over) or rotated past 1 MB
            self._logs[path] = (st.st_ino, st.st_size)
            if st.st_size == read:
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    f.seek(read)
                    new = f.read(1_000_000)
            except OSError:
                continue
            name = os.path.basename(path)[:-4]
            for crash in re.finditer(r"^(\S+) │ exited \((\w+)\)$", new, re.M):
                self._crashed(name, crash.group(1), crash.group(2), new[:crash.start()])

    def _crashed(self, name, service, code, before):
        p = self.j.projects.get(name)
        spoken = p.spoken if p else name
        error = ""
        for line in reversed(before.splitlines()):
            line = line.split(" │ ", 1)[-1].strip()
            if re.search(r"error|exception|traceback|failed|fatal|cannot|not found|refused|killed", line, re.I):
                error = line[:160]
                break
        how = "stopped by itself" if code == "0" else "crashed" if code.isdigit() else "was killed (%s)" % code
        line = "Heads up, %s's %s %s.%s" % (spoken, service, how, " The last error was: %s" % error.rstrip(".") + "."
                                            if error else "")
        self.tell("crash", line, "I watch the log biztel run keeps for %s (~/.local/state/biztel/runs/%s.log). Its "
                                 "%s exited with %s without being stopped. Say why isn't %s working for more." % (
                                     spoken, name, service, "code %s" % code if code.isdigit() else code, spoken))

    # --- "tell me when this finishes" ---

    def when_done(self, m):
        name = m.group(1).strip().lower()
        running = jobs.running()
        if not running and name not in GENERIC and self.j.projects.find(name):
            return False  # "tell me when study buddy is done": not something running in a terminal
        if not running:
            self.say("Nothing's running in a terminal right now.")
            return
        if name not in GENERIC:
            wanted = [w.rstrip("s") for w in re.findall(r"[a-z0-9]+", name) if len(w) > 2]
            hits = [j for j in running if any(w in j.command.lower() or w in " ".join(j.argv).lower()
                                              for w in wanted)]
            if not hits:
                self.say("I can't see %s running in a terminal. What's running is %s." % (
                    name, and_list(j.command for j in running[:3])))
                return
            running = hits
        job = running[0]
        if len(running) > 1:
            picked = self.j.choose("Which one: %s?" % or_list(j.command for j in running[:3]),
                                   [j.command for j in running[:3]])
            job = next((j for j in running if j.command == picked), None)
            if not job:
                self.say("Okay, never mind.")
                return
        asked = time.time()
        self.say(["Okay, I'll tell you when %s finishes." % job.command,
                  "Sure, I'll let you know when %s is done." % job.command])

        self.waiting.append(job)

        def wait():
            finished = jobs.wait(job)
            self.waiting.remove(job)
            if not finished:
                return
            folder = job.cwd.replace(os.path.expanduser("~"), "~")
            self.tell("job", "%s has finished, after %s." % (job.command[0].upper() + job.command[1:],
                                                            jobs.took(time.time() - job.started)),
                      "You asked me at %s to tell you when %s%s finished." % (
                          time.strftime("%H:%M", time.localtime(asked)), job.command,
                          " (in %s)" % folder if folder else ""), hold=False)

        threading.Thread(target=wait, daemon=True).start()

    # --- quiet hours ---

    def set_quiet(self, m):
        start, end = _hour(m.group(1), m.group(2), night=True), _hour(m.group(3), m.group(4), night=False)
        if start is None or end is None or start == end:
            self.say("Sorry, I didn't get those hours. Try quiet hours from 11 pm to 7 am.")
            return
        self._save_quiet(enabled=True, start=start, end=end)
        self.say("Quiet hours are now %s to %s. %s" % (_clock(start), _clock(end), self._quiet_means()))

    def toggle_quiet(self, m):
        on = "on" in m.group(0).lower().split() or m.group(0).lower().startswith("enable")
        self._save_quiet(enabled=on)
        q = self.j.settings["quiet_hours"]
        self.say("Quiet hours are on, %s to %s." % (_clock(q.get("start", 23)), _clock(q.get("end", 7))) if on
                 else "Quiet hours are off. I'll talk at my normal volume all day.")

    def quiet_status(self):
        q = self.j.settings.get("quiet_hours") or {}
        if not q.get("enabled", True):
            self.say("Quiet hours are off.")
            return
        now = " It's quiet hours now." if persona.quiet_hours(self.j.settings) else ""
        self.say("Quiet hours are %s to %s.%s %s" % (_clock(q.get("start", 23)), _clock(q.get("end", 7)), now,
                                                     self._quiet_means()))

    def _quiet_means(self):
        hold = (self.j.settings.get("quiet_hours") or {}).get("hold", True)
        return "I speak softer and shorter then%s." % (", and keep heads-ups until they end" if hold else "")

    def _save_quiet(self, **change):
        q = dict(self.j.settings.get("quiet_hours") or {}, **change)
        self.j.settings["quiet_hours"] = q
        saved = self.j.memory.pref("quiet_hours")
        self.j.memory.set_pref("quiet_hours", dict(saved if isinstance(saved, dict) else {}, **change))


def temperature():
    """(hottest sensor in °C, its name) from /sys/class/thermal; (0, "") if there are none."""
    best = (0, "")
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            with open(zone + "/temp") as f:
                milli = int(f.read())
            with open(zone + "/type") as f:
                name = f.read().strip()
        except (OSError, ValueError):
            continue
        if 0 < milli < 150000 and milli // 1000 > best[0]:  # some zones read -40 or 200+ when off
            best = (milli // 1000, name)
    return best


def _hour(text, ampm, night):
    """"11", "p" -> 23. Without am/pm a quiet-hours start is in the evening ("from 10" = 10 pm)."""
    text = text.lower()
    n = HOURS.get(text, int(text) if text.isdigit() else -1)
    if not 0 <= n <= 23:
        return None
    if ampm:
        return (n % 12) + (12 if ampm.lower() == "p" else 0) if n <= 12 else None
    return n + 12 if night and 6 <= n < 12 else 0 if night and n == 12 else n


def _clock(hour):
    return "midnight" if hour == 0 else "noon" if hour == 12 else "%d %s" % (hour % 12 or 12,
                                                                            "pm" if hour > 12 else "am")
