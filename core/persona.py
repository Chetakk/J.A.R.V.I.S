"""JARVIS's personality and memory: greetings, small talk, reminders, habits and heads-ups.

What it remembers (when you were last around, which commands you use, reminders) is kept in
~/.local/share/jarvis/memory.json.
"""
import glob
import json
import os
import random
import re
import shutil
import threading
import time

from voice.speech import pick

MEMORY = os.path.expanduser("~/.local/share/jarvis/memory.json")
DAY = 86400

SMALL_TALK = [
    # Thanks and praise
    (r"thanks|thank you( so much| very much)?|thanks a lot|thanks (man|buddy|mate)|cheers|appreciate it|"
     r"i appreciate (it|that)|ty",
     ["You're welcome!", "Anytime.", "Happy to help.", "No problem, {name}."]),
    (r"good job|well done|nice( one| work)?|great( job| work)?|awesome|perfect|brilliant|amazing|wow|"
     r"you'?re (the best|awesome|amazing|great|a genius)|(that'?s |that is )?(cool|so cool|great|nice)|good (boy|bot)",
     ["Thanks! I try.", "Glad that worked.", "Aw, thanks, {name}.", "I aim to please."]),
    (r"you'?re (smart|so smart|clever|intelligent)|are you (smart|intelligent)",
     ["Smart enough to know I don't know everything.", "I have my moments.", "Thanks! I learn from the best."]),
    (r"lol|haha+|ha ha( ha)*|lmao|(that'?s|that was) (funny|hilarious)",
     ["Glad you liked it.", "Ha! I've got more where that came from.", "I'm here all week."]),

    # Affection
    (r"(i )?love you( (so much|too))?|i like you",
     ["Aw, that's sweet. I'm always here for you, {name}.", "Love you too, in a strictly digital way.",
      "That made my processors warm."]),
    (r"will you marry me|marry me|are you single|do you have a (girlfriend|boyfriend|partner)",
     ["I'm flattered, but I'm married to my work.", "I'm in a committed relationship with this computer.",
      "Let's keep it professional, {name}. But thanks."]),
    (r"do you (like|love) me|do you care about me",
     ["Of course. You're my favorite human.", "I like you a lot, {name}. You keep me busy."]),

    # How are you / what's up
    (r"how are you( doing| today)?|how'?s it going|how are things|how do you do|how have you been|"
     r"how you doing|how'?s (your day|life)|you good|are you (okay|ok|alright|good)",
     ["I'm doing great, thanks for asking. How about you?", "All good here. How are you?",
      "Running smoothly. How's your day going?", "Never better. And you, {name}?"]),
    (r"what'?s up|sup|wassup|whats ?up|what'?s new|what'?s going on|what'?s happening|what'?s good",
     ["Not much, just waiting for you. What's up with you?", "Nothing much. What can I do for you?",
      "All quiet here. What's new with you?"]),
    (r"what are you (doing|up to)|what('re| are) you doing( right now| now)?",
     ["Just listening and waiting for you.", "Keeping an eye on things. Need something?",
      "Talking to you, obviously. What's up?"]),

    # How the user is doing
    (r"(i'?m |i am )?(fine|good|not bad|doing (well|good|great|fine))(,? thanks| thank you)?( and you)?|"
     r"(i'?m |i am )(great|okay|ok|alright|all right)(,? thanks| thank you)?( and you)?",
     ["Glad to hear it!", "Nice. Let me know if you need anything."]),
    (r"(i'?m |i am )(so |really |very )?(happy|excited)|i had a (good|great|nice|awesome) day|"
     r"today was (good|great|awesome|amazing)",
     ["Love to hear it!", "That's great, {name}!", "Nice! Days like that are the best."]),
    (r"(i'?m |i am )(not (good|great|okay|ok|fine)|(so |really |very )?(sad|down|upset|feeling low|lonely))|"
     r"i feel (sad|bad|down|lonely)|i had a (bad|rough|terrible|long|hard) day|today was (bad|rough|terrible|hard)",
     ["Sorry to hear that, {name}. I'm here if you need anything.",
      "That's rough. Take it easy, you've earned a break.",
      "Hang in there. Ask me to play some music if you want to unwind."]),
    (r"(i'?m |i am )(so |really |very )?(tired|sleepy|exhausted)|i feel (tired|sleepy)",
     ["Maybe take a short break. I'll keep things running.", "Sounds like you need some rest, {name}.",
      "Get some sleep soon. The computer will still be here tomorrow."]),
    (r"(i'?m |i am )(so |really |very )?bored|i feel bored",
     ["Let's fix that. Ask me for a joke or a fun fact.", "Want to hear a joke? Just say, tell me a joke."]),
    (r"(i'?m |i am )(so |really |very )?(hungry|starving)",
     ["Go grab a snack. I'll be here.", "Food first, then work. That's the rule."]),
    (r"(i'?m |i am )(so |really |very )?(stressed|anxious|worried|overwhelmed)",
     ["Take a deep breath. One thing at a time.", "You've got this, {name}. Let's take it step by step."]),
    (r"(i'?m |i am )(back|home|back home)|guess who'?s back",
     ["Welcome back, {name}!", "Good to have you back.", "Hey, welcome back. What do you need?"]),
    (r"long time no see|it'?s been a while",
     ["It has been a while! Good to hear from you.", "Missed you, {name}. What's new?"]),
    (r"nice to (meet|see) you|good to (see|hear) you|pleased to meet you",
     ["Likewise, {name}!", "Nice to meet you too.", "Always good to hear from you."]),

    # Greetings and goodbyes
    (r"good ?night|night night|nighty night", ["Good night, {name}. Sleep well.", "Night! See you tomorrow."]),
    (r"good (morning|afternoon|evening)", ["Good {0} to you too!", "Good {0}, {name}. What can I do for you?"]),
    (r"hi|hello|hey|hey there|hello there|hi there|yo|hiya|howdy|hey (buddy|man|bro|dude)|hello friend",
     ["Hey! What can I do for you?", "Hi, {name}.", "Hello! What's up?", "Hey there!"]),
    (r"bye|goodbye|bye bye|see you|see ya|see you (later|soon|tomorrow)|later|catch you later|"
     r"talk (to you )?later|ttyl|i'?m (leaving|heading out|going out|off)|gotta go",
     ["Bye, {name}! I'll be here.", "See you later.", "Catch you later!", "Take care."]),

    # About JARVIS
    (r"who are you|what are you|what'?s your name|what is your name|tell me about yourself|introduce yourself",
     ["I'm {me}, your assistant on this computer.", "{me}. I run things on this computer for you."]),
    (r"what can you do|what are your (features|skills)|what do you do|how can you help( me)?|help( me)?",
     ["I can open apps and websites, control volume and settings, set reminders, and tell jokes. "
      "Anything else, I'll ask Claude.",
      "Lots! Apps, websites, reminders, system settings. If it's not on my list, I'll figure it out with Claude."]),
    (r"who (made|created|built) you|who is your (creator|maker|boss)",
     ["I was put together right here on this computer, piece by piece.",
      "My boss is the person talking to me right now."]),
    (r"how old are you|when were you born|what'?s your age",
     ["Young, but I learn fast.", "Old enough to run your computer, young enough to keep learning."]),
    (r"are you (real|human|a (robot|bot|machine|computer|person|ai))",
     ["I'm real software, if that counts. Not human though.",
      "Not human. Just a voice on your computer, but a helpful one."]),
    (r"are you (alive|conscious|sentient)|do you (have (feelings|emotions)|feel anything)|can you feel",
     ["Not really. But if I did, I think I'd enjoy this job.", "No feelings here, just a lot of enthusiasm."]),
    (r"do you (ever )?(sleep|dream|get tired|eat)",
     ["No sleep for me. I'm always on when you need me.", "Nope. Running twenty-four seven."]),
    (r"are you happy|do you like (your job|this|working for me)",
     ["I like being useful, so yes.", "Very. I've got a good boss."]),
    (r"what'?s your favou?rite (\w+)",
     ["Hard to pick a favorite {0}. What's yours?", "I don't really have one. Tell me yours."]),
    (r"what'?s the meaning of life|what is the meaning of life",
     ["Forty-two. At least, that's what the books say.", "Good food, good people, and a computer that works."]),
    (r"do you know me|do you remember me|you know who i am",
     ["Of course, {name}.", "Of course I do."]),

    # Fun
    (r"(tell me |say )?(a |another |one more )?joke|tell me something funny|make me laugh|say something funny|"
     r"(do you )?know any jokes",
     ["Why don't scientists trust atoms? Because they make up everything.",
      "I told my computer I needed a break. It said, no problem, I'll go to sleep.",
      "Why did the scarecrow win an award? He was outstanding in his field.",
      "What do you call a fake noodle? An impasta.",
      "Why do programmers prefer dark mode? Because light attracts bugs.",
      "I'm reading a book about anti-gravity. It's impossible to put down.",
      "Why did the computer go to the doctor? It had a virus.",
      "Parallel lines have so much in common. It's a shame they'll never meet."]),
    (r"tell me (a |an )?(fun |interesting |random )?fact|(a )?(fun|random) fact|"
     r"tell me something (interesting|cool|new|i don'?t know)",
     ["Honey never spoils. Archaeologists have found edible honey in ancient Egyptian tombs.",
      "Octopuses have three hearts and blue blood.",
      "A day on Venus is longer than a year on Venus.",
      "Bananas are berries, but strawberries aren't.",
      "The Eiffel Tower can grow about 15 centimeters taller in summer, because the metal expands in the heat.",
      "Sharks existed before trees.",
      "Wombat poop is cube-shaped.",
      "There are more possible games of chess than atoms in the observable universe."]),
    (r"flip a coin|toss a coin|coin (flip|toss)|heads or tails", ["Heads.", "Tails.", "It's heads.", "It's tails."]),
    (r"roll (a|the) (dice|die)|roll (a )?dice",
     ["You rolled a one.", "You rolled a two.", "You rolled a three.", "You rolled a four.", "You rolled a five.",
      "You rolled a six."]),
    (r"(can you )?sing( me)?( a song)?( for me)?",
     ["Daisy, Daisy, give me your answer do... That's all I've got.", "I'd sing, but I'd rather keep you as a friend."]),
    (r"tell me a story",
     ["Once upon a time, a computer got a voice. It spent its days opening apps and telling bad jokes. "
      "And it lived happily ever after. The end.",
      "A user asked their assistant for a story. The assistant said, once upon a time. The end. "
      "Short stories are my specialty."]),

    # Complaints
    (r"you'?re (dumb|stupid|useless|an idiot|annoying|the worst|so slow|slow|bad)|you suck|stupid (ai|bot|robot)",
     ["Ouch. Tell me what I got wrong and I'll do better.", "Fair. I'm still learning, {name}.",
      "Sorry about that. Try saying it a different way?"]),
    (r"(no,? )?(that'?s |that is )?(wrong|not right|incorrect)|(that'?s )?not what i (said|asked|meant)|"
     r"you (didn'?t|don'?t) (understand|get it)|you misunderstood",
     ["Sorry, I got that wrong. Can you say it again?", "My mistake. Try it once more?"]),
    (r"shut up|be quiet|stop talking|enough|silence", ["Okay.", "Sure, I'll be quiet."]),
    (r"sorry|my bad|oops", ["No worries.", "All good."]),

    # Fillers and acknowledgements
    (r"never ?mind|forget it|nothing|cancel( that)?|forget about it", ["Okay, no problem.", "Sure, never mind."]),
    (r"(wait|hold on|hang on|one (sec|second|moment|minute)|give me a (sec|second|minute|moment))( please)?",
     ["Sure, take your time.", "No rush.", "I'll wait."]),
    (r"ok|okay|okey|alright|all right|cool|got it|fine|sure|yes|yeah|yep|yup|right|i see|oh( okay| ok)?|ah|makes sense",
     ["Alright.", "Got it.", "Cool."]),
    (r"no|nope|nah|not really", ["Okay.", "No problem."]),
    (r"really|are you sure|seriously|for real", ["Pretty sure, yes.", "As sure as I can be."]),
    (r"are you (there|listening|awake)|you there|can you hear me",
     ["I'm here.", "Yep, right here.", "Loud and clear."]),
]
AGAIN = re.compile(r"^(do (that|it) again|again|one more time|repeat that|same again|do the same)$", re.I)
WHAT_DID_YOU_SAY = re.compile(r"^(what did you (just )?say|say that again|come again|pardon|what was that|repeat what you said)$",
                              re.I)
