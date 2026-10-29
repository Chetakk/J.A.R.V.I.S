""""Copy this into Slack", "paste my clipboard into the terminal", "copy the link into VS Code", "copy the error
from the terminal into Chrome", "summarise this and put it in Gmail": text from one app pasted into another.

The selection is copied with the app's own key, the other window is focused (or the app opened, after a yes)
and the text pasted where its cursor is. Nothing is sent or run: no Enter is pressed, and a terminal is asked
about first when the text has more than one line (each line would run). Afterwards the text is on your
clipboard, and JARVIS says so when nothing changed in that window."""
import re
import time

from claude.bridge import ClaudeError
from core import verify
from skills import helpers
from skills.base import Skill
from skills.browser_tabs import BROWSERS
from skills.helpers import TERMINALS, clipboard, set_clipboard, type_into_window
from skills.screen import front_window
from skills.troubleshoot import _last_error
from system import accessibility, apps, desktop

CLIPBOARD = r"what i(?: just)? copied|(?:my |the )?clipboard|the copied text"
LINK = r"(?:the |this )?(?:page'?s? |tab'?s? )?(?:link|url|web address|address)(?: of this page)?"
ERROR = r"the (?:last )?error(?: message)?"
WINDOW = (r"(?:all |everything )?(?:the )?(?:text )?(?:in |of |on )?(?:this|the) (?:whole )?(?:window|page|screen)|"
          r"everything (?:here|in this window)|all of it")
SELECTION = (r"this|that|it|(?:the )?(?:selection|selected text|highlighted text)|"
             r"what(?: i(?:'ve)?|'s) (?:selected|highlighted)")
SOURCE = r"(?P<src>%s)" % "|".join((CLIPBOARD, LINK, ERROR, WINDOW, SELECTION))
TO = r"(?:in|into|to|onto|over to|across to|over into) (?:the |my |a )?(?P<to>.+?)(?: window| app)?"
FROM = r"(?: (?:from|in|on) (?:the |my )?(?P<from>.+?))?"
NOT_APPS = re.compile(r"^(?:workspace\b.*|(?:the )?(?:left|right|top|bottom)(?: side| half)?|clipboard|"
                      r"(?:my |the )?(?:notes?|todos?|to-?do list|list|trash|bin))$")
PASTED_PIXELS = 15  # changed pixels that mean the paste showed (a blinking cursor is ~8, one letter ~20)
MARK = "jarvis-copy-mark-%d"  # what the clipboard holds until the copy key replaces it: nothing selected if it stays


