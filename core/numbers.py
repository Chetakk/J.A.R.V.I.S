"""A number said as a word means the same as its digits: "workspace three" is "workspace 3", and the other way round.
jarvis.py tries the other form when what was said matches nothing as it is, so either spelling reaches the same skill.
"""
import re

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
       "seventeen eighteen nineteen".split()
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
VALUE = dict({w: n for n, w in enumerate(ONES)}, **TENS)
# "this one", "the one before", "which one": a thing, not the number 1
NOT_A_NUMBER = {"this", "that", "the", "which", "any", "every", "each", "no", "some", "another", "other", "next",
                "last", "first", "second", "third", "fourth", "fifth", "sixth", "only", "same", "right", "wrong", "new", "old", "big", "little", "a", "one"}
WORD = re.compile(r"\b(%s)(?:[ -](%s))?\b" % ("|".join(TENS), "|".join(ONES[1:10])) + r"|\b(%s)\b" % "|".join(ONES),
                  re.I)
DIGITS = re.compile(r"(?<![\d.:,/-])\b(\d{1,2})\b(?![.:,/]?\d)")  # not 1.5, 5:30, 3/4 or 1,000


def as_digits(text):
    """ "go to workspace three" -> "go to workspace 3", "twenty five minutes" -> "25 minutes"; "this one" stays.
    shortcut: 0-99 only (no "a hundred and five"), widen if a command needs bigger spoken numbers."""
    def sub(m):
        if m.group(3):
            before = text[:m.start()].split()
            if m.group(3).lower() == "one" and before and before[-1].lower() in NOT_A_NUMBER:
                return m.group(0)
            return str(VALUE[m.group(3).lower()])
        return str(TENS[m.group(1).lower()] + (VALUE[m.group(2).lower()] if m.group(2) else 0))
    return WORD.sub(sub, text)


def as_words(text):
    """ "go to workspace 3" -> "go to workspace three", "25" -> "twenty five"; 1.5, 5:30 and 100 stay as they are."""
    def sub(m):
        n = int(m.group(1))
        if n < 20:
            return ONES[n]
        tens = next(w for w, v in TENS.items() if v == n // 10 * 10)
        return tens + (" " + ONES[n % 10] if n % 10 else "")
    return DIGITS.sub(sub, text)


if __name__ == "__main__":
    assert as_digits("go to workspace three") == "go to workspace 3"
    assert as_digits("set a timer for twenty five minutes") == "set a timer for 25 minutes"
    assert as_digits("close this one") == "close this one"
    assert as_digits("Third one") == "Third one"
    assert as_digits("someone") == "someone"
    assert as_words("go to workspace 3") == "go to workspace three"
    assert as_words("1.5 speed at 5:30, 100 and 42") == "1.5 speed at 5:30, 100 and forty two"
    print("ok")
