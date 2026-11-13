"""What you can say about the card on screen, plus cards that keep themselves up to date.

"the second one" / "open the third link" (choices and links), "read it to me", "copy that", "save that",
"show the previous card" / "next card", "pin it" / "unpin", "close all the cards", live cards (a command re-run
every few seconds while the card is open) and timers ("set a timer for 10 minutes for pasta", "cancel the timer").
Several cards can be up at once; "the second one", "read it", "copy that" are about the newest.
"""
import itertools
import os
import re
import subprocess
import threading
import time

from skills.base import Skill
from skills.helpers import NTH, ORDINALS
from system.browser import open_command
from ui.card import BUTTONS, choices, links, plain, whole
from claude.bridge import WORKSPACE
from core.safety import AUTO

CARDS_DIR = os.path.join(WORKSPACE, "cards")  # "save that" puts cards here
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
                "forty-five": 45, "sixty": 60, "ninety": 90, "half an": 0.5, "a couple of": 2, "a few": 3}
SOUNDS_LIKE = {"to": 2, "too": 2, "for": 4}  # Whisper's "number to": only after "number" / "option"
LEVEL_SECONDS = 3  # the volume / brightness ring stays this long after the last change
UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600}
_AMOUNT = r"(\d+(?:\.\d+)?|half an|a couple of|a few|[a-z]+(?:-[a-z]+)?)\s*(seconds?|secs?|minutes?|mins?|hours?)"