class CopyBetween(Skill):
    def patterns(self):
        return [
            (re.compile(r"^(?P<job>(?:summari[sz]e|translate|fix|correct|rewrite|reword|shorten|improve|proofread|"
                        r"simplify|explain|tidy up|clean up)\b.*?) ?\b%s\b(?P<rest>.*?)%s and (?:then )?(?:paste|put|"
                        r"copy|send|type|drop) (?:it|that|the result|them) %s$" % (SOURCE, FROM, TO), re.I),
             self.transform),
            (re.compile(r"^(?P<verb>copy|paste|put|send|bring|take|drop|type) (?:over )?%s%s %s$" % (SOURCE, FROM, TO),
                        re.I), self.move),
        ]

    def move(self, m):
        win, app = target(self.j, m.group("to"))
        if not (win or app):
            return False  # "send this to my phone": no window by that name
        text = self._source(m, paste=m.group("verb").lower() == "paste")
        if text is not None:
            self._paste(text, win, app, m.group("verb").lower() == "send")

    def transform(self, m):
        win, app = target(self.j, m.group("to"))
        if not (win or app):
            return False
        text = self._source(m)
        if text is None:
            return
        self.say(["On it.", "Sure, one moment."])
        try:
            text = self.j.claude.rewrite(("%s this %s" % (m.group("job"), m.group("rest").strip())).strip(), text)
        except ClaudeError as e:
            self.say("Sorry, that didn't work: %s." % e)
            return
        self.j.chat("claude", text)
        self._paste(text, win, app, False)

    def _source(self, m, paste=False):
        """The text `src` means, or None (and why not said)."""
        src = m.group("src").lower()
        if re.fullmatch(CLIPBOARD, src) or (paste and re.fullmatch(SELECTION, src)):  # "paste it" = what's copied
            text = clipboard()
            if not text.strip():
                self.say("Your clipboard is empty. Copy something first.")
                return None
            return text
        if m.group("from"):
            win = _find(m.group("from"))
            if not win:
                self.say("I don't see %s open." % m.group("from"))
                return None
        else:
            win = front_window()
            if not win:
                self.say("I can't see a window to copy from.")
                return None
        if re.fullmatch(LINK, src):
            if not any(b in win.wm_class for b in BROWSERS):
                self.say("Links only come from a browser. Switch to it, or say copy the link from Chrome into ...")
                return None
            text = _copied(win, ("ctrl+l", "ctrl+c", "Escape")).strip()
            if not text:
                self.say("I couldn't copy the page's address.")
            return text or None
        if re.fullmatch(ERROR, src) or re.fullmatch(WINDOW, src):
            text, _ = accessibility.window_text(desktop.pid(win), win.title)
            if len(text.split()) < 8:
                self.say("That app doesn't share its text with me. Select it and say copy this into ... instead.")
                return None
            if re.fullmatch(WINDOW, src):
                return text
            error = _last_error(text)
            if not error:
                self.say("I can't see an error in that window.")
            return error or None
        terminal = any(t in win.wm_class for t in TERMINALS)
        text = _copied(win, ("ctrl+shift+c" if terminal else "ctrl+c",))
        if not text.strip():
            self.say("Nothing's selected there. Select the text first, then ask me again.")
            return None
        return text

    def _paste(self, text, win, app, send):
        if not win:
            if not self.j.ask("%s isn't open. Open it and paste it there?" % app["name"]):
                set_clipboard(text)
                self.say("Okay. It's on your clipboard if you want it.")
                return
            win = self._open(app)
            if not win:
                set_clipboard(text)
                self.say("%s didn't open a window, so I couldn't paste it. It's on your clipboard." % app["name"])
                return
        if any(t in win.wm_class for t in TERMINALS):
            text = text.strip("\r\n")  # a last newline would run it
            lines = text.count("\n") + 1
            if lines > 1 and not self.j.ask("That's %d lines, and a terminal runs each one as it's pasted. Paste it "
                                            "anyway?" % lines):
                set_clipboard(text)
                self.say("Okay, I didn't. It's on your clipboard.")
                return
        desktop.focus(win)
        time.sleep(0.4)
        before = verify.Pixels(win)
        type_into_window(self.say, text)
        set_clipboard(text)  # yours to paste again
        ok, what = before.report(least=PASTED_PIXELS)
        verify.record("paste into %s" % (win.title or "a window"), ok, what)
        if ok:
            self.say("Done, I pasted it. I haven't sent it." if send else ["Done, I pasted it.", "Done, it's in."])
        else:
            self.say("I pasted it, but nothing changed in that window that I can see. Click where it should go and "
                     "press control V; it's on your clipboard.")

    def _open(self, app):
        """Start `app` and return its new window (None if none showed up in 10 seconds)."""
        before = verify.windows_now()
        if not self.j.apps.launch(app):
            return None
        deadline = time.time() + 10
        while time.time() < deadline:
            time.sleep(0.4)
            new = [w for w in desktop.windows() if w.id not in before and "jarvis" not in w.wm_class]
            if new:
                time.sleep(1.0)  # let it put the cursor in its text box
                return new[-1]
        return None


def target(jarvis, words):
    """(open window, None) or (None, installed app) for the app `words` names; (None, None) if it's neither.
    Read-only: the dry run uses it too."""
    words = re.sub(r"^(?:the|my|a) ", "", words.strip().lower())
    if NOT_APPS.match(words):
        return None, None
    win = _find(words)
    if win:
        return win, None
    app, _ = jarvis.app_for(apps.OPEN, "open " + words)
    return None, app


def _find(words):
    """The front-most window `words` names ("slack", "vs code", "the terminal", "gmail"), the one on this
    workspace first; never JARVIS's own."""
    words = re.sub(r"^(?:the|my) ", "", words.lower().strip()).replace("vs code", "code").replace("vscode", "code")
    found = [w for w in reversed(desktop.windows()) if "jarvis" not in w.wm_class
             and (words in w.wm_class or words in w.title.lower())]
    here = desktop.current_workspace()
    return next((w for w in found if w.workspace in (here, None)), found[0] if found else None)


def _copied(win, combos):
    """What pressing `combos` in `win` put on the clipboard ("" if nothing did: then yours is put back)."""
    old, mark = clipboard(), MARK % time.time()
    helpers.typed_until = time.time() + 2  # the mark isn't one of your copies
    set_clipboard(mark)
    active = desktop.active_window()
    if not active or active.id != win.id:
        desktop.focus(win)
        time.sleep(0.3)
    for combo in combos:
        desktop.keys(combo)
        time.sleep(0.1)
    for _ in range(10):
        time.sleep(0.1)
        text = clipboard()
        if text != mark:
            return text
    set_clipboard(old)
    return ""
