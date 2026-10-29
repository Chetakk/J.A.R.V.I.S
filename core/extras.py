"""More skills JARVIS handles itself: dictation, maths and units, focus mode, "what's slowing my computer",
"where was I" and clipboard history. All instant and local; anything they can't answer falls through.

  "type: see you at five"         types it into the focused window (via the clipboard, which is restored)
  "start dictation" ... "stop dictation"   everything said in between is typed; "new line" presses Enter
  "what's 18% of 2400", "50 miles in km", "how many feet in 3 metres", "100 fahrenheit in celsius"
  "focus for 45 minutes" / "do not disturb" / "stop focusing"  no heads-ups, offers or banners meanwhile
  "what's slowing my computer"    top CPU/memory users; offers to close the top one
  "where was I"                   your last requests, what's still running, JARVIS's open windows
  "paste the thing I copied before", "paste number 3", "clipboard history"
"""
import ast
import collections
import json
import math
import operator
import os
import re
import signal
import subprocess
import threading
import time

from system import desktop

HOME = os.path.expanduser("~")
WHERE_FILE = os.path.join(HOME, ".local/share/jarvis/where.json")
FOCUS_FILE = os.path.join(HOME, ".local/share/jarvis/focus.json")
TERMINALS = ("gnome-terminal", "xterm", "terminator", "tilix", "kitty", "alacritty", "konsole")
# Never offered for closing: the desktop, sound, JARVIS itself and its helpers.
PROTECTED = {"gnome-shell", "xorg", "x", "systemd", "pulseaudio", "pipewire", "gdm", "gnome-session-b",
             "python3", "whisper-server", "piper", "nvargus-daemon", "dbus-daemon", "ibus-daemon"}
ORDINAL = {"last": 1, "previous": 2, "one before": 2, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
           "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}
NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
             "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "hundred": 100, "half an": 0.5}

# --- units: factor to a base unit, per kind ---
UNITS = {
    "length": {"mm": .001, "millimetre": .001, "cm": .01, "centimetre": .01, "m": 1, "metre": 1, "meter": 1,
               "km": 1000, "kilometre": 1000, "kilometer": 1000, "inch": .0254, "inches": .0254, "in": .0254,
               "foot": .3048, "feet": .3048, "ft": .3048, "yard": .9144, "mile": 1609.344, "mi": 1609.344},
    "weight": {"mg": 1e-6, "milligram": 1e-6, "g": .001, "gram": .001, "kg": 1, "kilo": 1, "kilogram": 1,
               "tonne": 1000, "ton": 1000, "ounce": .0283495, "oz": .0283495, "pound": .453592, "lb": .453592,
               "lbs": .453592, "stone": 6.35029},
    "volume": {"ml": .001, "millilitre": .001, "milliliter": .001, "l": 1, "litre": 1, "liter": 1,
               "cup": .236588, "pint": .473176, "gallon": 3.78541, "teaspoon": .00492892, "tablespoon": .0147868},
    "time": {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400,
             "week": 604800, "month": 2629746, "year": 31556952},
    "data": {"byte": 1, "kb": 1e3, "kilobyte": 1e3, "mb": 1e6, "megabyte": 1e6, "gb": 1e9, "gigabyte": 1e9,
             "tb": 1e12, "terabyte": 1e12, "gib": 2 ** 30, "mib": 2 ** 20},
    "speed": {"km/h": 1 / 3.6, "kmh": 1 / 3.6, "kph": 1 / 3.6, "kilometres per hour": 1 / 3.6,
              "kilometers per hour": 1 / 3.6, "mph": .44704, "miles per hour": .44704, "m/s": 1,
              "metres per second": 1, "knot": .514444},
}
TEMPS = {"c": "c", "celsius": "c", "centigrade": "c", "degrees celsius": "c", "f": "f", "fahrenheit": "f",
         "degrees fahrenheit": "f", "k": "k", "kelvin": "k"}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
SPOKEN_OPS = [(r"\bplus\b|\band\b", "+"), (r"\bminus\b|\btake away\b", "-"), (r"\btimes\b|\bmultiplied by\b|\bx\b", "*"),
              (r"\bdivided by\b|\bover\b", "/"), (r"\bto the power of\b|\bto the\b|\braised to\b", "**"),
              (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"), (r"\bmod\b|\bmodulo\b", "%"), ("×", "*"), ("÷", "/"),
              (r"\^", "**")]


def _unit(word):
    word = word.lower().strip().rstrip(".")
    if word in TEMPS:
        return "temp", TEMPS[word]
    for kind, table in UNITS.items():
        for name in (word, word[:-1] if word.endswith("s") else word, word.replace("meter", "metre")):
            if name in table:
                return kind, name
    return None, None


def _number(text):
    text = text.strip().lower().replace(",", "")
    if text in NUM_WORDS:
        return NUM_WORDS[text]
    return float(text)


def _spoken(x):
    """12.0 -> "12", 0.333333 -> "0.333", 1234567.8 -> "1,234,568"."""
    if x != x or x in (float("inf"), float("-inf")):
        return "undefined"
    if abs(x - round(x)) < 1e-9 and abs(x) < 1e15:
        return "{:,}".format(int(round(x)))
    if abs(x) >= 1000:
        return "{:,.0f}".format(x)
    digits = max(0, 3 - int(math.floor(math.log10(abs(x))))) if x else 0
    return ("%.*f" % (min(digits, 6), x)).rstrip("0").rstrip(".")


def calculate(expr):
    """Safely work out "18% of 2400", "12 times 7", "square root of 81"; None if it isn't maths."""
    e = expr.lower().strip().rstrip("?.")
    e = re.sub(r"^(?:what'?s|what is|calculate|compute|work out|how much is)\s+", "", e)
    m = re.fullmatch(r"([\d.,]+)\s*(?:%|percent) of ([\d.,]+)", e)
    if m:
        return _number(m.group(1)) / 100 * _number(m.group(2))
    m = re.fullmatch(r"(?:the )?square root of ([\d.,]+)", e)
    if m:
        return math.sqrt(_number(m.group(1)))
    for pattern, sym in SPOKEN_OPS:
        e = re.sub(pattern, sym, e)
    e = e.replace(",", "")
    if not re.fullmatch(r"[\d.\s+\-*/%()]+", e) or not re.search(r"\d\s*(?:\*\*|[+\-*/%])\s*[\d(]", e):
        return None

    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("too big")
            return OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.operand))
        raise ValueError("not maths")
    try:
        return float(ev(ast.parse(e, mode="eval").body))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None


