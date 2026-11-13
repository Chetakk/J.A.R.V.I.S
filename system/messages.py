"""What to do with a message from the phone (WhatsApp, SMS, Instagram...): decide(item, seen, rules, now) ->
(action, line). action: "say" (line spoken), "chime" (a soft sound, nothing spoken) or "silent" (panel only).
Rules (settings.yaml `messages:`): personal: [names] (who, never the text), quiet_chats: [names] (a chime only).
Order: one-time codes, unknown numbers, a burst (the first is said, the rest silent: the caller says more_line()
once the sender has been quiet for GAP), personal, quiet chats, media, long, short."""
import random
import re

GAP = 30  # s: a message within this of the sender's last one is part of the same burst
LONG = (160, 25)  # characters or words: "sent a long message"
OTP = re.compile(r"\botp\b|one[- ]time|verification code|passcode|security code|\bcode\b.{0,20}\b\d{4,8}\b|"
                 r"\b\d{4,8}\b.{0,20}\bcode\b", re.I)
UNKNOWN = re.compile(r"^[+\d\s()-]{6,}$|^[A-Z]{2}-[A-Z0-9]{3,}$")  # a number or an SMS short code, not a saved name
MEDIA = re.compile(r"\bsent (?:an? |\d+ )?(?:photos?|videos?|images?|stickers?|gifs?|audio|voice|documents?|"
                   r"contacts?|locations?|files?)[\w ]*|^\W*(?:voice message|photo|video|sticker|gif|audio)\b", re.I)
MARKS = re.compile("[\U0001F000-\U0001FAFF①-⓿☀-➿⬀-⯿️‍]")


def say(*lines):
    return "say", random.choice(lines)


def decide(item, seen, rules, now, known=None):
    """`seen`: {sender: [times]} kept by the caller (this message is added here). `known`: the sender is a saved
    contact (None: not looked up, a number or short code counts as unknown)."""
    who, body, app = item["title"] or item["app"], item["body"], item["app"]
    times = seen.setdefault(who, [])
    times[:] = (times if times and now - times[-1] < GAP else []) + [now]
    low = who.lower()
    if OTP.search(body):
        return "silent", "A code from %s is on the panel." % who
    if (not known) if known is not None else UNKNOWN.match(who):
        return "silent", ""  # not a saved contact (spam, delivery, bank, groups): panel only
    if len(times) > 1:
        return "silent", ""
    if any(p.lower() in low for p in rules.get("personal") or []):
        return say("%s messaged you." % who, "%s wrote to you." % who,
                   "Something from %s. It's private, so I'll leave it to you." % who, "%s is on the phone for you." % who)
    if any(q.lower() in low for q in rules.get("quiet_chats") or []):
        return "chime", ""
    text = " ".join(MARKS.sub("", body).split())
    media = MEDIA.search(text)
    if media:
        sent = media.group(0).strip()
        sent = sent.lower() if sent.lower().startswith("sent") else "sent a " + sent.lower()
        f = who
        return say("%s %s." % (f, sent), "%s %s. It's on your phone." % (f, sent), "Heads up: %s %s." % (f, sent))
    if len(text) > LONG[0] or len(text.split()) > LONG[1]:
        f = who
        return say("%s sent a long one. Say read it, or ask what it says." % f, "A long message from %s. Want it read, or the short version?" % f,
                   "%s wrote an essay. Say summarize it for the gist." % f)
    kind = "Text" if app in ("Messages", "WhatsApp") else app
    return say("%s from %s: %s" % (kind, who, body), "%s says: %s" % (who, body), "%s is saying: %s" % (who, body),
               "%s writes: %s" % (who, body), "%s messaged: %s" % (who, body))


def more_line(who, n):
    """Said once a burst is over: `n` messages after the first one that was said."""
    more = "one more" if n == 1 else "%d more" % n
    lines = ["%s sent %s. Say read them." % (who, more), "%s from %s. Say read them to hear them." % (more.capitalize(), who)]
    if n >= 5:
        lines = [l + " Or mute messages from %s." % who for l in lines]
    return random.choice(lines)
