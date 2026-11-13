"""What to do with a message from the phone (WhatsApp, SMS, Instagram...): decide(item, seen, rules, now) ->
(action, line). action: "say" (line spoken), "chime" (a soft sound, nothing spoken) or "silent" (panel only).
Rules (settings.yaml `messages:`): personal: [names] (who, never the text), quiet_chats: [names] (a chime only).
Order: one-time codes, unknown numbers, personal, quiet chats, a burst (3rd: said once, 8th: said again), media, long, short."""
import random
import re

BURST = (3, 120)  # this many messages from one sender within this many seconds: said once, the rest silent
LONG = (160, 25)  # characters or words: "sent a long message"
OTP = re.compile(r"\botp\b|one[- ]time|verification code|passcode|security code|\bcode\b.{0,20}\b\d{4,8}\b|"
                 r"\b\d{4,8}\b.{0,20}\bcode\b", re.I)
UNKNOWN = re.compile(r"^[+\d\s()-]{6,}$|^[A-Z]{2}-[A-Z0-9]{3,}$")  # a number or an SMS short code, not a saved name
MEDIA = re.compile(r"\bsent (?:an? |\d+ )?(?:photos?|videos?|images?|stickers?|gifs?|audio|voice|documents?|"
                   r"contacts?|locations?|files?)[\w ]*|^\W*(?:voice message|photo|video|sticker|gif|audio)\b", re.I)
MARKS = re.compile("[\U0001F000-\U0001FAFF①-⓿☀-➿⬀-⯿️‍]")


def say(*lines):
    return "say", random.choice(lines)


def first(name):
    return name  # the whole name as the phone shows it ("Avani Ma'am"): "mute messages from" it works the same


def decide(item, seen, rules, now, known=None):
    """`seen`: {sender: [times]} kept by the caller (this message is added here). `known`: the sender is a saved
    contact (None: not looked up, a number or short code counts as unknown)."""
    who, body, app = item["title"] or item["app"], item["body"], item["app"]
    times = seen.setdefault(who, [])
    times[:] = [t for t in times if now - t < BURST[1]] + [now]
    low = who.lower()
    if OTP.search(body):
        return "silent", "A code from %s is on the panel." % who
    if (not known) if known is not None else UNKNOWN.match(who):
        return "silent", ""  # not a saved contact (spam, delivery, bank, groups): panel only
    if any(p.lower() in low for p in rules.get("personal") or []):
        return say("%s messaged you." % first(who), "%s wrote to you." % first(who), "Something from %s. It's private, so I'll leave it to you." % first(who), "%s is on the phone for you." % first(who)) if len(times) < BURST[0] else ("silent", "")
    if any(q.lower() in low for q in rules.get("quiet_chats") or []):
        return "chime", ""
    if len(times) == BURST[0]:
        f = first(who)
        return say("%s is on a roll: three already. Say mute messages from %s if it's too much." % (f, f),
                   "%s has sent a few now. I'll keep the rest quiet. Say mute messages from %s to silence the lot." % (f, f),
                   "That's three from %s. The rest go to the panel." % f)
    if len(times) == 8:
        n, f = len(times), first(who)
        return say("%s is at %d messages now. Say mute messages from %s for an hour." % (f, n, f), "Still going: %d from %s. Say mute messages from %s if you've had enough." % (n, f, f),
                   "%s again, %d now. I can mute for an hour if you like." % (f, n))
    if len(times) > BURST[0]:
        return "silent", ""
    text = " ".join(MARKS.sub("", body).split())
    media = MEDIA.search(text)
    if media:
        sent = media.group(0).strip()
        sent = sent.lower() if sent.lower().startswith("sent") else "sent a " + sent.lower()
        f = first(who)
        return say("%s %s." % (f, sent), "%s %s. It's on your phone." % (f, sent), "Heads up: %s %s." % (f, sent))
    if len(text) > LONG[0] or len(text.split()) > LONG[1]:
        f = first(who)
        return say("%s sent a long one. Say read it, or ask what it says." % f, "A long message from %s. Want it read, or the short version?" % f,
                   "%s wrote an essay. Say summarize it for the gist." % f)
    kind = "Text" if app in ("Messages", "WhatsApp") else app
    if len(times) == 2:  # right after another one: less to say
        return say("%s from %s again: %s" % (kind, who, body), "Another %s from %s: %s" % (kind.lower(), who, body),
                   "%s adds: %s" % (who, body), "%s again: %s" % (who, body))
    return say("%s from %s: %s" % (kind, who, body), "%s says: %s" % (who, body), "%s is saying: %s" % (who, body),
               "%s writes: %s" % (who, body), "%s messaged: %s" % (who, body))
