"""
Build the hand-built NWS bodies for downtown Los Angeles under a heat watch
(d091546 §2.4 C1, D-09-25-76).

    python scripts/build_nws_la_heat_fixtures.py          # writes the bodies
    python scripts/build_nws_la_heat_fixtures.py --check  # exit 1 if one is stale

api.weather.gov is refused by the build session's egress policy (as it was on
2026-09-25), so no live body could be recorded. These follow the published
schema and carry what d091537's STOP-W saw on production on 2026-10-01: an
Extreme Heat Watch, and daily periods whose icon token is `hot`, `haze` or
`smoke` — tokens deliberately absent from NWS_ICON_TABLE — each with NWS's own
`shortForecast`. The gridpoint and the values are illustrative, not recorded.

    points.la_heat.json            forecast.la_heat.us.json
    stations.la_heat.json          forecastHourly.la_heat.us.json
    latest.la_heat.json            alerts.la_heat.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "nws"
PDT = timezone(timedelta(hours=-7))
LAT, LON = 34.052, -118.244
GRID = ("LOX", 155, 45)
STATION = "KCQT"
PROVENANCE = (
    "Hand-built to the api.weather.gov response schema for downtown Los Angeles "
    f"({LAT},{LON}), gridpoint {GRID[0]}/{GRID[1]},{GRID[2]} illustrative, not recorded: "
    "api.weather.gov was unreachable from the build session (egress policy). Generated "
    "by scripts/build_nws_la_heat_fixtures.py for d091546 (condition_text); see "
    "tests/fixtures/nws/README.md.")
GRID_URL = f"https://api.weather.gov/gridpoints/{GRID[0]}/{GRID[1]},{GRID[2]}"
ICON = "https://api.weather.gov/icons/land/{dn}/{token}?size={size}"
UPDATED = "2026-10-01T18:12:44+00:00"
GENERATED = "2026-10-01T19:58:03+00:00"

# (name, isDaytime, °F, icon token, shortForecast, wind, dir, PoP)
DAILY = [
    ("This Afternoon", True, 101, "hot", "Hot", "5 to 10 mph", "SW", None),
    ("Tonight", False, 72, "few", "Mostly Clear", "0 to 5 mph", "SW", None),
    ("Friday", True, 103, "hot", "Hot", "5 to 10 mph", "WSW", None),
    ("Friday Night", False, 73, "few", "Mostly Clear", "0 to 5 mph", "SW", None),
    ("Saturday", True, 102, "hot", "Hot", "5 to 10 mph", "WSW", None),
    ("Saturday Night", False, 71, "haze", "Haze", "0 to 5 mph", "SW", None),
    ("Sunday", True, 98, "hot", "Hot", "5 to 10 mph", "SW", None),
    ("Sunday Night", False, 68, "skc", "Clear", "0 to 5 mph", "SW", None),
    ("Monday", True, 92, "haze", "Haze", "5 mph", "SW", None),
    ("Monday Night", False, 66, "smoke", "Areas Of Smoke", "0 to 5 mph", "SW", None),
    ("Tuesday", True, 88, "smoke", "Areas Of Smoke", "5 mph", "SW", None),
    ("Tuesday Night", False, 64, "few", "Mostly Clear", "0 to 5 mph", "SW", None),
    ("Wednesday", True, 85, "skc/rain_showers,20", "Sunny then Slight Chance Showers",
     "5 to 10 mph", "W", 20),
    ("Wednesday Night", False, 63, "rain_showers,20", "Slight Chance Showers",
     "5 mph", "W", 20),
]


def _daily_periods() -> list[dict]:
    out = []
    start = datetime(2026, 10, 1, 13, tzinfo=PDT)
    for n, (name, day, f, token, short, wind, wdir, pop) in enumerate(DAILY, 1):
        end = (start.replace(hour=18) if day else
               (start + timedelta(days=1)).replace(hour=6))
        out.append({
            "number": n, "name": name, "startTime": start.isoformat(),
            "endTime": end.isoformat(), "isDaytime": day, "temperature": f,
            "temperatureUnit": "F", "temperatureTrend": "",
            "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": pop},
            "windSpeed": wind, "windDirection": wdir,
            "icon": ICON.format(dn="day" if day else "night", token=token, size="medium"),
            "shortForecast": short, "detailedForecast": "",
        })
        start = end
    return out


def _hourly_periods() -> list[dict]:
    """60 hours from 13:00 PDT: a diurnal curve peaking ~103 °F at 15:00, `hot`
    from 95 °F in daylight, `few`/`skc` otherwise."""
    out = []
    start = datetime(2026, 10, 1, 13, tzinfo=PDT)
    for k in range(60):
        t = start + timedelta(hours=k)
        h = t.hour
        day = 7 <= h < 19
        # 70 °F at 05:00 rising to 103 °F at 15:00, then falling back.
        if 5 <= h <= 15:
            f = 70 + round(33 * (h - 5) / 10)
        else:
            f = 103 - round(33 * ((h - 15) % 24) / 14)
        if day and f >= 95:
            token, short = "hot", "Hot"
        elif day:
            token, short = "skc", "Sunny"
        else:
            token, short = "few", "Mostly Clear"
        out.append({
            "number": k + 1, "name": "", "startTime": t.isoformat(),
            "endTime": (t + timedelta(hours=1)).isoformat(), "isDaytime": day,
            "temperature": f, "temperatureUnit": "F", "temperatureTrend": "",
            "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": 0},
            "dewpoint": {"unitCode": "wmoUnit:degC", "value": 8.3},
            "relativeHumidity": {"unitCode": "wmoUnit:percent",
                                 "value": 18 if f >= 95 else 35},
            "windSpeed": "6 mph", "windDirection": "SW",
            "icon": ICON.format(dn="day" if day else "night", token=token, size="small"),
            "shortForecast": short, "detailedForecast": "",
        })
    return out


def _qv(unit, value, qc="V"):
    return {"unitCode": f"wmoUnit:{unit}", "value": value, "qualityControl": qc}


def bodies() -> dict[str, dict]:
    grid_id, gx, gy = GRID
    return {
        "points.la_heat.json": {
            "_provenance": PROVENANCE, "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [LON, LAT]},
            "properties": {
                "@id": f"https://api.weather.gov/points/{LAT},{LON}",
                "cwa": grid_id, "gridId": grid_id, "gridX": gx, "gridY": gy,
                "forecast": f"{GRID_URL}/forecast",
                "forecastHourly": f"{GRID_URL}/forecast/hourly",
                "forecastGridData": GRID_URL,
                "observationStations": f"{GRID_URL}/stations",
                "timeZone": "America/Los_Angeles", "radarStation": "KVTX"}},
        "stations.la_heat.json": {
            "_provenance": PROVENANCE, "type": "FeatureCollection",
            "features": [{"id": f"https://api.weather.gov/stations/{STATION}",
                          "type": "Feature",
                          "properties": {"stationIdentifier": STATION,
                                         "name": "Los Angeles - Downtown/USC",
                                         "timeZone": "America/Los_Angeles"}}]},
        "forecast.la_heat.us.json": {
            "_provenance": PROVENANCE, "type": "Feature",
            "properties": {"units": "us", "forecastGenerator": "BaselineForecastGenerator",
                           "generatedAt": GENERATED, "updateTime": UPDATED,
                           "periods": _daily_periods()}},
        "forecastHourly.la_heat.us.json": {
            "_provenance": PROVENANCE, "type": "Feature",
            "properties": {"units": "us", "forecastGenerator": "HourlyForecastGenerator",
                           "generatedAt": GENERATED, "updateTime": UPDATED,
                           "validTimes": "2026-10-01T20:00:00+00:00/P2DT12H",
                           "periods": _hourly_periods()}},
        "latest.la_heat.json": {
            "_provenance": PROVENANCE,
            "id": f"https://api.weather.gov/stations/{STATION}/observations/"
                  "2026-10-01T19:47:00+00:00",
            "type": "Feature",
            "properties": {
                "station": f"https://api.weather.gov/stations/{STATION}",
                "timestamp": "2026-10-01T19:47:00+00:00",
                "textDescription": "Clear",
                "icon": ICON.format(dn="day", token="hot", size="medium"),
                "temperature": _qv("degC", 38.3), "dewpoint": _qv("degC", 7.8),
                "windDirection": _qv("degree_(angle)", 230),
                "windSpeed": _qv("km_h-1", 9.4), "windGust": _qv("km_h-1", None, "Z"),
                "barometricPressure": _qv("Pa", 101080),
                "seaLevelPressure": _qv("Pa", 101110),
                "relativeHumidity": _qv("percent", 16.2),
                "windChill": _qv("degC", None), "heatIndex": _qv("degC", 36.9),
                "cloudLayers": [{"base": {"unitCode": "wmoUnit:m", "value": None},
                                 "amount": "CLR"}]}},
        "alerts.la_heat.json": {
            "_provenance": PROVENANCE, "type": "FeatureCollection",
            "title": f"Current watches, warnings, and advisories for {LAT} N, {-LON} W",
            "features": [{
                "id": "https://api.weather.gov/alerts/urn:oid:2.49.0.1.840.0.fixture.heat.1",
                "type": "Feature",
                "properties": {
                    "id": "urn:oid:2.49.0.1.840.0.fixture.heat.1",
                    "event": "Extreme Heat Watch", "severity": "Severe",
                    "headline": "Extreme Heat Watch issued October 1 at 3:05AM PDT until "
                                "October 4 at 8:00PM PDT by NWS Los Angeles/Oxnard CA",
                    "onset": "2026-10-02T10:00:00-07:00",
                    "ends": "2026-10-04T20:00:00-07:00"}}]},
    }


def _dump(body: dict) -> str:
    return json.dumps(body, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    stale = []
    for name, body in bodies().items():
        path = FIX / name
        text = _dump(body)
        if "--check" in argv:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
    if stale:
        print("stale:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
