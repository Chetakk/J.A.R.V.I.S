"""Your Android phone, without Claude: "open spotify on my phone", "call Mom", "text Rahul saying I'm late",
"whatsapp Mom saying OK", "tap send on my phone", "scroll down on my phone", "phone battery", "pause my phone",
"lock my phone", "show my phone screen", "find my phone".

Two links, both local: adb (USB debugging; plugged in once, it moves itself to Wi-Fi and reconnects after) does the opening, calling, keys, screenshots and
tapping; GSConnect (the KDE Connect phone app, over Wi-Fi) sends texts and rings the phone, and puts the phone's
notifications on the desktop, where "read my notifications" already reads them. "Tap <x>" finds the words on the
phone's screen in its UI tree (uiautomator), so no vision model. Names are looked up in the phone's own contacts.
Texts and WhatsApp messages are sent only after a yes."""
import glob
import os
import shlex
import re
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ET

from claude.bridge import STATE_DIR, WORKSPACE
from skills import documents, files
from skills.base import Skill
from skills.helpers import clipboard
from system import chrome_link, desktop

PORT = 5555
IP_FILE = os.path.join(STATE_DIR, "phone-ip")  # the phone's last Wi-Fi address, to reconnect
PICTURES = os.path.expanduser("~/Pictures")
VAGUE = (r"(?:this|that|these|those|it|them)(?: (?:file|photo|picture|pic|image|video|clip|pdf|document|doc|song|"
         r"one|screenshot))?s?")
SHOT = r"(?:a |my |the )?(?:screen ?shot|screen|screen ?grab)(?: of (?:my|the) screen)?"
A_FILE = r"%s|%s|[^ ]+\.[A-Za-z0-9]{2,5}" % (VAGUE, SHOT)  # a file by pointing or by its name, not just words
ON_PHONE = r"(?: on (?:my|the) (?:phone|mobile))"
COUNTRY = "91"  # shortcut: India's code for 10-digit numbers, make it a setting if a non-Indian number comes up
KEYS = {"pause": 85, "play": 85, "resume": 85, "next": 87, "skip": 87, "previous": 88, "volume up": 24,
        "volume down": 25, "lock": 223}
APPS = {"camera": "android.media.action.STILL_IMAGE_CAMERA"}  # apps whose package name doesn't say what they are
BUS = "org.gnome.Shell.Extensions.GSConnect"
# Your own copy first: Ubuntu 20.04's package (v20) is too old for its GNOME 3.36, v43 from GitHub works.
GSCONNECTS = [os.path.join(d, "gnome-shell/extensions/gsconnect@andyholmes.github.io/service/daemon.js")
              for d in (os.path.expanduser("~/.local/share"), "/usr/share")]


class PhoneError(Exception):
    pass


