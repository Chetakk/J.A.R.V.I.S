"""Weather and the time anywhere: "what's the weather in delhi tomorrow", "will it rain today", "time in tokyo",
"time difference between here and london". No city means yours (settings `weather: home`).

The time needs no internet: the system's own time zone files (TZ=... date), with a city found by its time zone
name ("tokyo" is Asia/Tokyo) or a short list of the rest ("delhi", "japan"). Weather is one call to Open-Meteo
(free, no key; only the place name and its coordinates are sent), ~0.5 s. Places it had to look up are kept in
~/.local/share/jarvis/places.json, so each is looked up once."""
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

from core.store import load_json, save_json
from skills.base import Skill

PLACES = os.path.expanduser("~/.local/share/jarvis/places.json")
ZONES = "/usr/share/zoneinfo"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
# Places whose time zone isn't named after them (the rest are found in the time zone files)
ALIASES = {
    "india": "Asia/Kolkata", "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "bombay": "Asia/Kolkata", "bengaluru": "Asia/Kolkata", "bangalore": "Asia/Kolkata", "chennai": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata", "pune": "Asia/Kolkata", "goa": "Asia/Kolkata", "japan": "Asia/Tokyo",
    "china": "Asia/Shanghai", "beijing": "Asia/Shanghai", "uk": "Europe/London", "england": "Europe/London",
    "britain": "Europe/London", "scotland": "Europe/London", "edinburgh": "Europe/London", "france": "Europe/Paris",
    "germany": "Europe/Berlin", "italy": "Europe/Rome", "spain": "Europe/Madrid", "uae": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai", "nepal": "Asia/Kathmandu", "pakistan": "Asia/Karachi", "sri lanka": "Asia/Colombo",
    "bangladesh": "Asia/Dhaka", "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "california": "America/Los_Angeles", "washington": "America/New_York", "boston": "America/New_York",
    "miami": "America/New_York", "texas": "America/Chicago", "dallas": "America/Chicago",
    "houston": "America/Chicago", "ottawa": "America/Toronto", "montreal": "America/Toronto",
    "egypt": "Africa/Cairo", "russia": "Europe/Moscow", "singapore": "Asia/Singapore",
}
HOME_WORDS = re.compile(r"^(?:here|home|my (?:place|city|area|location|town)|where i am|outside|)$", re.I)
# "the temperature of the cpu", "how hot is the gpu": the computer's own (the command list's)
NOT_THE_COMPUTER = r"(?!.*\b(?:cpu|gpu|computer|processor|chip|jetson|board|laptop|pc)\b)"
SKY = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
       73: "snow", 75: "heavy snow", 77: "snow", 80: "light showers", 81: "showers", 82: "heavy showers",
       85: "snow showers", 86: "snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
       99: "thunderstorms with hail"}

PLACE = r"(?:in|at|for) (?:the )?(.+?)"
WHEN = r"(today|tonight|tomorrow|this week|this weekend|right now|now|outside|later)"
WEATHER = r"(?:weather|forecast|weather forecast|weather report)"
# A bare "what's the temperature" is the computer's too: this one needs a place, a day or "outside"
TEMPERATURE = r"(?=.*\b(?:in|at|for|outside|today|tonight|tomorrow|this week)\b)(?:outside )?temperature(?: outside)?"
ASK = r"(?:(?:what(?:'s| is| will be)|how(?:'s| is)|tell me|give me|check|get|show me|i want to know)(?: me)? )?"


DAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|the weekend|weekend)"


def _clean(place):
    place = re.sub(r"\s+(?:on|this|next|later|today|tonight|tomorrow)\b.*$", "", (place or "").strip(), flags=re.I)
    place = re.sub(r"\s*(?:,|\bplease\b|\bfor me\b|\bright now\b|\bnow\b|\bat the moment\b|\bcurrently\b)\s*$", "",
                   place, flags=re.I)
    return re.sub(r"^(?:the city of|city of|the)\s+", "", place, flags=re.I).strip()