def convert(amount, src, dst):
    """(value, unit word) or None if the units don't go together."""
    kind_a, a = _unit(src)
    kind_b, b = _unit(dst)
    if not kind_a or kind_a != kind_b:
        return None
    if kind_a == "temp":
        c = {"c": amount, "f": (amount - 32) * 5 / 9, "k": amount - 273.15}[a]
        return {"c": c, "f": c * 9 / 5 + 32, "k": c + 273.15}[b]
    return amount * UNITS[kind_a][a] / UNITS[kind_a][b]


def _cpu_ticks():
    """{pid: (name, cpu ticks, rss kB)} for the user's processes."""
    out, uid = {}, os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat("/proc/" + pid).st_uid != uid:
                continue
            with open("/proc/%s/stat" % pid) as f:
                stat = f.read()
            name = stat[stat.index("(") + 1:stat.rindex(")")]
            fields = stat[stat.rindex(")") + 2:].split()
            out[int(pid)] = (name, int(fields[11]) + int(fields[12]), int(fields[21]) * 4)
        except (OSError, ValueError, IndexError):
            pass
    return out


def top_processes(seconds=0.5):
    """[(name, cpu %, memory MB, [pids])] grouped by program, busiest first (measured over `seconds`)."""
    before = _cpu_ticks()
    time.sleep(seconds)
    after = _cpu_ticks()
    hz, groups = os.sysconf("SC_CLK_TCK"), {}
    for pid, (name, ticks, rss) in after.items():
        used = ticks - before.get(pid, (name, ticks))[1]
        g = groups.setdefault(name, [name, 0.0, 0.0, []])
        g[1] += 100.0 * used / hz / seconds
        g[2] += rss / 1024
        g[3].append(pid)
    return sorted(groups.values(), key=lambda g: (-g[1], -g[2]))