class Phone(Skill):
    group = "phone"
    does = ("Your Android phone: open apps, call, text, WhatsApp, tap, scroll, type, keys, battery, screenshot, ring, "
            "send it a page, file, text or your clipboard, get its clipboard")
    phrases = ("open <app> on my phone", "call <name>", "text <name> saying <message>",
               "whatsapp <name> saying <message>", "tap <button> on my phone", "scroll down on my phone",
               "phone battery", "pause my phone", "lock my phone", "show my phone screen", "find my phone",
               "send this to my phone", "send <file> to my phone", "send <text> to my phone",
               "copy my clipboard to my phone", "what's on my phone's clipboard",
               "open this on my phone", "send a note to my phone saying <message>",
               "send this to <name> on whatsapp", "share this on my phone", "paste from my phone", "type <text> on my phone",
               "reply saying <message>", "dismiss that notification", "mute the call", "read my texts",
               "any texts from <name>", "open my phone's files", "get my latest photo from my phone")
    gives = "done on the phone, said back; its messages and calls said as they come; texts, photos, screen on a card"
    permission = "confirm"
    asks = "before sending a text, WhatsApp message or reply"
    checks = "adb and GSConnect say whether it went; a tap says what it found on the screen"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self._contacts = (0.0, [])
        self._serial = None
        self.last = None  # the phone's last message or call JARVIS heard of (system/notifications.py item)

    def patterns(self):
        return [
            (r"open ((?:this|that|these|those|it|them)(?: \w+)?|\S+\.[a-z0-9]{2,5})%s" % ON_PHONE,
             self.open_on_phone),
            (r"(?:open|launch|start) (?:the )?(.+?)(?: app)?%s" % ON_PHONE, self.open_app),
            (r"(?:send|share|forward) (%s) to (.+?) on whatsapp|whatsapp (%s) to (.+)" % (A_FILE, A_FILE),
             self.whatsapp_file),
            (r"share (%s)(?: on| from) (?:my|the) (?:phone|mobile)" % A_FILE, self.share_on_phone),
            (r"(?:send )?(?:a )?(?:text|sms)(?: message)?(?: to)? (.+?) (?:saying|that) (.+)", self.text),
            (r"(?:whatsapp|send a whatsapp to|send a whatsapp message to) (.+?) (?:saying|that) (.+)|"
             r"(?:message|tell) (.+?) on whatsapp (?:saying |that )?(.+)", self.whatsapp),
            (r"(?:tap|press|click) (?:on )?(?:the )?(.+?)(?: button)?%s" % ON_PHONE, self.tap),
            (r"scroll (up|down)%s" % ON_PHONE, self.scroll),
            (r"(?:what'?s |how'?s |what is )?(?:my |the )?phone(?:'s)? battery(?: level)?|"
             r"how much (?:battery|charge) (?:is left |does )?(?:on )?my phone(?: have)?", self.battery),
            (r"(pause|play|resume|next|skip|previous)(?: (?:the )?(?:song|music|track))?%s|"
             r"(pause|play|resume|lock) (?:my|the) phone" % ON_PHONE, self.key),
            (r"(?:my )?phone (volume up|volume down)|(volume up|volume down)%s|"
             r"turn (?:my|the) phone (up|down)" % ON_PHONE, self.volume),
            (r"(?:show|screenshot|what'?s on) (?:me )?(?:my|the) phone(?:'s)?(?: screen)?", self.screenshot),
            (r"(?:find|where'?s|where is) my phone|ring my phone", self.ring),
            (r"(?:send|put|push) (?:a )?(?:note|notification|reminder|message to myself) (?:to|on) (?:my|the) "
             r"(?:phone|mobile)(?: saying| that)? (.+)", self.note),
            (r"(?:copy|send|share|push|put|sync) (?:my |the |this )?clipboard (?:to|on|onto|with) (?:my |the )?"
             r"(?:phone|mobile)", self.push_clipboard),
            (r"(?:get|pull|grab|paste|copy|bring|sync) (?:my |the )?(?:phone'?s?|mobile'?s?) clipboard(?: here| to (?:my |"
             r"the |this )?(?:computer|pc|laptop))?|paste (?:from|what i copied on) (?:my |the )?(?:phone|mobile)|"
             r"(?:copy|get|bring|paste) (?:my |the )?clipboard from (?:my |the )?(?:phone|mobile)", self.pull_clipboard),
            (r"(?:what'?s|what is|read(?: me)?) (?:on |in )?(?:my |the )?(?:phone'?s?|mobile'?s?) clipboard",
             self.phone_clipboard),
            (r"(?:send|share|push) (.+?) to (?:my |the )?(?:phone|mobile)", self.send),
            (r"type (.+?)%s" % ON_PHONE, self.type_text),
            (r"reply(?: to (.+?))?(?: on (?:my phone|the phone|whatsapp))? (?:saying|with|that) (.+)", self.reply),
            (r"(?:dismiss|clear|close|swipe away) (?:that|this|the) (?:notification|message)%s?" % ON_PHONE, self.dismiss),
            (r"(?:mute|silence) (?:the |my |that )?(?:call|ringing|phone call)|stop my phone ringing|"
             r"silence my phone", self.mute_call),
            (r"(?:read|check|show)(?: me)? (?:my )?(?:new |latest |last |recent )?(?:texts|text messages|sms|smses)|"
             r"(?:any|read|show)(?: me)? (?:new )?(?:texts|text messages|sms) from (.+?)|"
             r"(?:did i get|do i have|have i got) any (?:new )?(?:texts|text messages|sms)", self.texts),
            (r"(?:open|show|browse)(?: me)? (?:my |the )?phone'?s? (?:files|storage|folders?|photos folder)|"
             r"browse my phone", self.phone_files),
            (r"(?:get|show|send|bring|grab)(?: me)? (?:my |the )?(?:latest|last|newest|most recent) "
             r"(photo|picture|pic|screenshot)(?: i took)?(?: (?:from|on|off) (?:my|the) phone)", self.latest_photo),
            (r"(?:call|ring|phone|dial) (\S+(?: \S+){0,2}?)(?: on (?:my|the) (?:phone|mobile)| from my phone)?", self.call),  # last: "phone battery" isn't a name
        ]

    # --- handlers ---
    def open_app(self, m):
        name = m.group(1).lower()
        if name in APPS:
            self._adb("shell", "am", "start", "-a", APPS[name])
        else:
            pkg = self._package(name)
            if not pkg:
                return self.fail("I can't find %s on your phone." % name)
            self._adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
        self.say("Opening %s on your phone." % name)

    def call(self, m):
        who = m.group(1)
        if re.fullmatch(r"(?:it|that|this|him|her|them|back)", who, re.IGNORECASE):
            return False  # "call it a day", "call him back": not a name
        try:
            name, number = self._number(who)
        except PhoneError:
            if "phone" in m.group(0).lower():
                raise
            return False  # "call <x>" with no phone connected: maybe someone else's phrase
        if not number:
            return False  # not a contact or a number: someone else's phrase
        self._adb("shell", "am", "start", "-a", "android.intent.action.CALL", "-d", "tel:" + number)
        self.say("Calling %s." % name)

    def text(self, m):
        name, number = self._number(m.group(1))
        if not number:
            return self.fail("I can't find %s in your phone's contacts." % m.group(1))
        message = m.group(2)
        if not self.j.ask("Text %s: %s?" % (name, message)):
            return self.say("Okay, not sent.")
        self._act("sendSms", "(%s, %s)" % (_gstr(number), _gstr(message)))
        self.say("Sent to %s." % name)

    def whatsapp(self, m):
        who, message = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        name, number = self._number(who)
        if not number:
            return self.fail("I can't find %s in your phone's contacts." % who)
        if not self.j.ask("WhatsApp %s: %s?" % (name, message)):
            return self.say("Okay, not sent.")
        url = "https://api.whatsapp.com/send?phone=%s&text=%s" % (_international(number), urllib.parse.quote(message))
        self._adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "'%s'" % url, "-p", "com.whatsapp")
        for _ in range(8):  # WhatsApp takes a moment to open the chat
            time.sleep(0.5)
            node = _find(self._screen(), "send")
            if node:
                self._adb("shell", "input", "tap", *map(str, node["at"]))
                return self.say("Sent to %s on WhatsApp." % name)
        self.fail("WhatsApp opened the chat but I couldn't find its Send button. The message is typed in.")

    def tap(self, m):
        node = _find(self._screen(), m.group(1))
        if not node:
            return self.fail("I can't see %s on your phone's screen." % m.group(1))
        self._adb("shell", "input", "tap", *map(str, node["at"]))
        self.say("Tapped %s." % node["label"])

    def scroll(self, m):
        w, h = self._size()
        top, bottom = str(h // 4), str(h * 3 // 4)
        start, end = (bottom, top) if m.group(1).lower() == "down" else (top, bottom)
        self._adb("shell", "input", "swipe", str(w // 2), start, str(w // 2), end, "300")
        self.say("Done.")

    def battery(self, m):
        try:  # GSConnect first: no adb needed
            state = re.search(r"\((?:true|false), '([^']*)', (-?\d+), ", self._state("battery"))
        except PhoneError:
            state = None
        if state and int(state.group(2)) >= 0:
            level, charging = state.group(2), "charging" in state.group(1)
        else:
            info = dict(re.findall(r"^\s*([\w ]+): (.+)$", self._adb("shell", "dumpsys", "battery"), re.MULTILINE))
            level, charging = info.get("level", "?"), info.get("status") == "2"
        self.say("Your phone is at %s percent%s." % (level, " and charging" if charging else ""))

    def key(self, m):
        word = (m.group(1) or m.group(2)).lower()
        self._adb("shell", "input", "keyevent", str(KEYS[word]))
        self.say("Phone locked." if word == "lock" else "Done.")

    def volume(self, m):
        word = next(g for g in m.groups() if g).lower()
        word = word if word.startswith("volume") else "volume " + word
        for _ in range(3):  # one press is a small step
            self._adb("shell", "input", "keyevent", str(KEYS[word]))
        self.say("Done.")

    def screenshot(self, m):
        path = os.path.join(WORKSPACE, "phone.png")
        png = self._adb("exec-out", "screencap", "-p", raw=True)
        with open(path, "wb") as f:
            f.write(png)
        self.j.show({"title": "Your phone", "image": path})
        self.say("Here's your phone's screen.")

    def ring(self, m):
        self._act("ring")
        self.say("Ringing your phone.")

    def send(self, m, opening=False):
        """"Send this to my phone": the page you're on in Chrome, else what you're looking at (the files selected in
        Files, the picture, video or PDF a viewer has open), else the file JARVIS just found or opened, else what you
        copied. "Send plan.pdf to my phone" finds it by name; other words go as text (to the phone's clipboard).
        "Open this on my phone" sends it and opens it there."""
        what = m.group(1)
        low = what.lower()
        vague = bool(re.fullmatch(VAGUE, low))
        front = desktop.active_window()
        if low in ("this link", "this page", "this tab", "that link", "that page") or (
                vague and front and "chrome" in front.wm_class.lower()):
            page = chrome_link.page()
            if page and page.get("url"):
                self._act("shareUri", _gstr(page["url"]))  # the phone opens a link in its browser
                return self.say("Sent the page to your phone.")
            if not vague:
                return self.fail("I can't see the page in Chrome.")
        paths = self._pick(what)
        if not paths and documents.sure(what):
            return self.fail("I can't find %s." % what)
        if paths:
            for path in paths:
                self._act("shareFile", "(%s, %s)" % (_gstr(path), "true" if opening else "false"))
            name = os.path.basename(paths[0]) if len(paths) == 1 else "%d files" % len(paths)
            return self.say("Opening %s on your phone." % name if opening else "Sending %s to your phone." % name)
        if vague or low in ("my clipboard", "the clipboard", "what i copied"):
            if opening:
                return self.fail("I can't tell what to open. Select the file first.")
            self._act("clipboardPush")
            return self.say("Your phone has your clipboard now.")
        if re.match(r"(?:this|that|these|those) ", low):
            return self.fail("I don't know which %s you mean. Open or select it first." % low.split(" ", 1)[1])
        self._act("shareText", _gstr(what))
        self.say("Sent to your phone's clipboard.")

    def _pick(self, what):
        """The file(s) `what` means: "a screenshot" takes one; "it" / "this photo" is a screenshot you just took
        ("take a screenshot and send it ..."), else what you're looking at (the selection in Files, the picture,
        video or PDF a viewer has open), else the file JARVIS just found or opened; "plan.pdf" is found by name."""
        low = what.lower()
        if re.fullmatch(SHOT, low):
            return [self._screenshot()]
        if re.fullmatch(VAGUE, low):
            return _fresh_screenshot() or files.front_files(self.j) or files.recent_file() or []
        if documents.sure(what):
            return [h for h in files._find_files(what) if os.path.basename(h).lower() == low][:1]
        return []

    def share_on_phone(self, m):
        """The file on the phone, in Android's share menu: you pick the app there."""
        paths = self._pick(m.group(1))
        if not paths:
            return self.fail("I can't tell which file you mean. Open or select it first.")
        self._awake()
        uri, mime = self._on_phone(paths[0])
        self._adb("shell", "am", "start", "-a", "android.intent.action.SEND", "-t", mime, "--eu",
                  "android.intent.extra.STREAM", uri, "--grant-read-uri-permission")
        self.say("It's in your phone's share menu. Pick where it goes.")

    def whatsapp_file(self, m):
        """The file to someone on WhatsApp: their chat opens with it attached, and Send is tapped after a yes."""
        what, who = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        paths = self._pick(what)
        if not paths:
            return self.fail("I can't tell which file you mean. Open or select it first.")
        name, number = self._number(who)
        if not number:
            return self.fail("I can't find %s in your phone's contacts." % who)
        if not self.j.ask("Send %s to %s on WhatsApp?" % (os.path.basename(paths[0]), name)):
            return self.say("Okay, not sent.")
        self._awake()
        uri, mime = self._on_phone(paths[0])
        self._adb("shell", "am", "start", "-a", "android.intent.action.SEND", "-t", mime, "--eu",
                  "android.intent.extra.STREAM", uri, "--grant-read-uri-permission", "-p", "com.whatsapp",
                  "--es", "jid", "%s@s.whatsapp.net" % _international(number))
        for _ in range(10):  # WhatsApp shows the picture with its own Send button
            time.sleep(0.5)
            node = _find(self._screen(), "send")
            if node:
                self._adb("shell", "input", "tap", *map(str, node["at"]))
                return self.say("Sent to %s on WhatsApp." % name)
        self.fail("WhatsApp has it ready for %s, but I couldn't find Send. Tap it on your phone." % name)

    def _awake(self):
        """Screen on; PhoneError while it's locked (Android shows nothing over the lock screen)."""
        self._adb("shell", "input", "keyevent", "224")
        if "mDreamingLockscreen=true" in self._adb("shell", "dumpsys", "window"):
            raise PhoneError("it's locked. Unlock it and say that again")

    def _on_phone(self, path):
        """The file on the phone, indexed so apps can take it as an attachment: (its content:// address, its type).
        Sent first unless the same file (name and size) is already there; a new copy may be renamed "x (1).png"."""
        stem, ext = (n.replace("'", "''") for n in os.path.splitext(os.path.basename(path)))
        size = os.path.getsize(path)
        where = "_display_name LIKE '%s%%%s' AND _size=%d" % (stem, ext, size)
        row = self._indexed(where)
        if not row:
            self._act("shareFile", "(%s, false)" % _gstr(path))
            deadline = time.time() + 10 + min(size // 1000000, 120)  # ~1 MB a second over Wi-Fi
            while not row and time.time() < deadline:
                time.sleep(1)
                row = self._indexed(where)
        if not row:
            raise PhoneError("the file didn't arrive in time. Is it very big")
        kind = {"image": "images", "video": "video", "audio": "audio"}.get(row[1].split("/")[0])
        return ("content://media/external/%s/media/%s" % (kind, row[0]) if kind else
                "content://media/external/file/" + row[0]), row[1]

    def _indexed(self, where):
        """(id, type) of the newest file in the phone's media index matching `where`, or None."""
        out = self._adb("shell", "content", "query", "--uri", "content://media/external/file", "--projection",
                        "_id:mime_type", "--where", shlex.quote(where), "--sort", shlex.quote("date_added DESC"))
        row = re.search(r"_id=(\d+), mime_type=(\S+)", out)  # the media index takes no LIMIT: the first row
        return row.groups() if row else None

    def _screenshot(self):
        """A screenshot of the whole screen, saved in Pictures as gnome-screenshot names them."""
        os.makedirs(PICTURES, exist_ok=True)
        path = os.path.join(PICTURES, time.strftime("Screenshot from %Y-%m-%d %H-%M-%S.png"))
        subprocess.run(["gnome-screenshot", "-f", path], timeout=10)
        return path

    def open_on_phone(self, m):
        self.send(m, opening=True)

    def note(self, m):
        """A notification on the phone (needs "Receive notifications" on in KDE Connect on the phone)."""
        self._act("sendNotification", "{'appName': <'JARVIS'>, 'id': <%s>, 'title': <'JARVIS'>, 'body': <%s>, "
                  "'ticker': <%s>, 'isClearable': <true>}" % (_gstr("jarvis-%d" % time.time()), _gstr(m.group(1)),
                                                               _gstr("JARVIS: " + m.group(1))))
        self.say("It's on your phone.")

    def push_clipboard(self, m):
        self._act("clipboardPush")
        self.say("Your phone has your clipboard now.")

    def pull_clipboard(self, m):
        """The phone's clipboard, as GSConnect last heard it: Android 10+ only lets KDE Connect see it when you
        share it from the phone (its "Send clipboard"), so it can be an older copy."""
        self._act("clipboardPull")
        self.say("Your phone's clipboard is on this computer now.")

    def phone_clipboard(self, m):
        self._act("clipboardPull")
        time.sleep(0.5)  # GSConnect sets it a moment later
        text = clipboard().strip()
        self.say("Your phone's clipboard says: %s" % text if 0 < len(text) <= 200 else
                 "It's long. It's on this computer's clipboard now." if text else "Your phone's clipboard is empty.")

    def type_text(self, m):
        text = re.sub(r"([\\\"'`$&|;<>()*?!#~ ])", lambda c: "%s" if c.group(1) == " " else "\\" + c.group(1), m.group(1))
        self._adb("shell", "input", "text", text)
        self.say("Typed it.")

    # --- what the phone says by itself (system/notifications.py on_add) ---
    def arrived(self, item):
        """A new desktop notification: the phone's calls and messages (the ones that can be answered, and texts)
        are said as they come; the rest stay for "read my notifications"."""
        try:
            phone = item.get("phone")
            if not phone:
                return
            if phone.get("call") == "ringing":
                self.last = item
                self.j.skills.proactive.tell("phone", "%s is calling." % (item["title"] or "Someone"),
                                             "Your phone is ringing. Say mute the call to silence it.")
            elif phone.get("reply") or item["app"] == "Messages":
                self.last = item
                who = item["title"] or item["app"]
                line = ("Text from %s: %s" if item["app"] == "Messages" else item["app"] + " from %s: %s") % (
                    who, item["body"])
                self.j.skills.proactive.tell("phone", line, "A message came to your phone. Say reply saying, "
                                                            "and your answer, or dismiss that notification.")
        except Exception as e:  # runs on the notification reader's thread: never let it die
            print("[phone] %r" % e)

    def reply_target(self, name=None, since=0.0):
        """The phone message "reply saying ..." is about: the last one heard (from `name`, if given), when it
        came after `since` (Google Chat's last message, so the newer one wins) and within half an hour."""
        w = self.j.skills.notifications.watcher
        for item in reversed(w.recent(max(since, time.time() - 1800))):
            phone = item.get("phone") or {}
            if (phone.get("reply") or item["app"] == "Messages") and (
                    not name or name.lower() in item["title"].lower()):
                return item
        return None

    def reply(self, m):
        item = self.reply_target(m.group(1))
        if not item:
            if m.group(1):
                return False  # "reply to Rahul saying ..." with no phone message from him: Chat's, or a text
            return self.fail("I don't have a phone message to reply to.")
        message = m.group(2)
        where = "a text" if item["app"] == "Messages" else item["app"]
        if not self.j.ask('Reply to %s on %s: "%s"?' % (item["title"], where, message)):
            return self.say("Okay, not sent.")
        if item["app"] == "Messages":
            name, number = self._number(item["title"])
            if not number:
                return self.fail("I can't find %s's number to text back." % item["title"])
            self._act("sendSms", "(%s, %s)" % (_gstr(number), _gstr(message)))
        else:
            self._act("replyNotification", "(%s, %s, {'appName': %s, 'title': %s, 'text': %s})" % (
                _gstr(item["phone"]["reply"]), _gstr(message), _gstr(item["app"]), _gstr(item["title"]),
                _gstr(item["body"])))
        self.say("Sent to %s." % item["title"])

    def dismiss(self, m):
        w = self.j.skills.notifications.watcher
        item = next((n for n in reversed(w.recent()) if (n.get("phone") or {}).get("key")), None)
        if not item:
            return self.fail("There's no phone notification to clear.")
        self._act("closeNotification", _gstr(item["phone"]["key"]))
        self.say("Cleared %s's notification." % (item["title"] or item["app"]))

    def mute_call(self, m):
        self._act("muteCall")
        self.say("Muted.")

    def texts(self, m):
        who = next((g for g in m.groups() if g), None)
        where = []
        if who:
            name, number = self._number(who)
            if not number:
                return self.fail("I can't find %s in your phone's contacts." % who)
            where = ["--where", shlex.quote("address LIKE '%%%s'" % re.sub(r"\D", "", number)[-10:])]
        out = self._adb("shell", "content", "query", "--uri", "content://sms/inbox", "--projection",
                        "address:date:body", *where, "--sort", shlex.quote("date DESC LIMIT 5"))
        rows = re.findall(r"^Row: \d+ address=(.*?), date=(\d+), body=(.*?)(?=^Row: |\Z)", out, re.M | re.S)
        if not rows:
            return self.say("No texts from %s." % who if who else "No texts.")
        names = {re.sub(r"\D", "", n)[-10:]: name for name, n in self._people()}
        said = ["%s: %s" % (names.get(re.sub(r"\D", "", a)[-10:], a), b.strip()) for a, _, b in rows]
        self.j.show({"title": "Texts" + (" from " + who.title() if who else ""), "text": "\n".join(
            "- **%s**" % line for line in said)})
        self.say("Your latest text, from %s." % said[0] if len(said) == 1 else
                 "%d recent texts. The latest, from %s." % (len(said), said[0]))

    def phone_files(self, m):
        self._act("mount")
        for _ in range(16):  # the sftp mount takes a few seconds
            found = glob.glob("/run/user/%d/gvfs/sftp:host=*/storage/emulated/0" % os.getuid())
            if found:
                return self.j.executor.run("xdg-open %s" % shlex.quote(found[0]), reply="Opening your phone's files.",
                                           window=True)
            time.sleep(0.5)
        self.fail("Your phone's files didn't show up. Is file sharing on in KDE Connect?")

    def latest_photo(self, m):
        folder = "/sdcard/Pictures/Screenshots" if m.group(1).lower() == "screenshot" else "/sdcard/DCIM/Camera"
        name = self._adb("shell", "ls -t %s | head -1" % folder).strip()
        if not name:
            return self.fail("I can't find any in %s on your phone." % folder.rsplit("/", 1)[-1])
        here = os.path.expanduser("~/Pictures/Phone")
        os.makedirs(here, exist_ok=True)
        self._adb("pull", "%s/%s" % (folder, name), os.path.join(here, name))
        self.j.show({"title": name, "image": os.path.join(here, name)})
        self.say("Here it is. It's in Pictures, Phone.")

    # --- the links ---
    def _adb(self, *args, raw=False):
        """adb's output (bytes if raw) from the phone, over Wi-Fi when it can. PhoneError when no phone answers
        (the skill base says it). A link that dropped (phone asleep, Wi-Fi changed) is found again once."""
        for retry in (False, True):
            if retry or not self._serial:
                self._serial = self._connect()
            r = _run(["adb", "-s", self._serial, *args])
            err = r.stderr.decode(errors="replace")
            if not (r.returncode and re.search(r"not found|offline|no devices|closed", err)):
                return r.stdout if raw else r.stdout.decode(errors="replace")
        raise PhoneError("I lost the link to it")

    def _connect(self):
        """The serial to use: the phone over Wi-Fi if it's there, else a USB phone switched to Wi-Fi (adb tcpip,
        which lasts until the phone restarts), else the last Wi-Fi address tried again."""
        attached = re.findall(r"^(\S+)\tdevice$", _run(["adb", "devices"]).stdout.decode(), re.MULTILINE)
        wifi = [s for s in attached if ":" in s]
        if wifi:
            if _saved_ip() != wifi[0].split(":")[0]:
                _save_ip(wifi[0].split(":")[0])
            return wifi[0]
        if attached:  # on the cable: move it to Wi-Fi so it can be unplugged
            usb = attached[0]
            ip = re.search(r"inet ([\d.]+)", _run(["adb", "-s", usb, "shell", "ip", "-f", "inet", "addr", "show",
                                                   "wlan0"]).stdout.decode())
            if not ip:
                return usb  # phone not on Wi-Fi: the cable it is
            _run(["adb", "-s", usb, "tcpip", str(PORT)])
            time.sleep(2)  # adbd restarts on the phone
            serial = "%s:%d" % (ip.group(1), PORT)
            if b"connected to" in _run(["adb", "connect", serial]).stdout:
                _save_ip(ip.group(1))
                _run(["adb", "-s", serial, "wait-for-device"])  # "offline" for a few seconds first
                return serial
            return usb
        ip = _saved_ip()
        if ip and b"connected to" in _run(["adb", "connect", "%s:%d" % (ip, PORT)]).stdout:
            _run(["adb", "-s", "%s:%d" % (ip, PORT), "wait-for-device"])  # "offline" for a few seconds first
            return "%s:%d" % (ip, PORT)
        raise PhoneError("it isn't on adb. If it restarted, plug it in by USB once and ask again")

    def _act(self, action, *params):
        """Run a GSConnect action on the first paired, connected phone (params as GVariant text). PhoneError when
        GSConnect or the phone isn't there."""
        _run(["gdbus", "call", "--session", "-d", BUS, "-o", self._device(), "-m", "org.gtk.Actions.Activate",
              action, "[%s]" % ", ".join("<%s>" % p for p in params), "{}"])

    def _state(self, action):
        """A GSConnect action's state as GVariant text ("(true, 'battery-full-charging-symbolic', 86, 4074)")."""
        return _run(["gdbus", "call", "--session", "-d", BUS, "-o", self._device(), "-m",
                     "org.gtk.Actions.Describe", action]).stdout.decode()

    def _device(self):
        daemon = next((d for d in GSCONNECTS if os.path.exists(d)), None)
        if not daemon:
            raise PhoneError("GSConnect isn't installed")
        ids = _run(["gjs", daemon, "--list-devices"]).stdout.decode().split()  # paired and connected ones
        if not ids:
            raise PhoneError("GSConnect can't see it. Is KDE Connect open on the phone, on the same Wi-Fi")
        return "/org/gnome/Shell/Extensions/GSConnect/Device/" + ids[0]

    def _package(self, name):
        """The installed package whose name holds the app's name ("spotify" -> com.spotify.music), shortest first."""
        key = re.sub(r"[^a-z0-9]", "", name)
        pkgs = re.findall(r"package:(\S+)", self._adb("shell", "pm", "list", "packages"))
        hits = sorted((p for p in pkgs if key in p.replace(".", "").replace("_", "")), key=len)
        return hits[0] if hits else None

    def _number(self, who):
        """(name, number) from the phone's contacts, or a number said as digits; (who, None) when neither."""
        digits = re.sub(r"[^\d+]", "", who)
        if len(digits) >= 7 and not re.search(r"[a-z]", who, re.IGNORECASE):
            return who, digits
        want = who.lower().strip()
        people = self._people()
        hits = ([p for p in people if p[0].lower() == want] or
                [p for p in people if p[0].lower().startswith(want + " ")] or
                [p for p in people if want in p[0].lower()])
        return hits[0] if hits else (who, None)

    def _people(self):
        """[(name, number)] from the phone, read once every 10 minutes."""
        at, people = self._contacts
        if time.time() - at > 600:
            out = self._adb("shell", "content", "query", "--uri", "content://com.android.contacts/data/phones",
                            "--projection", "display_name:data1")
            people = [(n.strip(), re.sub(r"[^\d+]", "", num)) for n, num in
                      re.findall(r"display_name=(.*?), data1=(.*)$", out, re.MULTILINE)]
            self._contacts = (time.time(), people)
        return people

    def _screen(self):
        """[{"label", "at"}] for every labelled thing on the phone's screen."""
        self._adb("shell", "uiautomator", "dump", "/sdcard/jarvis-ui.xml")
        return labelled(self._adb("shell", "cat", "/sdcard/jarvis-ui.xml"))

    def _size(self):
        m = re.search(r"(\d+)x(\d+)", self._adb("shell", "wm", "size"))
        return (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)


def labelled(xml):
    """The UI dump's nodes that have a text or description, with their centre point."""
    try:
        root = ET.fromstring(xml[xml.find("<"):])
    except ET.ParseError:
        return []
    out = []
    for node in root.iter("node"):
        label = node.get("text") or node.get("content-desc") or ""
        box = re.findall(r"\d+", node.get("bounds", ""))
        if label.strip() and len(box) == 4:
            x1, y1, x2, y2 = map(int, box)
            out.append({"label": label.strip(), "at": ((x1 + x2) // 2, (y1 + y2) // 2)})
    return out


def _find(nodes, words):
    """The node whose label is `words` (any case), else the first that holds it."""
    want = words.lower().strip()
    return (next((n for n in nodes if n["label"].lower() == want), None) or
            next((n for n in nodes if want in n["label"].lower()), None))


def _international(number):
    """WhatsApp wants the country code and no +: "+91 98..." / "098..." / "98..." -> "9198..."."""
    digits = number[1:] if number.startswith("+") else number.lstrip("0")
    return COUNTRY + digits if len(digits) == 10 else digits


def _fresh_screenshot():
    """[the newest screenshot in Pictures] when it's under a minute old, else []."""
    shots = glob.glob(os.path.join(PICTURES, "Screenshot*"))
    newest = max(shots, key=os.path.getmtime, default=None)
    return [newest] if newest and time.time() - os.path.getmtime(newest) < 60 else []


def _gstr(text):
    """`text` as a GVariant string literal."""
    return "'%s'" % text.replace("\\", "\\\\").replace("'", "\\'")


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=15)
    except FileNotFoundError:
        raise PhoneError("adb isn't installed. Run: sudo apt install adb")
    except subprocess.TimeoutExpired:
        raise PhoneError("it didn't answer")


def _saved_ip():
    try:
        with open(IP_FILE) as f:
            return f.read().strip()
    except OSError:
        return None


def _save_ip(ip):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(IP_FILE, "w") as f:
        f.write(ip)
