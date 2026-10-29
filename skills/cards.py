"""What you can say about the card on screen, plus cards that keep themselves up to date.

"the second one" / "open the third link" (choices and links), "read it to me", "copy that", "save that",
"show the previous card" / "next card", "pin it" / "unpin", live cards (a command re-run every few
seconds while the card is open) and timers ("set a timer for 10 minutes for pasta", "cancel the timer").
"""
import itertools
import os
import re
import subprocess
import threading
import time

from skills.base import Skill
from ui.card import choices, links, meter, plain
from claude.bridge import WORKSPACE
from core.safety import AUTO

CARDS_DIR = os.path.join(WORKSPACE, "cards")  # "save that" puts cards here
ORDINALS = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4, "fifth": 5, "5th": 5, "five": 5, "sixth": 6, "6th": 6, "six": 6,
            "last": -1}
_NTH = r"(%s|\d)" % "|".join(ORDINALS)
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
                "forty-five": 45, "sixty": 60, "ninety": 90, "half an": 0.5, "a couple of": 2, "a few": 3}
UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600}
_AMOUNT = r"(\d+(?:\.\d+)?|half an|a couple of|a few|[a-z]+(?:-[a-z]+)?)\s*(seconds?|secs?|minutes?|mins?|hours?)"


class Cards(Skill):
    def __init__(self, jarvis):
        self.timers = {}  # id -> threading.Event that cancels it
        self._ids = itertools.count(1)
        super().__init__(jarvis)

    def patterns(self):
        return [
            (r"(?:pick|choose|select|go with|do|take)?\s*(?:the |option |number )*%s(?: one| option)?" % _NTH,
             self.pick),
            (r"open (?:the )?%s link|open link (?:number )?%s" % (_NTH, _NTH), self.open_link),
            (r"read (?:it|that|this|the card)(?: out)?(?: to me)?|read (?:it|that) out(?: to me)?|"
             r"what does (?:it|the card) say", self.read),
            (r"copy (?:that|it|this|the card)(?: to (?:the |my )?clipboard)?", self.copy),
            (r"save (?:that|it|this|the card)(?: card)?(?: for later| as a file| as a note)?", self.save),
            (r"(?:show |go back to )?(?:me )?(?:the )?(?:previous|last|earlier) card|back a card",
             lambda m: self.step(-1)),
            (r"(?:show )?(?:me )?(?:the )?next card", lambda m: self.step(1)),
            (r"pin (?:it|that|this|the card)", self.pin),
            (r"unpin(?: it| that| everything| the cards?| all)?|close (?:the |all )?pinned cards?", self.unpin),
            (r"(?:set |start )?(?:a |an )?(?:timer|countdown) (?:for )?%s(?: for (.+))?" % _AMOUNT, self.timer),
            (r"%s timer(?: for (.+))?" % _AMOUNT.replace(r"\s*(seconds?", r"[\s-]*(seconds?"), self.timer),
            (r"(?:cancel|stop|clear) (?:the |my |all )?(?:timers?|countdowns?)", self.cancel_timers),
        ]

    # --- the card on screen ---

    def _card(self):
        ui = self.j.ui
        return ui.current_card() if ui and ui.card_visible() else None

    @staticmethod
    def _nth(m, items):
        word = next((g for g in m.groups() if g), "").lower()
        n = ORDINALS.get(word) or (int(word) if word.isdigit() else 0)
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
        return self.j.handle(choice[1])

    def open_link(self, m):
        urls = links(self._card())
        if not urls:
            return False
        url = self._nth(m, urls)
        if not url:
            self.say("There are only %d links on the card." % len(urls))
            return
        self.j.executor.run("xdg-open %s" % url, reply="Opening it.", window=True)

    def read(self, m):
        card = self._card()
        if not card:
            return False
        text = plain(card)
        if not text:
            self.say("There's only a picture on it.")
            return
        self.j.skills.reading.long(text)  # the start of a long one; "read the rest" / "summarise it"

    def copy(self, m):
        card = self._card()
        if not card:
            return False
        subprocess.run(["xclip", "-selection", "clipboard"], input=card.get("text") or "", text=True, timeout=5)
        self.say(["Copied.", "It's on your clipboard."])

    def save(self, m):
        card = self._card()
        if not card:
            return False
        os.makedirs(CARDS_DIR, exist_ok=True)
        name = re.sub(r"\W+", "-", (card.get("title") or "card").lower()).strip("-")[:40] or "card"
        path = os.path.join(CARDS_DIR, "%s-%s.md" % (time.strftime("%Y-%m-%d-%H%M"), name))
        with open(path, "w") as f:
            f.write("# %s\n\n%s\n" % (card.get("title") or "Card", card.get("text") or ""))
            if card.get("image"):
                f.write("\n![](%s)\n" % card["image"])
        self.j.chat("sys", "saved card: %s" % path)
        self.say("Saved it in your cards folder.")

    def step(self, delta):
        if not (self.j.ui and self.j.ui.current_card()):
            return False  # no cards yet: let the phrase go elsewhere
        self.j.ui.card_step(delta)

    def pin(self, m):
        if not self._card():
            return False
        self.j.ui.pin_card()
        self.say(["Pinned. It'll stay put.", "Okay, that one stays."])

    def unpin(self, m):
        if not self.j.ui:
            return False
        self.j.ui.unpin_cards()

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
        return bool(ui and ui.card_visible() and (ui.current_card() or {}).get("id") == card_id)

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
        self.say("Okay, %s for %s." % (name.lower() if what else "timer set", _spoken(seconds)))

        def loop():
            while not stop.is_set():
                left = end - time.time()
                if left <= 0:
                    break
                if self.j.ui and (self._showing(card_id) or not self.j.ui.card_visible()):
                    self.j.ui.show_card({"id": card_id, "title": name, "choices": [
                        {"label": "Cancel", "do": "cancel the timer"}],
                        "text": "# %s\n%s" % (_clock(left), meter(int(100 * left / seconds)))})
                stop.wait(1 if left < 600 else 5)
            self.timers.pop(card_id, None)
            if stop.is_set():
                return
            if self.j.ui:
                self.j.ui.show_card({"id": card_id, "title": name, "text": "# Done ✓\n%s is up." % name})
            line = "Your %s is done." % name.lower() if what else "Time's up!"
            self.j.skills.proactive.note("timer", line, "You set a timer for %s at %s." % (
                _spoken(seconds), time.strftime("%H:%M", time.localtime(time.time() - seconds))))
            self.j.inbox.put(("say", line))

        threading.Thread(target=loop, daemon=True).start()

    def cancel_timers(self, m):
        if not self.timers:
            self.say("There's no timer running.")
            return
        for stop in list(self.timers.values()):
            stop.set()
        if self.j.ui and str((self.j.ui.current_card() or {}).get("id", "")).startswith("timer-"):
            self.j.ui.close_card()
        self.say(["Timer cancelled.", "Okay, cancelled."])


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
