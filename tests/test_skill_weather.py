"""Weather and the time anywhere (skills/weather.py). Open-Meteo and `date` are faked: nothing leaves."""
import re

import pytest

PLACES = {"bengaluru": ("Bengaluru", "India", 12.97, 77.59, "Asia/Kolkata"),
          "delhi": ("Delhi", "India", 28.65, 77.23, "Asia/Kolkata"),
          "orange tx": None}
OFFSETS = {"Asia/Kolkata": ("+0530", "5:30 PM on Friday"), "Asia/Tokyo": ("+0900", "9:00 PM on Friday"),
           "Europe/London": ("+0000", "12:00 PM on Friday"), "Africa/Cairo": ("+0200", "2:00 PM on Friday")}


def _geocode(url, data):
    name = re.search(r"name=([^&]+)", url).group(1).replace("+", " ")
    hit = PLACES.get(name)
    return {"results": [dict(zip(("name", "country", "latitude", "longitude", "timezone"), hit))] if hit else []}


def _forecast(url, data):
    return {"current": {"temperature_2m": 27.6, "apparent_temperature": 29.2, "weather_code": 2},
            "daily": {"time": ["2026-10-30", "2026-10-31", "2026-11-01"], "weather_code": [2, 61, 3],
                      "temperature_2m_min": [18, 19, 20], "temperature_2m_max": [30, 27, 29],
                      "precipitation_probability_max": [10, 80, 40]}}


def _date(line):
    zone, fmt = re.search(r"TZ=(\S+) date (.+)", line).groups()
    offset, said = OFFSETS[zone]
    return offset if "%z" in fmt else said


@pytest.fixture
def sky(jv):
    jv.net.on(r"geocoding-api\.open-meteo\.com", _geocode).on(r"api\.open-meteo\.com/v1/forecast", _forecast)
    jv.shell.on(r"TZ=\S+ date", _date)
    return jv


def test_no_place_is_your_city(sky):
    sky.do("what's the weather")
    assert sky.says("In Bengaluru it's 28 degrees and partly cloudy", "10 percent chance of rain")
    assert sky.net.opened(r"name=bengaluru") and not sky.claude.calls


def test_a_city_and_tomorrow(sky):
    sky.do("what's the weather in delhi tomorrow")
    assert sky.says("Tomorrow in Delhi: light rain, 19 to 27 degrees, 80 percent")


def test_rain_is_answered_yes_or_no(sky):
    sky.do("will it rain today")
    assert sky.says("Probably not: 10 percent chance of rain today in Bengaluru")
    sky.do("do i need an umbrella tomorrow")
    assert sky.says("Yes, likely: 80 percent chance of rain tomorrow")


def test_a_place_is_looked_up_once(sky):
    sky.do("weather in delhi")
    sky.do("weather in delhi")
    assert len(sky.net.opened(r"geocoding")) == 1


def test_this_week_goes_on_a_card(sky):
    sky.do("what does the weather look like this week")
    assert sky.says("This week in Bengaluru: 18 to 30 degrees")
    assert "2026-10-31" in sky.card_text()


def test_unknown_place_and_no_internet_are_said(sky):
    sky.do("what's the weather in orange tx")
    assert sky.says("couldn't find a place called orange tx")
    sky.net.on(r"open-meteo", raises=OSError("network is unreachable"))
    sky.do("what's the weather in paris")
    assert sky.says("couldn't reach the weather service") and not sky.claude.calls


def test_the_time_needs_no_internet(sky):
    sky.do("what time is it in tokyo")
    assert sky.says("It's 9:00 PM on Friday in Tokyo, 3 and a half hours ahead of you")
    sky.do("the time in cairo please")
    assert sky.says("in Cairo, 3 and a half hours behind you")
    assert not sky.net.calls


def test_time_difference(sky):
    sky.do("time difference between here and london")
    assert sky.says("London is 5 and a half hours behind Bengaluru")


@pytest.mark.parametrize("said", ["what's the temperature", "how hot is it", "what's the temperature of the cpu"])
def test_the_computers_temperature_is_left_alone(sky, said):
    sky.do(said)
    assert not sky.net.calls and not sky.says("degrees in")
