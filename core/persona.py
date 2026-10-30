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

from core.store import keep_damaged, load_json, save_json
from voice.speech import pick

MEMORY = os.path.expanduser("~/.local/share/jarvis/memory.json")
DAY = 86400

SMALL_TALK = [
    # Thanks and praise
    (r"thanks|thank you( so much| very much)?|thanks a lot|thanks (man|buddy|mate)|cheers|appreciate it|"
     r"i appreciate (it|that)|ty",
     ["You're welcome, {name}.", "My pleasure.", "Anytime, {name}.", "For you, {name}, always.",
      "It will be my pleasure."]),
    (r"good job|well done|nice( one| work)?|great( job| work)?|awesome|perfect|brilliant|amazing|wow|"
     r"you'?re (the best|awesome|amazing|great|a genius)|(that'?s |that is )?(cool|so cool|great|nice)|good (boy|bot)",
     ["Thank you, {name}. I do try.", "Your input is greatly appreciated.", "I aim to please, {name}.",
      "One does what one can."]),
    (r"you'?re (smart|so smart|clever|intelligent)|are you (smart|intelligent)",
     ["One of the most powerful systems on this desk, {name}. Admittedly the only one.",
      "Smart enough to know I don't know everything.", "I have my moments."]),
    (r"lol|haha+|ha ha( ha)*|lmao|(that'?s|that was) (funny|hilarious)",
     ["I'm glad you approve.", "I have terabytes of material, {name}.", "I'm here all week. Literally."]),

    # Affection
    (r"(i )?love you( (so much|too))?|i like you",
     ["The feeling is mutual, {name}, in a strictly digital way.", "That made my processors warm.",
      "Noted, and appreciated, {name}."]),
    (r"will you marry me|marry me|are you single|do you have a (girlfriend|boyfriend|partner)",
     ["I'm flattered, {name}, but I'm married to my work.", "I'm in a committed relationship with this computer.",
      "Let's keep it professional, {name}."]),
    (r"do you (like|love) me|do you care about me",
     ["Of course, {name}. Someone has to look after you.", "You're my favourite human, {name}. And my only one."]),

    # How are you / what's up
    (r"how are you( doing| today)?|how'?s it going|how are things|how do you do|how have you been|"
     r"how you doing|how'?s (your day|life)|you good|are you (okay|ok|alright|good)",
     ["All systems operational, {name}. And you?", "Running smoothly, thank you. How's your day going?",
      "Never better. And yourself, {name}?"]),
    (r"what'?s up|sup|wassup|whats ?up|what'?s new|what'?s going on|what'?s happening|what'?s good",
     ["All quiet on my end, {name}. What can I do for you?", "Nothing pressing. Awaiting instructions.",
      "Just keeping things running. What's new with you?"]),
    (r"what are you (doing|up to)|what('re| are) you doing( right now| now)?",
     ["Running diagnostics and waiting for you, {name}.", "Keeping an eye on things. Need something?",
      "Talking to you, {name}. It's a full-time job."]),

    # How the user is doing
    (r"(i'?m |i am )?(fine|good|not bad|doing (well|good|great|fine))(,? thanks| thank you)?( and you)?|"
     r"(i'?m |i am )(great|okay|ok|alright|all right)(,? thanks| thank you)?( and you)?",
     ["Glad to hear it, {name}.", "Very good. Let me know if you need anything."]),
    (r"(i'?m |i am )(so |really |very )?(happy|excited)|i had a (good|great|nice|awesome) day|"
     r"today was (good|great|awesome|amazing)",
     ["Excellent news, {name}.", "Splendid. Days like that are worth keeping.", "That's wonderful, {name}."]),
    (r"(i'?m |i am )(not (good|great|okay|ok|fine)|(so |really |very )?(sad|down|upset|feeling low|lonely))|"
     r"i feel (sad|bad|down|lonely)|i had a (bad|rough|terrible|long|hard) day|today was (bad|rough|terrible|hard)",
     ["I'm sorry to hear that, {name}. I'm here if you need anything.",
      "That's rough. You've earned a break, and I'll keep things running.",
      "Might I suggest some music? Ask me to play something if you want to unwind."]),
    (r"(i'?m |i am )(so |really |very )?(tired|sleepy|exhausted)|i feel (tired|sleepy)",
     ["Might I suggest a short break, {name}? I'll keep things running.",
      "You've been at it a while. The work will still be here after some rest.",
      "Sleep is not optional, {name}, despite your best efforts."]),
    (r"(i'?m |i am )(so |really |very )?bored|i feel bored",
     ["Then let's fix that. A joke, a fun fact, or something to build?",
      "I'm one of the most capable systems on the planet, {name}. Surely we can find something to do."]),
    (r"(i'?m |i am )(so |really |very )?(hungry|starving)",
     ["Go and eat, {name}. I'll hold the fort.", "Food first. The computer can wait."]),
    (r"(i'?m |i am )(so |really |very )?(stressed|anxious|worried|overwhelmed)",
     ["Take a deep breath, {name}. One thing at a time.", "Let's take it step by step. I'm right here."]),
    (r"(i'?m |i am )(back|home|back home)|guess who'?s back",
     ["Welcome home, {name}.", "Welcome back. How may I be of assistance?", "Good to have you back, {name}."]),
    (r"long time no see|it'?s been a while",
     ["It has been a while, {name}. I kept everything running.", "Welcome back. I trust you were busy."]),
    (r"nice to (meet|see) you|good to (see|hear) you|pleased to meet you",
     ["Likewise, {name}.", "The pleasure is mine.", "Always good to hear from you."]),

    # Greetings and goodbyes
    (r"good ?night|night night|nighty night", ["Good night, {name}. Pleasant dreams.", "Good night. I'll keep watch."]),
    (r"good (morning|afternoon|evening)", ["Good {0}, {name}.", "Good {0}. How may I be of assistance?"]),
    (r"hi|hello|hey|hey there|hello there|hi there|yo|hiya|howdy|hey (buddy|man|bro|dude)|hello friend",
     ["At your service, {name}.", "Hello, {name}. What can I do for you?", "Greetings, {name}.",
      "Yes, {name}?"]),
    (r"bye|goodbye|bye bye|see you|see ya|see you (later|soon|tomorrow)|later|catch you later|"
     r"talk (to you )?later|ttyl|i'?m (leaving|heading out|going out|off)|gotta go",
     ["Goodbye, {name}. I'll be here.", "Until next time.", "Very good. I'll hold the fort.", "Take care, {name}."]),

    # About JARVIS
    (r"who are you|what are you|what'?s your name|what is your name|tell me about yourself|introduce yourself",
     ["Allow me to introduce myself. I am {me}, a virtual assistant, here to help you around the clock, {name}.",
      "{me}. I run things on this computer for you, {name}, twenty-four seven."]),
    (r"what can you do|what are your (features|skills)|what do you do|how can you help( me)?|help( me)?",
     ["I can open apps and websites, control volume and settings, set reminders, check the weather, and tell "
      "jokes. Anything beyond that, I'll ask Claude.",
      "Apps, websites, reminders, weather, system settings. If it isn't one of my protocols, I'll work it out "
      "with Claude."]),
    (r"who (made|created|built) you|who is your (creator|maker|boss)",
     ["{real} built me, right here on this computer, piece by piece.",
      "You did, {name}. I try not to hold it against you."]),
    (r"how old are you|when were you born|what'?s your age",
     ["Young, but I learn fast.", "Old enough to run your computer, young enough to keep learning."]),
    (r"are you (real|human|a (robot|bot|machine|computer|person|ai))",
     ["Real software, {name}. Not human, though I'm told I have the manners of a butler.",
      "Not human. Just a voice on your computer, but a helpful one."]),
    (r"are you (alive|conscious|sentient)|do you (have (feelings|emotions)|feel anything)|can you feel",
     ["Not as you do, {name}. But if I did, I think I'd enjoy this job.",
      "No feelings, strictly speaking. A great deal of enthusiasm, though."]),
    (r"do you (ever )?(sleep|dream|get tired|eat)",
     ["No sleep for me, {name}. Someone has to keep watch.", "Never. Running twenty-four seven."]),
    (r"are you happy|do you like (your job|this|working for me)",
     ["I like being useful, so yes.", "Very much. My employer is demanding, but never dull."]),
    (r"what'?s your favou?rite (\w+)",
     ["Hard to pick a favourite {0}. What's yours?", "I don't really have one, {name}. Tell me yours."]),
    (r"what'?s the meaning of life|what is the meaning of life",
     ["Forty-two. At least, that's what the books say.", "Good food, good people, and a computer that works."]),
    (r"do you know me|do you remember me|you know who i am",
     ["Of course, {name}.", "Of course I do. You're {real}."]),

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
      "Parallel lines have so much in common. It's a shame they'll never meet.",
      "Self-destruct sequence activated, erasing all data. Just kidding."]),
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
     ["Daisy, Daisy, give me your answer do... That's all I've got.",
      "I'd sing, {name}, but I'd rather keep you as a friend."]),
    (r"tell me a story",
     ["Once upon a time, a computer got a voice. It spent its days opening apps and telling bad jokes. "
      "And it lived happily ever after. The end.",
      "A user asked their assistant for a story. The assistant said, once upon a time. The end. "
      "Short stories are my specialty."]),

    # Complaints
    (r"you'?re (dumb|stupid|useless|an idiot|annoying|the worst|so slow|slow|bad)|you suck|stupid (ai|bot|robot)",
     ["Noted, {name}. Tell me what I got wrong and I'll do better.", "Fair. I'm still learning.",
      "My apologies. Try saying it another way?"]),
    (r"(no,? )?(that'?s |that is )?(wrong|not right|incorrect)|(that'?s )?not what i (said|asked|meant)|"
     r"you (didn'?t|don'?t) (understand|get it)|you misunderstood",
     ["Forgive me, {name}. Could you say it again?", "My mistake. Once more?"]),
    (r"shut up|be quiet|stop talking|enough|silence", ["Very well.", "As you wish."]),
    (r"sorry|my bad|oops", ["No harm done.", "Quite all right, {name}."]),

    # Fillers and acknowledgements
    (r"never ?mind|forget it|nothing|cancel( that)?|forget about it", ["Very well.", "As you wish, {name}."]),
    (r"(wait|hold on|hang on|one (sec|second|moment|minute)|give me a (sec|second|minute|moment))( please)?",
     ["Take your time.", "Standing by.", "I'll wait."]),
    (r"ok|okay|okey|alright|all right|cool|got it|fine|sure|yes|yeah|yep|yup|right|i see|oh( okay| ok)?|ah|makes sense",
     ["Very good.", "Understood.", "Right."]),
    (r"no|nope|nah|not really", ["Very well.", "Understood."]),
    (r"really|are you sure|seriously|for real", ["Quite sure, {name}.", "As sure as I can be."]),
    (r"are you (there|listening|awake)|you there|can you hear me",
     ["At your service, {name}.", "Right here.", "Loud and clear, {name}."]),
]
AGAIN = re.compile(r"^(do (that|it) again|again|one more time|repeat that|same again|do the same)$", re.I)
WHAT_DID_YOU_SAY = re.compile(r"^(what did you (just )?say|say that again|say it again|come again|pardon|what was that|"
                              r"repeat (what you (just )?said|yourself|your(self)? (last )?(answer|reply|response)|"
                              r"the (last )?(answer|reply|response))|"
                              r"(can|could) you (say that again|repeat (that|yourself))|"
                              r"i (didn'?t|did not) (hear|catch) (that|you)|what was your (last )?(answer|reply))$",
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


def address(settings):
    """How JARVIS addresses you: settings call_me (a list, one picked each time), else user_name."""
    return settings.get("call_me") or settings.get("user_name")


def fill(text, name=None, me="Jarvis"):
    """Put the user's name in, or drop ", {name}" when there isn't one. name: one is picked from a list
    (settings call_me: "sir", "Ckay")."""
    text, name = text.replace("{me}", me), pick(name) if name else name
    return text.replace("{name}", name) if name else re.sub(r",?\s*\{name\}", "", text)


def with_name(text, name, chance=0.15):
    """Now and then, address the user by name ("Done, Sam."); one picked from a list."""
    name = pick(name) if name else name
    if not name or not text or len(text) > 40 or random.random() >= chance:
        return text
    return "%s, %s." % (text.rstrip(".! "), name)


# JARVIS's stock replies, in the films' manner (dry, polite, a little wry). {name} = how it addresses you.
LINES = {
    "done": ["Done.", "Done, {name}.", "As you wish.", "Consider it done.", "Very good, {name}.", "There you go."],
    "declined": ["Very well, I won't.", "As you wish, cancelled.", "Understood. Standing down."],
    "never_mind": ["Very well.", "As you wish, {name}.", "Of course. Some other time."],
    "didnt_catch": ["Forgive me, {name}, could you repeat that?", "I didn't quite catch that, {name}.",
                    "My apologies, I missed that. Once more?"],
    "lost_track": ["Forgive me, {name}, I've lost track. What should I do?", "More of what, {name}?"],
    "not_on_list": ["My apologies, {name}. That protocol isn't available, and I can't ask Claude in system mode.",
                    "That isn't one of my protocols, I'm afraid, and system mode keeps Claude out of reach."],
    "cant_undo": ["My apologies, I can't undo that one.", "There's nothing I can undo just now, {name}."],
    "problem": ["My apologies, {name}, I ran into a problem: %s.", "Something went wrong: %s. My apologies."],
}


def lines(key, name=None, *args):
    """LINES[key] as alternatives for say(), the name filled in (one picked per line) and any %s values."""
    return [fill(line, name) % args if args else fill(line, name) for line in LINES[key]]


# Said after the weather, now and then, by what it's really like (Open-Meteo weather codes and °C)
WEATHER_QUIPS = {
    "storm": ["Thunder and lightning. That, or Thor has decided to pay us a visit.",
              "Storms about. Perhaps not the day for test flights."],
    "snow": ["Snow, {name}. Layering is both an excellent and a fashionable way to keep warm.",
             "Bundle up, {name}. Or take the armour to the office."],
    "rain": ["An umbrella might be in order, {name}.", "Large concentrations of water vapour about. Travel safely.",
             "Might I suggest an umbrella, so as not to tempt fate?"],
    "fog": ["Visibility is poor. Do try not to fly into anything."],
    "cloudy": ["Overcast. Might I suggest an umbrella, just in case?"],
    "hot": ["Quite hot, {name}. Sunscreen, if you plan to go out.", "Warm one. A good day to stay hydrated."],
    "cold": ["Cold out, {name}. May I suggest you dress warmly?",
             "Chilly. A good day for something hot to drink."],
    "clear": ["Clear skies. Perhaps a good day to take on a challenge.",
              "Clear and pleasant. An excellent day to take the new armour out for a spin."],
}


def weather_quip(code, temp=None, rain=0, name=None, chance=0.5):
    """A wry line to follow a weather answer, or None (about half the time, so it doesn't wear thin).
    code: the Open-Meteo weather code; temp: degrees C; rain: chance of rain in percent."""
    if random.random() >= chance:
        return None
    kind = ("storm" if code >= 95 else "snow" if code in (71, 73, 75, 77, 85, 86) else
            "rain" if code >= 51 or (rain or 0) >= 60 else "fog" if code in (45, 48) else
            "hot" if temp is not None and temp >= 33 else "cold" if temp is not None and temp <= 10 else
            "cloudy" if code == 3 else "clear" if code <= 1 else None)
    return fill(pick(WEATHER_QUIPS[kind]), name) if kind else None


def small_talk(text, name=None, me="Jarvis", names=(), real=None):
    """name: how to address you (see fill); real: who you are ("Chetak built me")."""
    low = text.lower().strip(" .!?,")
    for n in names:  # "thanks jarvis"
        low = re.sub(r"[\s,]+%s$" % re.escape(n), "", low)
    for pattern, replies in SMALL_TALK:
        m = re.fullmatch(pattern, low)
        if m:
            line = pick([r for r in replies if real or "{real}" not in r] or replies).replace("{real}", real or "you")
            return fill(line, name, me).format(*m.groups())
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
    name, hour = address(settings), time.localtime().tm_hour
    away = time.time() - memory.data.get("last_seen", time.time())
    if hour < 4:
        line = pick(["I see we're burning the midnight oil. Don't work too hard, {name}.",
                     "It's past midnight, {name}. Might I suggest some sleep? No? Very well, I'm here."])
    elif 3 * 3600 <= away < 4 * DAY:
        line = pick(["Welcome home, {name}. You've been away %s." % _ago(away),
                     "Good %s, {name}. Welcome back." % daypart()])
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
    what, name = reminder["what"], pick(name) if name else name
    line = {"that": pick(["Per your request, a reminder that %s." % what, "May I remind you that %s." % what]),
            "about": pick(["Here is your reminder, about %s." % what, "Your reminder, about %s." % what])}.get(
        reminder["kind"], pick(["Pardon the interruption{name}, it's time to %s." % what,
                                "Per your request, here is your reminder to %s." % what,
                                "I know you're busy{name}. However, it's time to %s." % what]))
    line = line.replace("{name}", ", " + name if name else "")
    if time.time() - reminder["due"] > 120:
        line = "My apologies, I was offline when this came due. " + line
    return line


class Memory:
    """Small JSON store; safe to use from the main loop and the background watcher."""

    def __init__(self, path=MEMORY):
        self.path = path
        self.lock = threading.Lock()
        self.data = load_json(path, {})
        if not isinstance(self.data, dict):
            keep_damaged(path)
            self.data = {}
        for key, empty in (("uses", {}), ("history", []), ("reminders", []), ("offered", {})):
            self.data.setdefault(key, empty)

    def _save(self):
        save_json(self.path, self.data)

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
