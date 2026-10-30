"""Maths and units, worked out locally: "what's 18% of 2400", "50 miles in km",
"how many feet in 3 metres", "100 fahrenheit in celsius". Numbers can be said as words ("twelve times seven"),
and the everyday forms work too: "half of 90", "20 percent off 500", "what percent is 30 of 120", "10 factorial",
"average of 4, 6 and 8", "sin 30", "5 feet 10 inches in cm". Currencies need live rates, so they go to Claude."""
import ast
import math
import operator
import re

from skills.base import Skill

NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
             "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "hundred": 100, "half an": 0.5}
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
# Spoken numbers, turned into digits before anything else: "twenty five" -> 25, "three point five" -> 3.5
ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                   "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
SCALES = {"hundred": 100, "thousand": 1000, "lakh": 10 ** 5, "million": 10 ** 6, "crore": 10 ** 7,
          "billion": 10 ** 9}
FRACTIONS = {"half": 2, "halves": 2, "third": 3, "quarter": 4, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
             "eighth": 8, "ninth": 9, "tenth": 10, "hundredth": 100}
TRIG = {"sin": math.sin, "sine": math.sin, "cos": math.cos, "cosine": math.cos, "tan": math.tan, "tangent": math.tan}
N = r"(-?[\d.]+)"
LIST = r"((?:-?[\d.]+(?:,? (?:and )?|, ?))+-?[\d.]+)"
SPOKEN_OPS = [(r"\bplus\b|\band\b", "+"), (r"\bminus\b|\btake away\b", "-"), (r"\btimes\b|\bmultiplied by\b|\bx\b", "*"),
              (r"\bdivided by\b|\bover\b", "/"), (r"\bto the power of\b|\bto the\b|\braised to\b", "**"),
              (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"), (r"\bmod\b|\bmodulo\b", "%"), ("×", "*"), ("÷", "/"),
              (r"\^", "**")]


class Maths(Skill):
    group = "maths and units (instant)"
    does = "Works out sums, percentages and unit conversions locally"
    phrases = ("what's 18% of 2400", "twelve times seven", "half of 90", "20 percent off 500",
               "what percent is 30 of 120", "average of 4, 6 and 8", "50 miles in km", "100 fahrenheit in celsius",
               "5 feet 10 inches in cm")
    gives = "speech"
    permission = "auto"
    checks = "computed exactly; anything it can't work out (currencies, divide by zero, huge powers) goes to Claude"

    def patterns(self):
        n = r"([\d.,]+|%s)" % "|".join(sorted(NUM_WORDS, key=len, reverse=True))
        return [
            (r"(?:what'?s |what is |convert )?%s ?([a-z]+) (?:and )?([\d.]+) ?([a-z]+) (?:in|to|into|in to) ([a-z/ ]+)" % n,
             self.units_mixed),
            (r"(?:what'?s |what is |convert )?%s ?([a-z/ ]+?) (?:in|to|into|in to) ([a-z/ ]+)" % n, self.units),
            (r"how many ([a-z/ ]+?) (?:are )?(?:in|is) %s ?([a-z/ ]+)" % n, self.units_how_many),
            (r"(?:what'?s |what is |calculate |compute |work out |how much is )?(.*\d.*)", self.maths),
        ]

    def handle(self, text):
        return super().handle(digits(text))

    def maths(self, m):
        found = solve(m.group(0))
        if found is None:
            return False
        self.say(found[1] % _spoken(found[0]) + ".")

    def units_mixed(self, m):
        """"5 feet 10 inches in cm": both parts in the same unit kind, added up."""
        try:
            a, b = _number(m.group(1)), _number(m.group(3))
        except ValueError:
            return False
        first, second, dst = (g.strip() for g in m.group(2, 4, 5))
        x, y = convert(a, first, dst), convert(b, second, dst)
        if x is None or y is None:
            return False
        self.say("%s %s %s %s is %s %s." % (_spoken(a), first, _spoken(b), second, _spoken(x + y), dst))

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


def digits(text):
    """Spoken numbers as digits: "what is twenty five plus three point five" -> "what is 25 plus 3.5".
    "a"/"an" only count before a scale ("a hundred"), so "a third of 90" and "add a note" keep theirs."""
    words = re.split(r"(\s+|-(?=[a-z]))", text)
    out, run = [], []

    def flush():
        while run and run[-1][0] in ("and", "point", "a", "an"):  # "one and" -> "1 and"
            tail = run.pop()
            flush()
            out.append(tail[1])
            return
        if run:
            out.append(_words_value([w for w, _ in run]))
            del run[:]

    for i, raw in enumerate(words):
        if i % 2:  # the separators
            if not run:
                out.append(raw)
            continue
        w = raw.lower()
        nxt = next((x.lower() for x in words[i + 2::2] if x.strip()), "")
        number = w in ONES or w in TENS or w in SCALES
        joins = run and (w == "and" and run[-1][0] in SCALES and (nxt in ONES or nxt in TENS)
                         or w == "point" and nxt in ONES)
        if number or joins or (w in ("a", "an") and nxt in SCALES) or (run and run[-1][0] == "point" and w in ONES):
            run.append((w, raw))
            continue
        if run:
            flush()
            out.append(" ")
        out.append(raw)
    flush()
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _words_value(words):
    total, now, decimals = 0, 0, None
    for w in words:
        if decimals is not None:
            decimals += str(ONES[w])
        elif w == "point":
            decimals = ""
        elif w in ("a", "an"):
            now = 1
        elif w in ONES:
            now += ONES[w]
        elif w in TENS:
            now += TENS[w]
        elif w == "hundred":
            now = (now or 1) * 100
        elif w in SCALES:
            total += (now or 1) * SCALES[w]
            now = 0
    value = total + now
    return "%d.%s" % (value, decimals) if decimals else str(value)


def solve(expr):
    """(value, "%s ..." how to say it) for a sum, or None if it isn't one."""
    e = digits(expr).lower().strip().rstrip("?.!")
    e = re.sub(r"^(?:what'?s|what is|calculate|compute|work out|how much is|tell me|find)\s+", "", e)
    e = re.sub(r"^(?:the )", "", e).replace(",", "") if not re.search(r"\d,\d{3}", e) else e.replace(",", "")
    e = re.sub(r"\s+", " ", e)
    num = _number
    forms = [
        (r"%s ?(?:%%|percent) of %s" % (N, N), lambda a, b: (num(a) / 100 * num(b), "%s")),
        (r"(?:square root|root) of %s" % N, lambda a: (math.sqrt(num(a)), "%s")),
        (r"cube root of %s" % N, lambda a: (math.copysign(abs(num(a)) ** (1 / 3), num(a)), "%s")),
        (r"(?:a |one )?half of %s|%s halved|halve %s" % (N, N, N),
         lambda a, b, c: (num(a or b or c) / 2, "%s")),
        (r"(?:(a|one|%s) )?(%s)s? of %s" % (r"[\d.]+", "|".join(FRACTIONS), N),
         lambda k, f, a: (num(k if k and k not in ("a", "one") else "1") * num(a) / FRACTIONS[f], "%s")),
        (r"(?:double|twice) %s|%s doubled" % (N, N), lambda a, b: (num(a or b) * 2, "%s")),
        (r"(?:triple|treble|thrice) %s|%s tripled" % (N, N), lambda a, b: (num(a or b) * 3, "%s")),
        (r"%s ?(?:%%|percent) (?:off|discount on|off of) %s" % (N, N),
         lambda a, b: (num(b) * (1 - num(a) / 100), "%s")),
        (r"%s (?:minus|less) %s ?(?:%%|percent)" % (N, N), lambda b, a: (num(b) * (1 - num(a) / 100), "%s")),
        (r"%s (?:plus|with) %s ?(?:%%|percent)(?: (?:tax|gst|vat))?" % (N, N),
         lambda b, a: (num(b) * (1 + num(a) / 100), "%s")),
        (r"(?:increase|raise|add) %s (?:by|to) %s ?(?:%%|percent)|%s ?(?:%%|percent) more than %s" % (N, N, N, N),
         lambda b, a, c, d: (num(b or d) * (1 + num(a or c) / 100), "%s")),
        (r"(?:decrease|reduce|lower|cut) %s by %s ?(?:%%|percent)|%s ?(?:%%|percent) less than %s" % (N, N, N, N),
         lambda b, a, c, d: (num(b or d) * (1 - num(a or c) / 100), "%s")),
        (r"(?:a )?%s ?(?:%%|percent) (tip|tax|gst|vat|service charge) (?:on|for) %s" % (N, N),
         lambda a, what, b: (num(a) / 100 * num(b), "%%s %s, so %s in total" % (what, _spoken(num(b) * (1 + num(a) / 100))))),
        (r"what (?:percent|percentage) (?:is|of) %s (?:is |of |out of )?%s|%s is what (?:percent|percentage) of %s|"
         r"%s out of %s (?:as a|in) (?:percent|percentage)" % (N, N, N, N, N, N),
         lambda a, b, c, d, e2, f: (num(a or c or e2) / num(b or d or f) * 100, "%s percent")),
        (r"(?:the )?(?:factorial of %s|%s factorial|%s !)" % (N, N, N),
         lambda a, b, c: (_factorial(num(a or b or c)), "%s")),
        (r"(?:the )?(?:average|mean) of %s" % LIST, lambda xs: (_mean(_numbers(xs)), "%s")),
        (r"(?:the )?(?:sum|total) of %s" % LIST, lambda xs: (sum(_numbers(xs)), "%s")),
        (r"(%s)(?: of)? %s(?: degrees?)?" % ("|".join(TRIG), N),
         lambda f, a: (_trig(f, num(a)), "%s")),
        (r"(?:log|log base 10) (?:of )?%s" % N, lambda a: (math.log10(num(a)), "%s")),
        (r"(?:natural log|ln) (?:of )?%s" % N, lambda a: (math.log(num(a)), "%s")),
    ]
    for pattern, fn in forms:
        m = re.fullmatch(pattern, e)
        if m:
            try:
                return fn(*m.groups())
            except (ValueError, ZeroDivisionError, OverflowError):
                return None
    value = _expression(e)
    return None if value is None else (value, "%s")


def _numbers(text):
    return [_number(x) for x in re.findall(r"-?[\d.]+", text)]


def _mean(xs):
    return sum(xs) / len(xs)


def _factorial(x):
    if x != int(x) or not 0 <= x <= 170:
        raise ValueError("no factorial")
    return float(math.factorial(int(x)))


def _trig(name, degrees):
    value = TRIG[name](math.radians(degrees))
    if abs(value) > 1e10:
        raise ValueError("undefined")  # tan 90
    return round(value, 12)


def calculate(expr):
    """Safely work out "18% of 2400", "twelve times seven", "half of 90"; None if it isn't maths."""
    found = solve(expr)
    return found and found[0]


def _expression(e):
    """"12 * 7", "(2 + 3) times 4": plain arithmetic, parsed safely."""
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