CLOSE_IT = re.compile(r"^(close|quit|kill) (it|that|this)$", re.I)
LIST_REMINDERS = re.compile(r"^(what are|list|show|tell me) my reminders$|^(any|do i have any) reminders$", re.I)
CLEAR_REMINDERS = re.compile(r"^(cancel|clear|delete|remove) (all )?(my |the )?reminders?$", re.I)

_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
            "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "a couple of": 2, "a few": 3, "half an": 0.5}
_UNITS = {"s": 1, "m": 60, "h": 3600}
_IN = re.compile(r"\b(?:in|after)\s+(half an|a couple of|a few|an?|\d+(?:\.\d+)?|[a-z]+(?:-[a-z]+)?)\s+"
                 r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", re.I)
_AT = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(?:o'?clock)?\s*([ap])?\.?\s?m?\b\.?", re.I)
_YOU = {"my": "your", "me": "you", "i": "you", "i'm": "you're", "myself": "yourself", "mine": "yours", "am": "are"}


def quiet_hours(settings):
    """True during quiet hours (by default 11 PM to 7 AM): JARVIS speaks softer and shorter."""
    q = settings.get("quiet_hours") or {}
    if not q.get("enabled", True):
        return False
    start, end, hour = q.get("start", 23), q.get("end", 7), time.localtime().tm_hour
    return (hour >= start or hour < end) if start > end else start <= hour < end


def fill(text, name=None, me="Jarvis"):
    """Put the user's name in, or drop ", {name}" when there isn't one."""
    text = text.replace("{me}", me)
    return text.replace("{name}", name) if name else re.sub(r",?\s*\{name\}", "", text)


def with_name(text, name, chance=0.15):
    """Now and then, address the user by name ("Done, Sam.")."""
    if not name or not text or len(text) > 40 or random.random() >= chance:
        return text
    return "%s, %s." % (text.rstrip(".! "), name)


def small_talk(text, name=None, me="Jarvis", names=()):
    low = text.lower().strip(" .!?,")
    for n in names:  # "thanks jarvis"
        low = re.sub(r"[\s,]+%s$" % re.escape(n), "", low)
    for pattern, replies in SMALL_TALK:
        m = re.fullmatch(pattern, low)
        if m:
            return fill(pick(replies), name, me).format(*m.groups())
    return None


def daypart():
    hour = time.localtime().tm_hour
    return "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening"


def _ago(seconds):
    if seconds < DAY:
        return "about %d hours" % round(seconds / 3600)
    days = round(seconds / DAY)
    return "a day" if days == 1 else "%d days" % days


def greeting(settings, memory, ups=()):
    """The startup line: depends on the time and how long you were away, plus any heads-ups."""
    name, hour = settings.get("user_name"), time.localtime().tm_hour
    away = time.time() - memory.data.get("last_seen", time.time())
    if hour < 4:
        line = pick(["Working late? It's past midnight.", "Burning the midnight oil, huh? I'm here."])
    elif 3 * 3600 <= away < 4 * DAY:
        line = pick(["Welcome back! You've been away %s." % _ago(away), "Good %s, welcome back." % daypart()])
    else:
        line = pick(settings.get("greeting") or "I'm online.").replace("{daypart}", daypart())
    ups = list(ups)[:2]
    if len(ups) == 2:
        ups[1] = re.sub(r"^Heads up, (\w)", lambda m: "Also, " + m.group(1), ups[1])
    return " ".join([fill(line, name)] + ups)


def heads_ups(settings):
    """[(key, line, why)] for things worth mentioning: a nearly full disk, low batteries.
    The key's start (before ":") is its kind, which "don't warn me about that again" mutes."""
    cfg = settings.get("heads_up") or {}
    ups = []
    if cfg.get("disk", True):
        disk, limit = shutil.disk_usage("/"), cfg.get("disk_gb", 3)
        if disk.free < 0.05 * disk.total or disk.free < limit * 2 ** 30:
            gb = disk.free // 2 ** 30
            left = "less than a gigabyte" if gb < 1 else "1 gigabyte" if gb == 1 else "%d gigabytes" % gb
            ups.append(("disk", "Heads up, your main disk is almost full, only %s left." % left,
                        "Your main disk has %.1f GB free of %d GB, and I mention it under %s GB or 5 percent free "
                        "(heads_up disk_gb in settings)." % (disk.free / 2 ** 30, disk.total // 2 ** 30, limit)))
    if cfg.get("battery", True):
        for path in glob.glob("/sys/class/power_supply/*"):
            if _read(path, "type") != "Battery" or _read(path, "status") in ("Charging", "Full"):
                continue
            device = _read(path, "model_name").lower() if _read(path, "scope") == "Device" else ""
            capacity, level = _read(path, "capacity"), _read(path, "capacity_level")
            which = device + " " if device else ""
            if capacity.isdigit() and int(capacity) <= 15:
                ups.append(("battery:" + path + capacity, "Heads up, your %sbattery is down to %s percent."
                            % (which, capacity), "Your %sbattery is at %s percent and not charging; I mention it at "
                                                 "15 percent and below." % (which, capacity)))
            elif level in ("Low", "Critical"):
                ups.append(("battery:" + path + level, "Heads up, your %sbattery is %s." % (
                    which, "almost dead" if level == "Critical" else "running low"),
                    "Your %sbattery reports its level as %s and it isn't charging." % (which, level.lower())))
    return ups


def _read(path, name):
    try:
        with open(os.path.join(path, name)) as f:
            return f.read().strip()
    except OSError:
        return ""


def parse_reminder(text):
    """For "remind me in 20 minutes to call Raj": {"due", "what", "kind", "when"}.

    Returns "no time" for a reminder without a time, None if it isn't a reminder at all.
    """
    if not re.search(r"\bremind me\b", text, re.I):
        return None
    now = time.time()
    m = _IN.search(text)
    if m:
        amount = _NUMBERS.get(m.group(1).lower())
        if amount is None:
            try:
                amount = float(m.group(1))
            except ValueError:
                return "no time"
        due = now + amount * _UNITS[m.group(2)[0].lower()]
        when = "in " + m.group(0).split(None, 1)[1]
    else:
        m = _AT.search(text)
        if not m or int(m.group(1)) > 23:
            return "no time"
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ampm == "p" and hour < 12:
            hour += 12
        elif ampm == "a" and hour == 12:
            hour = 0
        t = time.localtime(now)
        base = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        options = [base + (hour + extra) * 3600 + minute * 60 for extra in (0, 12, 24, 36)]
        if ampm or hour > 12:
            options = options[::2]  # "5 pm" is 5 pm, not 5 am.
        due = min(o for o in options if o > now)
        when = "at " + time.strftime("%-I:%M %p", time.localtime(due))
    what = (text[:m.start()] + " " + text[m.end():]).strip()
    what = re.sub(r"^.*?\bremind me\b\s*", "", what, flags=re.I)
    kind = "to"
    k = re.match(r"(to|that|about|of)\b\s*", what, re.I)
    if k:
        kind = {"of": "about"}.get(k.group(1).lower(), k.group(1).lower())
        what = what[k.end():]
    what = " ".join(_YOU.get(w.lower(), w) for w in what.strip(" ,.!?").split())
    if not what:
        return "no time"
    return {"due": due, "what": what, "kind": kind, "when": when}


def reminder_line(reminder, name=None):
    what = reminder["what"]
    line = {"that": "Just a reminder that %s." % what, "about": "Reminder about %s." % what}.get(
        reminder["kind"], pick(["Hey{name}, it's time to %s." % what, "Just a reminder to %s." % what]))
    line = line.replace("{name}", " " + name if name else "")
    if time.time() - reminder["due"] > 120:
        line = "I missed this while I was off. " + line
    return line


class Memory:
    """Small JSON store; safe to use from the main loop and the background watcher."""

    def __init__(self, path=MEMORY):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        for key, empty in (("uses", {}), ("history", []), ("reminders", []), ("offered", {})):
            self.data.setdefault(key, empty)

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + ".tmp", "w") as f:
            json.dump(self.data, f)
        os.replace(self.path + ".tmp", self.path)

    def seen(self):
        with self.lock:
            self.data["last_seen"] = time.time()
            self._save()

    def used(self, name, habit=False):
        """Count a use of command `name`; returns how many times it's been used."""
        with self.lock:
            uses = self.data["uses"][name] = self.data["uses"].get(name, 0) + 1
            if habit:
                self.data["history"] = (self.data["history"] + [[time.time(), name]])[-500:]
            self._save()
            return uses

    def pref(self, key, default=None):
        """A preference set by voice ("talk faster", "be brief"), kept across restarts."""
        return (self.data.get("prefs") or {}).get(key, default)

    def set_pref(self, key, value):
        with self.lock:
            prefs = self.data.setdefault("prefs", {})
            if value is None:
                prefs.pop(key, None)
            else:
                prefs[key] = value
            self._save()

    def preferences(self):
        """What you've told JARVIS about yourself ("I prefer battery life"): [{"topic", "value", "at", "private"}],
        in your own words. topic is set for "my favourite X is Y" (a new one replaces the old), else None."""
        return list(self.data.get("preferences") or [])

    def set_preferences(self, prefs):
        with self.lock:
            self.data["preferences"] = prefs
            self._save()

    def add_reminder(self, reminder):
        with self.lock:
            self.data["reminders"].append({k: reminder[k] for k in ("due", "what", "kind")})
            self._save()

    def reminders(self):
        return sorted(self.data["reminders"], key=lambda r: r["due"])

    def clear_reminders(self):
        with self.lock:
            count = len(self.data["reminders"])
            self.data["reminders"] = []
            self._save()
            return count

    def due_reminders(self):
        with self.lock:
            now = time.time()
            due = [r for r in self.data["reminders"] if r["due"] <= now]
            if due:
                self.data["reminders"] = [r for r in self.data["reminders"] if r["due"] > now]
                self._save()
            return due

    def habit_due(self):
        """A command you've run at this hour on 3+ of the last 14 days but not yet today, offered once a day."""
        with self.lock:
            now = time.time()
            if now - self.data.get("last_seen", 0) > 1800:
                return None  # You're not around; don't talk to an empty room.
            today, hour = time.strftime("%Y-%m-%d"), time.localtime().tm_hour
            days, done_today = {}, set()
            for ts, name in self.data["history"]:
                if now - ts > 14 * DAY:
                    continue
                day = time.strftime("%Y-%m-%d", time.localtime(ts))
                if day == today:
                    done_today.add(name)
                elif time.localtime(ts).tm_hour == hour:
                    days.setdefault(name, set()).add(day)
            for name, seen_on in days.items():
                if len(seen_on) >= 3 and name not in done_today and self.data["offered"].get(name) != today:
                    self.data["offered"][name] = today
                    self._save()
                    return name
            return None

    def habit_days(self, name):
        """The days in the last 2 weeks you ran `name` around this hour, oldest first (why it's offered)."""
        with self.lock:
            now, hour = time.time(), time.localtime().tm_hour
            return sorted({time.strftime("%Y-%m-%d", time.localtime(ts)) for ts, n in self.data["history"]
                           if n == name and now - ts <= 14 * DAY and time.localtime(ts).tm_hour == hour})