def _clipboard():
    try:
        return subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, text=True,
                              timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _set_clipboard(text):
    subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), timeout=2,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ago(secs):
    if secs < 90:
        return "just now"
    if secs < 3600:
        return "%d minutes ago" % (secs // 60)
    if secs < 7200:
        return "about an hour ago"
    if secs < 86400:
        return "about %d hours ago" % (secs // 3600)
    return "%d days ago" % (secs // 86400)


class Extras:
    def __init__(self, jarvis):
        self.j = jarvis
        self.say = jarvis.speaker.say
        self.dictating = False
        self.copies = collections.deque(maxlen=10)  # clipboard history, newest last; memory only, never saved
        self._ignore_clip_until = 0.0  # JARVIS's own clipboard use (typing) isn't a copy of yours
        self._focus_timer = None
        self.focus_until = 0.0
        self._restore_focus()
        n = r"([\d.,]+|%s)" % "|".join(sorted(NUM_WORDS, key=len, reverse=True))
        rules = [
            # dictation
            (r"(?:start|begin) (?:dictation|dictating|typing)|dictation mode|take dictation", lambda m: self.start_dictation()),
            (r"type(?: this| that| out)?[:,]? (.+)", lambda m: self.type_text(m.group(1))),
            # maths and units
            (r"(?:what'?s |what is |convert )?%s ?([a-z/ ]+?) (?:in|to|into|in to) ([a-z/ ]+)" % n, self.units),
            (r"how many ([a-z/ ]+?) (?:are )?(?:in|is) %s ?([a-z/ ]+)" % n, self.units_how_many),
            (r"(?:what'?s |what is |calculate |compute |work out |how much is )?(.*\d.*)", self.maths),
            # focus mode
            (r"(?:start )?(?:focus(?: mode)?|do not disturb|don'?t disturb me|quiet mode)"
             r"(?: for %s ?(minutes?|mins?|hours?))?|let me focus(?: for %s ?(minutes?|mins?|hours?))?" % (n, n),
             self.focus),
            (r"(?:stop|end|cancel|turn off|exit) (?:focus(?:ing| mode)?|do not disturb|quiet mode)|i'?m done focusing",
             lambda m: self.end_focus()),
            (r"how (?:long|much time) (?:is )?left (?:on|in) (?:my )?focus(?: mode)?", lambda m: self.focus_left()),
            # slow computer
            (r"what'?s (?:slowing (?:down )?|hogging |using (?:up )?)(?:my |the )?(?:computer|system|jetson|cpu|"
             r"memory|ram)(?: down)?|why is (?:my |the )?(?:computer|system|jetson|it|everything) (?:so )?slow|"
             r"what'?s using (?:all )?(?:my |the )?(?:cpu|memory|ram)|(?:show )?(?:the )?top processes",
             lambda m: self.slow()),
            # where was I
            (r"where was i|where did i (?:leave|stop) off|catch me up|what did i leave (?:open|running)",
             lambda m: self.where_was_i()),
            # clipboard history
            (r"paste (?:the )?(?:thing|text|one) i copied (?:before|earlier|before that)|paste (?:the )?previous "
             r"(?:copy|clipboard|thing)|paste (?:the )?(last|previous|second|2nd|third|3rd|fourth|4th|fifth|5th)"
             r"(?: last)? (?:copy|thing i copied|one)|paste (?:number |copy )(\d+)", self.paste_copy),
            (r"(?:show |read |what'?s in )?(?:my |the )?clipboard history|what did i copy before|"
             r"what (?:else )?have i copied", lambda m: self.copy_history()),
        ]
        self.rules = [(re.compile("^(?:%s)$" % p, re.IGNORECASE), fn) for p, fn in rules]

    # --- called by jarvis.handle ---

    def first(self, text):
        """Runs before everything else: remembers the request for "where was I", and types it while dictating."""
        if not self.dictating:
            if not re.match(r"^(?:where was i|where did i (?:leave|stop) off|catch me up)\b", text, re.IGNORECASE):
                self._log(text)
            return False
        low = text.lower().strip(" .!?")
        if re.fullmatch(r"(?:stop|end|finish|cancel|exit) (?:dictation|dictating|typing)|that'?s all|i'?m done", low):
            self.dictating = False
            self.say("Dictation off.")
        elif low in ("new line", "next line", "press enter", "enter"):
            desktop.keys("Return")
        elif low in ("new paragraph",):
            desktop.keys("Return")
            desktop.keys("Return")
        elif low in ("undo", "undo that", "delete that", "scratch that"):
            desktop.keys("ctrl+z")
        else:
            self._paste(text.strip() + " ")
        return True

    def handle(self, text):
        text = text.strip().rstrip("?.!")
        for pattern, fn in self.rules:
            m = pattern.match(text)
            if m:
                try:
                    done = fn(m)
                except Exception as e:  # a broken skill must never take JARVIS down
                    print("[extras] %s failed: %r" % (getattr(fn, "__name__", "skill"), e))
                    self.say("Sorry, that didn't work: %s." % e)
                    return True
                if done is not False:
                    return True
        return False

    def focusing(self):
        return time.time() < self.focus_until

    # --- dictation ---

    def start_dictation(self):
        self.dictating = True
        self.say("Dictation on. Say stop dictation when you're done.")

    def type_text(self, text):
        self._paste(text.strip())

    def _paste(self, text):
        """Type `text` into the focused window: put it on the clipboard, paste, then put yours back."""
        win = desktop.active_window()
        if win and "jarvis" in win.wm_class:
            self.say("Click where you want it typed first.")
            return
        old = _clipboard()
        self._ignore_clip_until = time.time() + 1.5
        _set_clipboard(text)
        terminal = win and any(t in win.wm_class for t in TERMINALS)
        desktop.keys("ctrl+shift+v" if terminal else "ctrl+v")
        time.sleep(0.25)  # let the app read the clipboard before it changes back
        _set_clipboard(old)

    # --- maths and units ---

    def maths(self, m):
        value = calculate(m.group(0))
        if value is None:
            return False
        self.say(_spoken(value) + ".")

    def units(self, m):
        return self._convert(m.group(1), m.group(2), m.group(3))

    def units_how_many(self, m):
        return self._convert(m.group(2), m.group(3), m.group(1))

    def _convert(self, amount, src, dst):
        try:
            amount = _number(amount)
        except ValueError:
            return False
        value = convert(amount, src.strip(), dst.strip())
        if value is None:
            return False  # e.g. currencies: Claude can try
        self.say("%s %s is %s %s." % (_spoken(amount), src.strip(), _spoken(value), dst.strip()))

    # --- focus mode ---

    def focus(self, m):
        groups = [g for g in m.groups() if g]
        minutes = 45
        if len(groups) == 2:
            minutes = _number(groups[0]) * (60 if groups[1].startswith("h") else 1)
        self._start_focus(time.time() + minutes * 60)
        self.say("Focus mode for %s. I'll keep quiet and hold notifications." % (
            "%d minutes" % minutes if minutes < 120 else "%s hours" % _spoken(minutes / 60)))

    def _start_focus(self, until):
        self.focus_until = until
        _banners(False)
        try:
            with open(FOCUS_FILE, "w") as f:
                json.dump({"until": until}, f)
        except OSError:
            pass
        if self._focus_timer:
            self._focus_timer.cancel()
        self._focus_timer = threading.Timer(max(0, until - time.time()), self._focus_over)
        self._focus_timer.daemon = True
        self._focus_timer.start()

    def _focus_over(self):
        if self.focus_until and time.time() >= self.focus_until - 1:
            self._clear_focus()
            self.j.inbox.put(("say", "Focus time's over. Nice work."))

    def end_focus(self):
        if not self.focusing():
            self.say("You weren't in focus mode.")
            return
        self._clear_focus()
        self.say("Focus mode off.")

    def _clear_focus(self):
        self.focus_until = 0.0
        if self._focus_timer:
            self._focus_timer.cancel()
            self._focus_timer = None
        _banners(True)
        try:
            os.remove(FOCUS_FILE)
        except OSError:
            pass

    def _restore_focus(self):
        """After a restart: carry on a focus session, or turn notifications back on if it ran out meanwhile."""
        try:
            with open(FOCUS_FILE) as f:
                until = json.load(f)["until"]
        except (OSError, ValueError, KeyError):
            return
        if until > time.time():
            self._start_focus(until)
        else:
            self._clear_focus()

    def focus_left(self):
        if not self.focusing():
            self.say("You're not in focus mode.")
        else:
            self.say("%d minutes left." % max(1, round((self.focus_until - time.time()) / 60)))

    # --- slow computer ---

    def slow(self):
        procs = top_processes()
        cpu = [p for p in procs if p[1] >= 5][:3]
        heavy = sorted(procs, key=lambda p: -p[2])[:3]
        free = _meminfo("MemAvailable") / 2 ** 20  # GB
        lines = ["%s: %d%% CPU, %d MB" % (p[0], p[1], p[2]) for p in (cpu + [h for h in heavy if h not in cpu])]
        self.j.show({"title": "What's using the computer", "text": "\n".join("- " + l for l in lines)
                     + "\n\nMemory free: %.1f GB" % free})
        if cpu:
            spoken = "%s is using the most CPU, %d percent" % (cpu[0][0], cpu[0][1])
        else:
            spoken = "Nothing's working the CPU hard right now"
        spoken += ". The biggest on memory is %s at %s." % (heavy[0][0], _mb(heavy[0][2]))
        if free < 1.5:
            spoken += " Memory is low, only %.1f gigabytes free." % free
        target = next((p for p in cpu + heavy if p[0].lower() not in PROTECTED), None)
        if not target or (target[1] < 30 and free >= 1.5):
            self.say(spoken)
            return
        if self.j.ask(spoken + " Want me to close %s?" % target[0]):
            for pid in target[3]:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            self.say("Closed %s." % target[0])
        else:
            self.say("Okay, leaving it.")

    # --- where was I ---

    def _log(self, text):
        try:
            with open(WHERE_FILE) as f:
                log = json.load(f)
        except (OSError, ValueError):
            log = []
        log = (log + [[time.time(), text]])[-20:]
        try:
            with open(WHERE_FILE, "w") as f:
                json.dump(log, f)
        except OSError:
            pass

    def where_was_i(self):
        try:
            with open(WHERE_FILE) as f:
                log = json.load(f)
        except (OSError, ValueError):
            log = []
        parts, card = [], []
        if log:
            when, last = log[-1]
            parts.append("The last thing you asked me, %s, was: %s." % (_ago(time.time() - when), last))
            card += ["**Your last requests**"] + ["- %s — %s" % (t, _ago(time.time() - w)) for w, t in log[-5:][::-1]]
        running = list(self.j.projects.running())
        if running:
            parts.append("Still running: %s." % ", ".join(running))
            card += ["", "**Running**"] + ["- " + r for r in running]
        ws = int((self.j.settings.get("workspace") or {}).get("number", 5))
        open_here = [w.title for w in desktop.windows() if w.workspace == ws and w.title and "jarvis" not in w.wm_class]
        if open_here:
            card += ["", "**Open on workspace %d**" % ws] + ["- " + t for t in open_here]
        if self.focusing():
            parts.append("You're in focus mode for %d more minutes." % round((self.focus_until - time.time()) / 60))
        if not parts and not open_here:
            self.say("I don't have anything from before, we're starting fresh.")
            return
        self.j.show({"title": "Where you left off", "text": "\n".join(card)})
        if open_here and self.j.ask(" ".join(parts) + " You've got %d window%s open on my workspace. Take you there?"
                                    % (len(open_here), "" if len(open_here) == 1 else "s")):
            desktop.switch_to(ws)
        elif not open_here:
            self.say(" ".join(parts))

    # --- clipboard history ---

    def watch_clipboard(self):
        """Call from the GTK thread (needs the UI): remember each copy, newest last."""
        from gi.repository import Gdk, Gtk
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        def got(_clip, text):
            if text and text.strip() and time.time() > self._ignore_clip_until:
                if text in self.copies:
                    self.copies.remove(text)
                self.copies.append(text)
        clip.connect("owner-change", lambda c, _e: c.request_text(got))
        clip.request_text(got)
        return False  # for GLib.idle_add: run once

    def paste_copy(self, m):
        which = m.group(2) and int(m.group(2)) or ORDINAL.get((m.group(1) or "previous").lower(), 2)
        if len(self.copies) < which:
            self.say("I only remember %d thing%s you copied." % (len(self.copies), "" if len(self.copies) == 1 else "s")
                     if self.copies else "I haven't seen you copy anything yet.")
            return
        text = self.copies[-which]
        self._paste(text)
        self.copies.remove(text)
        self.copies.append(text)  # pasting it makes it the newest again, like copying it
        _set_clipboard(text)

    def copy_history(self):
        if not self.copies:
            self.say("I haven't seen you copy anything yet.")
            return
        items = list(self.copies)[::-1]
        self.j.show({"title": "Clipboard history",
                     "text": "\n".join("%d. %s" % (i + 1, (t.strip()[:120] + ("…" if len(t.strip()) > 120 else ""))
                                                   .replace("\n", " ")) for i, t in enumerate(items))})
        self.say("Here's what you copied, newest first. Say paste number 2 to use one.")


def _banners(on):
    """GNOME's notification pop-ups (the urgent ones still show during focus)."""
    subprocess.run(["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "true" if on else "false"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)


def _meminfo(key):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1])
    return 0


def _mb(mb):
    return "%.1f gigabytes" % (mb / 1024) if mb >= 1024 else "%d megabytes" % mb