def _zone_names():
    """{"new york": "America/New_York", ...} from the time zone files' own names."""
    names = {}
    for region in ("Africa", "America", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific"):
        for root, _, files in os.walk(os.path.join(ZONES, region)):
            for f in files:
                zone = os.path.relpath(os.path.join(root, f), ZONES)
                names.setdefault(f.replace("_", " ").lower(), zone)
    return names


def _get(url, params, timeout=4):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "JARVIS-voice-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _hours(minutes):
    """210 -> "3 and a half hours"."""
    h, m = divmod(abs(minutes), 60)
    part = {0: "", 15: " and a quarter", 30: " and a half", 45: " and three quarters"}.get(m, " %d minutes" % m)
    if not h:
        return {15: "a quarter of an hour", 30: "half an hour", 45: "three quarters of an hour"}.get(m, "%d minutes" % m)
    return "%d%s hour%s" % (h, part, "" if h == 1 and not part else "s")


class _Groups:
    """A match's groups, for handing one handler's request to another."""

    def __init__(self, *groups):
        self.g = groups

    def groups(self):
        return self.g

    def group(self, i):
        return self.g[i - 1]


class Weather(Skill):
    group = "time and weather"
    does = "The weather and the time anywhere, yours when no place is said; time zones need no internet"
    phrases = ("what's the weather", "what's the weather in <name>", "weather tomorrow", "will it rain today",
               "do i need an umbrella", "temperature outside", "what time is it in <name>",
               "time difference between here and <name>")
    gives = "a spoken answer; a card with the next days for a forecast"
    checks = "says when the weather service can't be reached or a place isn't found, instead of guessing"

    def __init__(self, jarvis):
        super().__init__(jarvis)
        self.zones = None
        self.places = load_json(PLACES, {})

    def patterns(self):
        rain = r"(rain|be raining|be rainy|snow|be sunny|be cloudy|be hot|be cold|be windy|be warm)"
        return [
            (r"(?:what(?:'s| is) the )?time difference between (.+?) and (.+)", self.difference),
            (r"what time is it (?:now |right now |currently )?%s" % PLACE, lambda m: self.time(m.group(1))),
            (r"%s(?:the )?(?:current |local )?(?:local )?time (?:now |right now )?%s" % (ASK, PLACE),
             lambda m: self.time(m.group(1))),
            # "what's the weather in delhi tomorrow", "tomorrow's weather", "temperature outside"
            (NOT_THE_COMPUTER + r"%s(?:the )?(today's |tomorrow's |tonight's )?(?:current )?(?:%s|%s)"
             r"(?: like| going to be(?: like)?| report)?(?: %s)?(?: %s)?(?: %s)?"
             % (ASK, WEATHER, TEMPERATURE, PLACE, WHEN, PLACE), self.weather),
            (r"(?:will|is) it (?:going to )?%s(?: %s)?(?: %s)?(?: %s)?" % (rain, PLACE, WHEN, PLACE), self.will_it),
            (r"is it (raining|snowing|sunny|cloudy|hot|cold|windy)(?: %s)?(?: %s)?(?: %s)?" % (PLACE, WHEN, PLACE),
             self.will_it),
            (r"do i need (?:an? )?(umbrella|raincoat|jacket|coat|sweater)(?: %s)?(?: %s)?(?: %s)?"
             % (PLACE, WHEN, PLACE), self.will_it),
            # (a bare "how hot is it" is the computer's temperature)
            (NOT_THE_COMPUTER + r"how (hot|cold|warm) is it (?:outside(?: %s)?|%s)(?: %s)?" % (WHEN, PLACE, WHEN), self.how_hot),
            # anything else short about the weather: "what does the weather look like this week", "is it going to
            # rain later today" ("google the weather in X", "open the weather app", "write a poem about rain" and
            # "ask claude ..." go on to the browser, apps and Claude)
            (NOT_THE_COMPUTER + r"(?!(?:open|launch|google|search|look up|browse|close|ask|write|compose|make|sing)\b)"
             r"(?!.*\b(?:claude|poem|story|song|joke|essay|why|explain|history|movie|film|book|game)\b)"
             r"(?=.*\b(?:weather|forecast|rain|raining|rainy|umbrella|raincoat|raingear|snow|snowing)\b)"
             r"(?!(?:\S+ ){12}).+", self.loose),
        ]

    # --- where ---------------------------------------------------------------------------------------------

    def home(self):
        return (self.j.settings.get("weather") or {}).get("home") or "Bengaluru"

    def zone(self, place):
        """The time zone for a place, or None. Offline first; an unknown city is looked up once."""
        key = _clean(place).lower()
        if key in ALIASES:
            return ALIASES[key]
        if self.zones is None:
            self.zones = _zone_names()
        if key in self.zones:
            return self.zones[key]
        found = self.find(place)
        return found and found.get("tz")

    def find(self, place):
        """{"name", "country", "lat", "lon", "tz"} for a place (looked up once, then kept), or None.
        Raises OSError when the lookup service can't be reached."""
        key = _clean(place).lower()
        if key in self.places:
            return self.places[key]
        words = key.split()
        for name in [key] + ([" ".join(words[:-1])] if len(words) > 1 else []):
            hits = _get(GEOCODE, {"name": name, "count": 5, "language": "en", "format": "json"}).get("results") or []
            # "paris france": the one in that country
            hit = next((h for h in hits if name != key and words[-1] in (h.get("country") or "").lower()),
                       hits[0] if hits else None)
            if hit:
                self.places[key] = {"name": hit["name"], "country": hit.get("country", ""), "lat": hit["latitude"],
                                    "lon": hit["longitude"], "tz": hit.get("timezone")}
                save_json(PLACES, self.places, indent=1)
                return self.places[key]
        return None

    def _where(self, *places):
        """The place said (cleaned), or home."""
        place = next((_clean(p) for p in places if p and not HOME_WORDS.match(_clean(p))), "")
        return place or self.home()

    # --- time ----------------------------------------------------------------------------------------------

    def _date(self, zone, fmt):
        return subprocess.run(["env", "TZ=" + zone, "date", fmt], capture_output=True, text=True,
                              timeout=3).stdout.strip()

    def _offset(self, zone):
        """Minutes ahead of UTC."""
        z = self._date(zone, "+%z")
        return (1 if z[0] == "+" else -1) * (int(z[1:3]) * 60 + int(z[3:5]))

    def _zone_or_say(self, place):
        try:
            zone = self.zone(place)
        except (OSError, ValueError) as e:
            self.say("I don't know %s's time zone offline, and I couldn't look it up: %s."
                     % (place, getattr(e, "reason", e)))
            return None
        if not zone:
            self.say("I couldn't find a place called %s." % place)
        return zone

    def time(self, place):
        place = self._where(place)
        zone = self._zone_or_say(place)
        if not zone:
            return
        here = self.zone(self.home())
        said = self._date(zone, "+%-I:%M %p on %A")
        ahead = self._offset(zone) - self._offset(here) if here else 0
        if not ahead:
            self.say("It's %s in %s, the same as here." % (said, place.title()))
            return
        self.say("It's %s in %s, %s %s you." % (said, place.title(), _hours(ahead),
                                                "ahead of" if ahead > 0 else "behind"))

    def difference(self, m):
        a, b = self._where(m.group(1)), self._where(m.group(2))
        za = self._zone_or_say(a)
        zb = za and self._zone_or_say(b)
        if not zb:
            return
        ahead = self._offset(zb) - self._offset(za)
        if not ahead:
            self.say("%s and %s have the same time." % (a.title(), b.title()))
            return
        self.say("%s is %s %s %s." % (b.title(), _hours(ahead), "ahead of" if ahead > 0 else "behind", a.title()))

    # --- weather -------------------------------------------------------------------------------------------

    def forecast(self, place):
        """(place info, Open-Meteo reply), or None after saying why not."""
        try:
            where = self.find(place)
            if not where:
                self.say("I couldn't find a place called %s." % place)
                return None
            got = _get(FORECAST, {
                "latitude": where["lat"], "longitude": where["lon"], "timezone": "auto", "forecast_days": 7,
                "current": "temperature_2m,apparent_temperature,weather_code,relative_humidity_2m,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
        except (OSError, ValueError) as e:
            self.say("I couldn't reach the weather service: %s." % getattr(e, "reason", e))
            return None
        return where, got

    def _day(self, when):
        return 1 if (when or "").lower() == "tomorrow" else 0

    def weather(self, m):
        pre, place, when, place2 = m.groups()
        if re.search(r"\b(?:on )?%s\b" % DAYS, "%s %s" % (place or "", place2 or ""), re.I):
            when = when or "this week"  # "weather in chennai on friday": the days are on the card
        when = when or (pre or "").replace("'s", "").strip()
        place = self._where(place, place2)
        got = self.forecast(place)
        if not got:
            return
        where, w = got
        name, day, d = where["name"], self._day(when), w["daily"]
        if (when or "").lower() in ("this week", "this weekend"):
            self.j.show({"title": "Weather in %s" % name, "text": "\n".join(
                "- **%s**: %s, %d to %d°, %d%% rain" % (date, SKY.get(code, "mixed"), lo, hi, rain or 0)
                for date, code, lo, hi, rain in zip(d["time"], d["weather_code"], d["temperature_2m_min"],
                                                   d["temperature_2m_max"], d["precipitation_probability_max"]))})
            common = max(set(d["weather_code"]), key=d["weather_code"].count)
            self.say("This week in %s: %d to %d degrees, mostly %s. The days are on the card." % (
                name, min(d["temperature_2m_min"]), max(d["temperature_2m_max"]), SKY.get(common, "mixed")))
            return
        if day:
            self.say("Tomorrow in %s: %s, %d to %d degrees, %d percent chance of rain." % (
                name, SKY.get(d["weather_code"][1], "mixed"), d["temperature_2m_min"][1], d["temperature_2m_max"][1],
                d["precipitation_probability_max"][1] or 0))
            return
        c = w["current"]
        self.say("In %s it's %d degrees and %s. Today: %d to %d, %d percent chance of rain." % (
            name, round(c["temperature_2m"]), SKY.get(c["weather_code"], "mixed"), d["temperature_2m_min"][0],
            d["temperature_2m_max"][0], d["precipitation_probability_max"][0] or 0))

    def will_it(self, m):
        what, place, when, place2 = m.groups()
        got = self.forecast(self._where(place, place2))
        if not got:
            return
        where, w = got
        name, day, d = where["name"], self._day(when), w["daily"]
        rain, lo, hi = d["precipitation_probability_max"][day] or 0, d["temperature_2m_min"][day], \
            d["temperature_2m_max"][day]
        sky = SKY.get(d["weather_code"][day], "mixed")
        on = "tomorrow" if day else "today"
        what = what.lower()
        if re.search(r"rain|umbrella|raincoat", what):
            lead = "Yes, likely" if rain >= 60 else "Maybe" if rain >= 30 else "Probably not"
            self.say("%s: %d percent chance of rain %s in %s." % (lead, rain, on, name))
        elif re.search(r"jacket|coat|sweater|cold", what):
            lead = "Yes, it gets cold" if lo < 12 else "Maybe in the evening" if lo < 18 else "Probably not"
            self.say("%s: %d to %d degrees %s in %s." % (lead, lo, hi, on, name))
        else:
            self.say("%s in %s: %s, %d to %d degrees, %d percent chance of rain." % (on.capitalize(), name, sky, lo,
                                                                                     hi, rain))

    def loose(self, m):
        text = m.group(0)
        when = re.search(r"\b(tomorrow|this week|this weekend|weekend|week|monday|tuesday|wednesday|thursday|friday|"
                         r"saturday|sunday)\b", text, re.I)
        when = when and ("tomorrow" if when.group(1).lower() == "tomorrow" else "this week")
        place = re.search(r"\b(?:in|at|for) (?!the (?:morning|evening|afternoon|night|week|weekend)\b)"
                          r"(?!(?:my|this|the next|next|a|an|few)\b)([a-z][a-z .]*?)"
                          r"(?: (?:today|tonight|tomorrow|this week|this weekend|right now|now|later|on\b.*))?$", text, re.I)
        place = place and place.group(1)
        if re.search(r"\b(?:rain|raining|rainy|umbrella|raincoat|raingear)\b", text, re.I) and when != "this week":
            return self.will_it(_Groups("rain", place, when, None))
        return self.weather(_Groups(None, place, when, None))

    def how_hot(self, m):
        _, when, place, when2 = m.groups()
        got = self.forecast(self._where(place))
        if not got:
            return
        where, w = got
        if self._day(when or when2):
            d = w["daily"]
            self.say("Tomorrow in %s: %d to %d degrees." % (where["name"], d["temperature_2m_min"][1],
                                                          d["temperature_2m_max"][1]))
            return
        c = w["current"]
        self.say("It's %d degrees in %s right now, feels like %d." % (round(c["temperature_2m"]), where["name"],
                                                                     round(c["apparent_temperature"])))
