"""Maths and units, worked out locally: "what's 18% of 2400", "50 miles in km",
"how many feet in 3 metres", "100 fahrenheit in celsius"."""
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
SPOKEN_OPS = [(r"\bplus\b|\band\b", "+"), (r"\bminus\b|\btake away\b", "-"), (r"\btimes\b|\bmultiplied by\b|\bx\b", "*"),
              (r"\bdivided by\b|\bover\b", "/"), (r"\bto the power of\b|\bto the\b|\braised to\b", "**"),
              (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"), (r"\bmod\b|\bmodulo\b", "%"), ("×", "*"), ("÷", "/"),
              (r"\^", "**")]


class Maths(Skill):
    group = "maths and units (instant)"
    does = "Works out sums, percentages and unit conversions locally"
    phrases = ("what's 18% of 2400", "50 miles in km", "100 fahrenheit in celsius")
    gives = "speech"
    permission = "auto"
    checks = "computed exactly; anything it can't work out (currencies, divide by zero, huge powers) goes to Claude"

    def patterns(self):
        n = r"([\d.,]+|%s)" % "|".join(sorted(NUM_WORDS, key=len, reverse=True))
        return [
            (r"(?:what'?s |what is |convert )?%s ?([a-z/ ]+?) (?:in|to|into|in to) ([a-z/ ]+)" % n, self.units),
            (r"how many ([a-z/ ]+?) (?:are )?(?:in|is) %s ?([a-z/ ]+)" % n, self.units_how_many),
            (r"(?:what'?s |what is |calculate |compute |work out |how much is )?(.*\d.*)", self.maths),
        ]

    def maths(self, m):
        value = calculate(m.group(0))
        if value is None:
            return False
        self.say(_spoken(value) + ".")

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


def calculate(expr):
    """Safely work out "18% of 2400", "12 times 7", "square root of 81"; None if it isn't maths."""
    e = expr.lower().strip().rstrip("?.")
    e = re.sub(r"^(?:what'?s|what is|calculate|compute|work out|how much is)\s+", "", e)
    m = re.fullmatch(r"([\d.,]+)\s*(?:%|percent) of ([\d.,]+)", e)
    if m:
        return _number(m.group(1)) / 100 * _number(m.group(2))
    m = re.fullmatch(r"(?:the )?square root of ([\d.,]+)", e)
    if m:
        return math.sqrt(_number(m.group(1)))
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