def _words(text):
    """Only the letters and digits: "Add to my to-dos." is "addtomytodos", as Whisper's "add to my todos" is."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


class Cards(Skill):
    group = "cards"
    does = "Acts on the card on screen: picks a choice, opens a link, reads, copies, saves, pins; timers"
    phrases = ("the second one (a choice on the card)", "open the first link", "read it to me", "copy that", "save that",
               "show the previous card", "pin it", "unpin", "close all the cards", "set a timer for 10 minutes for <x>",
               "cancel the timer")
    gives = "the chosen action, speech, or a card"
    permission = "auto"
    checks = ("a timer card counts down and says when it's up; live cards only repeat commands the safety rules run "
              "without asking")

    def __init__(self, jarvis):
        self.timers = {}  # id -> threading.Event that cancels it
        self._ids = itertools.count(1)
        super().__init__(jarvis)

    def patterns(self):
        return [
            # a button by what it says ("add to my to-dos", "open it"), on any card on screen
            (r".+", self.button),
            (r"(?:(?:can|could|would) you |please )?open (?:it|that|this)(?: one)?(?: up)?(?: for me)?(?: please)?",
             self.open_it),
            # "what's the second one", "read the third email": a row of a list card (its "items"), not a button;
            # a card without rows hands it on to pick below
            (r"(?:(?:and |so |ok(?:ay)?,? )?(?:read|open|show(?: me)?|what(?:'s| is| was| about| does)(?: in)?|"
             r"tell me (?:about|what)|and) )(?:me )?(?:the |that )?%s(?: one| email| mail| message| file| doc| "
             r"item| chat)?(?: say| about| is| one is)?" % NTH, self.item),
            (r"(?:(?:i(?:'ll| will)? )?(?:want|pick|choose|select|go with|do|take|open|play|run|use|try)|let's go with|"
             r"give me|show(?: me)?)?\s*(?:the |option |number |no\.? )*(?:%s|(?:number|option) (to|too|for))"
             r"(?: one| option| item| thing)?(?:,? (?:please|then|for me))?" % NTH, self.pick),
            (r"open (?:the )?%s link|open link (?:number )?%s" % (NTH, NTH), self.open_link),
            (r"read (?:it|that|this|the card)(?: out)?(?: to me)?|read (?:it|that) out(?: to me)?|"
             r"what does (?:it|the card) say", self.read),
            (r"copy (?:that|it|this|the card)(?: to (?:the |my )?clipboard)?", self.copy),
            (r"save (?:that|it|this|the card)(?: card)?(?: for later| as a file| as a note)?", self.save),
            (r"(?:show |go back to )?(?:me )?(?:the )?(?:previous|last|earlier) card|back a card",
             lambda m: self.step(-1)),
            (r"(?:show )?(?:me )?(?:the )?next card", lambda m: self.step(1)),
            (r"pin (?:it|that|this|the card)", self.pin),
            (r"unpin(?: it| that| everything| the cards?| all)?|close (?:the |all )?pinned cards?", self.unpin),
            (r"(?:close|hide|clear|dismiss|get rid of) (?:all|every)(?: of)?(?: the| my| these| those)? cards?|"
             r"(?:close|hide|clear|dismiss|get rid of) (?:the|my|these|those) cards|"
             r"(?:close|hide|clear|dismiss) (?:them|these|those) all|(?:close|hide|clear) all (?:of )?(?:them|these)",
             self.close_all),
            (r"(?:close|hide|clear|dismiss|remove|get rid of) (?:the |my |this |that )?(?P<name>[\w' -]*?) ?card",
             self.close_named),
            # "our"/"my": Whisper often hears "set a timer" as "set our timer".
            (r"(?:set(?: up)? |start )?(?:a |an |the |my |our |one )?(?:timer|countdown) (?:for )?%s(?: for (.+))?"
             % _AMOUNT, self.timer),
            (r"%s timer(?: for (.+))?" % _AMOUNT.replace(r"\s*(seconds?", r"[\s-]*(seconds?"), self.timer),
            (r"(?:cancel|stop|clear) (?:the |my |all )?(?:timers?|countdowns?)", self.cancel_timers),
        ]

    # --- the card on screen ---

    def _card(self):
        ui = self.j.ui
        return ui.current_card() if ui and ui.card_visible() else None

    def _press(self, card, label, phrase):
        """Run a button of `card` (any card on screen), with that card as the one its phrase is about."""
        self.j.ui.use_card(card)
        self.j.chat("sys", "picked: %s" % label)
        return self.j.handle(phrase)

    def _open_cards(self):
        ui = self.j.ui
        return ui.open_cards() if ui and ui.card_visible() else []

    def _button_for(self, text):
        """(card, label, phrase) of a button on a card on screen that says `text`, or None."""
        said = _words(text)
        for card in self._open_cards():
            for label, phrase in choices(card)[:BUTTONS]:
                if _words(label) == said and _words(phrase) != said:  # a label that is its own phrase: said as is
                    return card, label, phrase
        return None

    def button(self, m):
        hit = self._button_for(m.group(0))
        return self._press(*hit) if hit else False

    def open_it(self, m):
        """"Open it" with no button saying so: the card's Open (else Read, Play, Join) button, else its first link."""
        for card in self._open_cards():
            for verb in ("open", "read", "play", "join"):
                button = next(((l, p) for l, p in choices(card)[:BUTTONS] if _words(l).startswith(verb)), None)
                if button:
                    return self._press(card, *button)
            if links(card):
                return self._press(card, "its link", "open the first link")
        return False

    @staticmethod
    def _nth(m, items):
        word = next((g for g in m.groups() if g), "").lower()
        n = ORDINALS.get(word) or SOUNDS_LIKE.get(word) or (int(word) if word.isdigit() else 0)
        if n == -1:
            n = len(items)
        return items[n - 1] if 0 < n <= len(items) else None

    def pick(self, m):
        options = choices(self._card())
        if not options:
            return False  # no choices on screen: "the second one" means something else
        choice = self._nth(m, options)
        if not choice:
            self.say("There are only %d options." % len(options))
            return
        self.j.chat("sys", "picked: %s" % choice[0])
        if any(p.match(choice[1].strip().rstrip("?.!")) for p, fn in self.rules if fn == self.pick):
            # a button whose phrase is itself "the first one" would pick itself forever
            self.say(["Picked %s." % choice[0],
                      "%s it is." % choice[0], "Going with %s." % choice[0]])
            return
        return self.j.handle(choice[1])

    def item(self, m):
        card = self._card()
        rows = (card or {}).get("items") or []
        if not rows:
            options = choices(card)
            asked = re.match(r"(?:what|tell me)\b", m.group(0), re.I)
            choice = self._nth(m, options) if asked and options else None
            if not choice:
                return False  # "open the second one": the second button (pick)
            self.say("The %s one is %s." % (next(g for g in m.groups() if g), choice[0]))  # asked, not pressed
            return
        phrase = self._nth(m, rows)
        if not phrase:
            self.say("There are only %d on the card." % len(rows))
            return
        self.j.chat("sys", "picked: row %s" % next(g for g in m.groups() if g))
        return self.j.handle(phrase)

    def open_link(self, m):
        urls = links(self._card())
        if not urls:
            return False
        url = self._nth(m, urls)
        if not url:
            self.say("There are only %d links on the card." % len(urls))
            return
        self.j.executor.run(open_command(url), reply="Opening it.", window=True)

    def read(self, m):
        card = self._card()
        if not card:
            return False
        text = plain(card)
        if not text:
            self.say("There's only a picture on it.")
            return
        if card.get("private"):  # a secret or private notes: read out, but not passed on to Claude
            self.j.keep_private()
        self.j.skills.reading.long(text)  # the start of a long one; "read the rest" / "summarise it"

    def copy(self, m):
        card = self._card()
        if not card:
            return False
        subprocess.run(["xclip", "-selection", "clipboard"], input=whole(card), text=True, timeout=5)
        self.say(["Copied.", "It's on your clipboard.",
                  "Copied. Paste wherever you like.", "Copied. Ready to paste.", "On your clipboard now."])

    def save(self, m):
        card = self._card()
        if not card:
            return False
        os.makedirs(CARDS_DIR, exist_ok=True)
        name = re.sub(r"\W+", "-", (card.get("title") or "card").lower()).strip("-")[:40] or "card"
        path = os.path.join(CARDS_DIR, "%s-%s.md" % (time.strftime("%Y-%m-%d-%H%M"), name))
        with open(path, "w") as f:
            f.write("# %s\n\n%s\n" % (card.get("title") or "Card", whole(card)))
            if card.get("image"):
                f.write("\n![](%s)\n" % card["image"])
        self.j.chat("sys", "saved card: %s" % path)
        self.say(["Saved it in your cards folder.",
                  "Tucked into your cards folder.", "Saved to your cards folder.", "Kept in your cards folder."])

    def step(self, delta):
        if not (self.j.ui and self.j.ui.current_card()):
            return False  # no cards yet: let the phrase go elsewhere
        self.j.ui.card_step(delta)

    def pin(self, m):
        if not self._card():
            return False
        self.j.ui.pin_card()
        self.say(["Pinned. It'll stay put.", "Okay, that one stays.",
                  "Pinned. It's staying right there.", "Pinned. It's not going anywhere."])

    def close_all(self, m):
        if not (self.j.ui and self.j.ui.card_visible()):
            return False  # no cards up: "close them all" is about windows
        self.j.ui.close_card(every=True)

    def close_named(self, m):
        """"Remove the weather card": the open card whose title (or id) has that name; "close this card": the current."""
        name, ui = m.group("name").lower().strip(), self.j.ui
        cards = ui.open_cards() if ui else []
        card = next((c for c in cards if name in (c.get("title") or "").lower() or name == c.get("id")), None) \
            if name else self._card()
        if not card:
            self.say("There's no %s card open." % name if name else "There's no card open.")
            return
        if card.get("id"):
            ui.close_card(card["id"])
        else:
            ui.use_card(card)
            ui.close_card()  # the one just used

    def unpin(self, m):
        if not self.j.ui:
            return False
        self.j.ui.unpin_cards()

    # --- the new level after "volume up", "brightness low": a ring for a few seconds ---

    def level(self, name, command):
        if name.startswith("brightness"):
            m = re.search(r"--brightness\s+([\d.]+)", command)
            if not m:
                return
            title, value, off = "Brightness", round(float(m.group(1)) * 100), False
        else:
            try:
                vol = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], capture_output=True, text=True,
                                     timeout=3).stdout
                mute = subprocess.run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], capture_output=True, text=True,
                                      timeout=3).stdout
            except (OSError, subprocess.SubprocessError):
                return
            m = re.search(r"(\d+)%", vol)
            if not m:
                return
            title, value, off = "Volume", int(m.group(1)), "yes" in mute.lower()
        self._level_shown = token = getattr(self, "_level_shown", 0) + 1
        self.j.ui.show_card({"id": "level", "title": title, "text": "```ring\n%s: %d%%%s\n```" % (
            "Muted" if off else title, min(value, 150), " off" if off else "")})

        def hide():
            if self._level_shown == token and self.j.ui and self.j.ui.card_open("level"):
                self.j.ui.close_card("level")
        threading.Timer(LEVEL_SECONDS, hide).start()

    # --- cards that update themselves ---

    def live(self, card):
        """card["refresh"] = {"command", "every" seconds, "for" seconds}: re-run the command and update the card
        while it's on screen. Only commands the safety rules run without asking are repeated."""
        spec = card.get("refresh") or {}
        command = str(spec.get("command") or "").strip()
        if not self.j.ui:
            return
        if not command or self.j.safety.classify(command)[0] != AUTO:
            if command:
                self.j.chat("sys", "not refreshing (needs your OK each time): %s" % command)
            self.j.ui.show_card(card)  # shown once, as it is
            return
        card.setdefault("id", "live-%d" % next(self._ids))
        every = max(2.0, float(spec.get("every") or 5))
        until = time.time() + min(3600.0, float(spec.get("for") or 600))
        template = card.get("text") or ""

        def loop():
            first = True
            while time.time() < until:
                try:
                    res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=10)
                    out = (res.stdout or res.stderr).strip()[-2000:]
                except subprocess.TimeoutExpired:
                    out = "(timed out)"
                text = template.replace("{output}", out) if "{output}" in template else \
                    (template + "\n```\n%s\n```" % out).strip()
                if not first and not self._showing(card["id"]):
                    return  # closed or replaced: stop
                first = False
                self.j.ui.show_card(dict(card, text=text + "\n\n*updated %s*" % time.strftime("%-I:%M:%S %p")))
                time.sleep(every)

        threading.Thread(target=loop, daemon=True).start()

    def _showing(self, card_id):
        ui = self.j.ui
        return bool(ui and ui.card_open(card_id))

    def timer(self, m):
        amount, unit, what = m.group(1).lower(), m.group(2).lower(), (m.group(3) or "").strip()
        n = float(amount) if re.fullmatch(r"\d+(?:\.\d+)?", amount) else NUMBER_WORDS.get(amount)
        if not n:
            return False
        seconds = n * next(v for k, v in UNITS.items() if unit.startswith(k))
        name = "%s timer" % what if what else "Timer"
        card_id, stop = "timer-%d" % next(self._ids), threading.Event()
        self.timers[card_id] = stop
        end = time.time() + seconds
        which, how_long = name.lower() if what else "timer set", _spoken(seconds)
        self.say(["Okay, %s for %s." % (which, how_long),
                  "%s for %s. I'll shout when it's done." % (which, how_long),
                  "Right, %s for %s. I'll tell you when it's up." % (which, how_long)])

        if self.j.ui:  # the ring counts down by itself (ui/blocks.py ```countdown): shown once, not every second
            self.j.ui.set_timer(card_id, what, end)
            self.j.ui.show_card({"id": card_id, "title": name, "choices": [{"label": "Cancel", "do": "cancel the timer"}],
                                 "text": "```countdown\nend: %.0f\ntotal: %.0f\nlabel: %s\n```" % (
                                     end, seconds, _spoken(seconds))})

        def loop():
            while not stop.is_set() and end - time.time() > 0:
                stop.wait(min(30.0, max(0.05, end - time.time())))
            self.timers.pop(card_id, None)
            if self.j.ui:
                self.j.ui.set_timer(card_id, what)
            if stop.is_set():
                return
            if self.j.ui:
                self.j.ui.show_card({"id": card_id, "title": name, "text": "```countdown\nend: %.0f\ntotal: %.0f\n"
                                     "label: %s is up\n```" % (end, seconds, name)})
            line = "Your %s is done." % name.lower() if what else "Time's up!"
            self.j.skills.proactive.note("timer", line, "You set a timer for %s at %s." % (
                _spoken(seconds), time.strftime("%H:%M", time.localtime(time.time() - seconds))))
            self.j.inbox.put(("say", line))

        threading.Thread(target=loop, daemon=True).start()

    def cancel_timers(self, m):
        if not self.timers:
            self.say("There's no timer running.")
            return
        running = list(self.timers.items())
        for _card_id, stop in running:
            stop.set()
        if self.j.ui:
            for card_id, _stop in running:
                self.j.ui.close_card(card_id)
        self.say(["Timer cancelled.", "Okay, cancelled.",
                  "Timer stopped. No rush after all.", "Stopped the timer.", "Timer's off."])


def _clock(seconds):
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    return ("%d:%02d:%02d" % (h, rest // 60, rest % 60)) if h else "%d:%02d" % divmod(rest, 60)


def _spoken(seconds):
    if seconds % 3600 == 0:
        n, unit = seconds // 3600, "hour"
    elif seconds % 60 == 0:
        n, unit = seconds // 60, "minute"
    else:
        n, unit = seconds, "second"
    return "%g %s%s" % (n, unit, "" if n == 1 else "s")
