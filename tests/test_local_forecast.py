"""
Tests for GET /api/local/forecast (d091477) — Local Weather lane A: two arms,
one shape, every card labelled.

NO NETWORK, EVER. NWS is answered by `FakeNws` over the fixtures in
`tests/fixtures/nws/` (gridpoint LOX/154,44 — hand-built to the published
schema, see that directory's README). The model arm is answered by
`FakeGlobalBank`, an in-memory `global` sidecar set shaped like the one
`test_weather_point.py`'s `ladder_fake` stands in for — same (key, Range)
transport surface, same refusal of a value read without a Range header — but
on the 721 x 1440 global grid and with weather-shaped values, because the
na3/mslp synthetic array there cannot stand in for a temperature.

T1..T10 are the spec's §3 table; each test's name carries its number.
"""

import hashlib
import json
import logging
import math
import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import local_forecast as lf
import main
import model_arm
import nws_arm
import weather_point as wp

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent
NWS_FIX = Path(__file__).parent / "fixtures" / "nws"
RAW_OUTLINE = str(Path(__file__).parent / "fixtures" / "us_outline_raw.geojson")

NOW = datetime(2026, 9, 25, 21, 10, tzinfo=UTC)
RUN = datetime(2026, 9, 25, 12, tzinfo=UTC)

LAX = {"lat": 33.94, "lon": -118.41}
VANCOUVER = {"lat": 49.28, "lon": -123.12}


# ---------------------------------------------------------------------------
# The NWS fake
# ---------------------------------------------------------------------------

def _fixture(name):
    return json.loads((NWS_FIX / name).read_text())


class FakeNws:
    """(url, params) -> (status, body) over the fixtures. `fail[kind]` is an
    HTTP status or an exception instance served for that kind."""

    ROUTES = (("points", re.compile(r"/points/")),
              ("hourly", re.compile(r"/forecast/hourly$")),
              ("forecast", re.compile(r"/forecast$")),
              ("stations", re.compile(r"/gridpoints/[^/]+/[^/]+/stations$")),
              ("obs", re.compile(r"/observations/latest$")),
              ("alerts", re.compile(r"/alerts/active$")))
    FILES = {"points": "points.json", "hourly": "forecastHourly.json",
             "forecast": "forecast.json", "stations": "stations.json",
             "obs": "latest.json", "alerts": "alerts.json"}

    def __init__(self, **fail):
        self.fail = fail
        self.calls = []
        self.override = {}

    def count(self, kind):
        return sum(1 for k, _, _ in self.calls if k == kind)

    async def __call__(self, url, params):
        kind = next(k for k, rx in self.ROUTES if rx.search(url))
        self.calls.append((kind, url, params))
        f = self.fail.get(kind)
        if isinstance(f, Exception):
            raise f
        if isinstance(f, int):
            return f, {"title": "fixture failure", "status": f}
        if kind in self.override:
            return 200, self.override[kind]
        return 200, _fixture(self.FILES[kind])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------------------
# The model arm's bank fake — the global sidecars
# ---------------------------------------------------------------------------

GLOBAL_HEADER = {"shape": [721, 1440], "lat0": -90.0, "lon0": -180.0, "dlat": 0.25,
                 "dlon": 0.25, "lat_order": "ascending",
                 "lon_convention": "west_negative_monotonic", "dtype": "float32",
                 "endianness": "little", "model": "gfs", "crop": "global",
                 "sha256": "0" * 64}
UNITS = {"t2m": "K", "wind10m": "m s-1", "mslp": "Pa", "dswrf": "W m-2"}


def bank_value(param, fhr):
    """Weather-shaped, and linear in fhr so interpolation is checkable."""
    return {"t2m": 283.15 + 0.1 * fhr, "wind10m": 5.0 + fhr / 60.0,
            "mslp": 101300.0 - 10.0 * fhr, "dswrf": 200.0}[param]


class FakeGlobalBank:
    def __init__(self, run=RUN, absent_params=(), absent_keys=()):
        self.run = run
        self.absent_params = set(absent_params)
        self.absent_keys = set(absent_keys)
        self.calls = []
        self.range_widths = []

    def _parse(self, key):
        m = re.match(r"weather/values/gfs/global/(\d{8})/(\d{2})Z/([a-z0-9_]+)_f(\d{3})\.(json|f32)$", key)
        if not m:
            return None
        run = datetime.strptime(m[1] + m[2], "%Y%m%d%H").replace(tzinfo=UTC)
        return run, m[3], int(m[4]), m[5]

    def present(self, key):
        p = self._parse(key)
        if p is None or p[0] != self.run or key in self.absent_keys:
            return False
        run, param, fhr, _ = p
        if param in self.absent_params or param not in UNITS:
            return False
        return not (param == "dswrf" and fhr == 0)     # absent at analysis by design

    async def __call__(self, key, byte_range):
        self.calls.append((key, byte_range))
        if not self.present(key):
            return 404, b""
        _, param, fhr, ext = self._parse(key)
        if ext == "json":
            return 200, json.dumps({**GLOBAL_HEADER, "param": param,
                                    "units": UNITS[param]}).encode()
        assert byte_range is not None, "a value read with no Range header"
        start, end = (int(x) for x in byte_range.split("=")[1].split("-"))
        self.range_widths.append(end - start + 1)
        return 206, struct.pack("<f", bank_value(param, fhr))


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

@pytest.fixture
def world(monkeypatch):
    nws = FakeNws()
    clock = Clock()
    bank = FakeGlobalBank()
    state = {"nws": nws, "clock": clock, "bank": bank, "runs": [RUN]}

    def install():
        client = nws_arm.NwsClient(transport=state["nws"], clock=state["clock"])
        store = wp.SidecarStore(transport=state["bank"])
        monkeypatch.setattr(main, "_local_nws_client", client)
        monkeypatch.setattr(main, "_get_weather_store", lambda: store)
        state["client"] = client

    async def runs():
        return list(state["runs"])

    monkeypatch.setattr(main, "_local_gfs_run_candidates", runs)
    monkeypatch.setattr(lf, "utcnow", lambda: state.get("now", NOW))
    model_arm._run_memo.clear()
    state["install"] = install
    install()
    state["http"] = TestClient(main.app)
    return state


def get(world, **params):
    return world["http"].get("/api/local/forecast", params=params)


# ═══════════════════════════════════════════════════════════════════════════
# T1 — select_arm, and the buffer is what admits the offshore point
# ═══════════════════════════════════════════════════════════════════════════

# 15 km from the Santa Monica pier (34.009, -118.497) on a bearing of 225°.
SANTA_MONICA_OFFSHORE = (33.915, -118.615)
# ~31 km south of the border at Tijuana (32.53N), inland Baja.
MEXICO_30KM = (32.25, -116.95)


@pytest.mark.parametrize("name,lat,lon,arm", [
    ("Los Angeles", 34.05, -118.24, "nws"),
    ("15 km off Santa Monica", *SANTA_MONICA_OFFSHORE, "nws"),
    ("Vancouver BC", 49.28, -123.12, "model"),
    ("Honolulu", 21.31, -157.86, "nws"),
    ("San Juan PR", 18.47, -66.11, "nws"),
    ("30 km into Mexico from San Diego", *MEXICO_30KM, "model"),
])
def test_T1_select_arm(name, lat, lon, arm):
    assert lf.select_arm(lat, lon) == arm, name


def test_T1_the_offshore_point_is_the_buffers_doing():
    """Without this, T1's Santa Monica row could pass on a coastline that
    happened to reach it. The raw (unbuffered) outline must NOT contain it."""
    assert not lf.in_outline(*SANTA_MONICA_OFFSHORE, path=RAW_OUTLINE)
    assert lf.in_outline(34.05, -118.24, path=RAW_OUTLINE)


def test_T1_outline_is_bundled_small_and_its_sha_is_in_the_receipt(world):
    raw = lf.OUTLINE_PATH.read_bytes()
    assert len(raw) <= 200 * 1024
    props = json.loads(raw)["features"][0]["properties"]
    assert props["buffer_m"] == 20000.0 and "Natural Earth" in props["source"]
    body = get(world, **VANCOUVER).json()
    assert body["receipts"]["outline_sha"] == hashlib.sha256(raw).hexdigest()


def test_T1_aleutians_across_the_antimeridian():
    assert lf.select_arm(52.9, 173.2) == "nws"      # Attu, east of 180
    assert lf.select_arm(51.88, -176.65) == "nws"   # Adak


# ═══════════════════════════════════════════════════════════════════════════
# T2 — the US arm: shape, rows, pairing, units, now
# ═══════════════════════════════════════════════════════════════════════════

TOP = ["place", "now", "hourly", "daily", "alerts", "sun", "receipts"]


def test_T2_us_payload_key_order_is_exact(world):
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert list(b) == TOP
    assert list(b["place"]) == list(lf.PLACE_KEYS)
    assert list(b["now"]) == list(lf.NOW_KEYS)
    assert list(b["now"]["wind"]) == list(lf.WIND_KEYS)
    assert all(list(h) == list(lf.HOURLY_KEYS) for h in b["hourly"])
    assert all(list(d) == list(lf.DAILY_KEYS) for d in b["daily"])
    assert all(list(a) == list(lf.ALERT_KEYS) for a in b["alerts"])
    assert list(b["sun"]) == list(lf.SUN_KEYS)
    assert list(b["receipts"]) == list(lf.RECEIPT_KEYS)
    assert list(b["receipts"]["memo"]) == list(lf.MEMO_KEYS)
    assert b["receipts"]["arm"] == "nws"
    assert b["receipts"]["source"] == "nws · gridpoint LOX/154,44 · issued 2026-09-25T18:31Z"
    assert b["place"] == {"lat": 33.94, "lon": -118.41, "tz": "America/Los_Angeles",
                          "tz_source": "nws", "country": "US"}


def test_T2_48_hourly_rows_and_daily_paired_day_night(world):
    b = get(world, **LAX).json()
    assert len(b["hourly"]) == 48
    assert b["hourly"][0]["valid"] == "2026-09-25T21:00:00Z"
    # 14 NWS periods starting on a day → 7 paired rows (NOT 10: see handback).
    assert len(b["daily"]) == 7 <= 10
    his_f = [77, 79, 81, 84, 80, 75, 73]
    los_f = [63, 64, 66, 65, 62, 61, 60]
    for row, hf, lf_ in zip(b["daily"], his_f, los_f):
        assert row["hi"] == round((hf - 32) * 5 / 9, 1)
        assert row["lo"] == round((lf_ - 32) * 5 / 9, 1)
        assert row["hi"] > row["lo"]
    assert b["daily"][0]["date"] == "2026-09-25"
    assert b["daily"][6]["date"] == "2026-10-01"


def test_T2_si_units_a_known_fahrenheit_arrives_as_celsius(world):
    b = get(world, **LAX).json()
    assert b["daily"][0]["hi"] == 25.0            # 77 °F
    assert b["daily"][0]["lo"] == 17.2            # 63 °F
    h0 = b["hourly"][0]
    assert h0["wind"]["speed"] == round(9 / 3.6, 1)          # "9 km/h" → m/s
    assert (h0["wind"]["dir_txt"], h0["wind"]["dir_deg"]) == ("W", 270.0)
    assert b["daily"][0]["wind"]["speed"] == round(10 * 0.44704, 1)   # "5 to 10 mph"
    assert b["now"]["mslp"] == 1013.2             # 101320 Pa
    assert b["now"]["wind"]["speed"] == 4.1       # 14.8 km/h


def test_T2_now_names_the_station_and_the_observation_time(world):
    now = get(world, **LAX).json()["now"]
    assert now["source"] == "nws · KLAX · observed 20:53Z"
    assert now["valid"] == "2026-09-25T20:53:00Z"
    assert now["age_min"] == 17
    assert (now["t"], now["dewpoint"], now["rh"]) == (21.1, 12.0, 56)
    assert (now["condition"], now["condition_raw"], now["sky"]) == ("mostly-clear", "few", 0.1875)
    assert now["feels"] is None and any(a.startswith("feels:") for a in now["absent"])


def test_T2_leading_night_fills_lo_and_states_why_hi_is_null():
    periods = _fixture("forecast.json")["properties"]["periods"][1:]   # starts "Tonight"
    rows = nws_arm.daily_rows(periods, "America/Los_Angeles", 33.94, -118.41, "src")
    assert rows[0]["hi"] is None and rows[0]["lo"] == 17.2
    assert "hi: day period elapsed" in rows[0]["absent"]
    assert rows[1]["hi"] == round((79 - 32) * 5 / 9, 1)


def test_T2_nws_qc_null_temperature_still_renders(world):
    obs = _fixture("latest.json")
    obs["properties"]["temperature"]["value"] = None
    world["nws"].override["obs"] = obs
    r = get(world, **LAX)
    assert r.status_code == 200
    now = r.json()["now"]
    assert now["t"] is None and "t: nws qc" in now["absent"]


def test_T2_alerts_carry_the_six_fields(world):
    a = get(world, **LAX).json()["alerts"]
    assert a == [{"id": "urn:oid:2.49.0.1.840.0.fixture.001.1",
                  "event": "Beach Hazards Statement", "severity": "Moderate",
                  "headline": a[0]["headline"], "onset": "2026-09-25T18:14:00Z",
                  "ends": "2026-09-28T03:00:00Z"}]


def test_T2_every_null_in_a_payload_has_its_reason(world):
    for params in (LAX, VANCOUVER):
        b = get(world, **params).json()
        for where, row, kind in ([("now", b["now"], "now"), ("sun", b["sun"], "sun")]
                                 + [(f"hourly[{i}]", h, "hourly") for i, h in enumerate(b["hourly"])]
                                 + [(f"daily[{i}]", d, "daily") for i, d in enumerate(b["daily"])]):
            named = {a.split(":", 1)[0] for a in row["absent"]}
            for k in lf._REASONED[kind]:
                if row[k] is None:
                    assert k in named, f"{where}.{k}"
            for k, v in (row.get("wind") or {}).items():
                if v is None:
                    assert f"wind.{k}" in named, f"{where}.wind.{k}"


def test_T2_build_payload_refuses_an_unexplained_null_and_a_decimal():
    from decimal import Decimal
    parts = _nws_parts()
    parts["now"]["absent"] = [a for a in parts["now"]["absent"] if not a.startswith("feels:")]
    with pytest.raises(ValueError, match="now.feels is null"):
        lf.build_payload(**parts)
    parts = _nws_parts()
    parts["now"]["t"] = Decimal("21.1")
    with pytest.raises(TypeError, match="Decimal"):
        lf.build_payload(**parts)


def _nws_parts():
    bundle = {"tz": "America/Los_Angeles", "grid": "LOX/154,44", "station": "KLAX",
              "hourly": _fixture("forecastHourly.json"),
              "forecast": _fixture("forecast.json"), "obs": _fixture("latest.json"),
              "alerts": _fixture("alerts.json"),
              "memo": {"points": "miss", "forecast": "miss", "obs": "miss", "alerts": "miss"}}
    return nws_arm.build(bundle, 33.94, -118.41, NOW, [])


# ═══════════════════════════════════════════════════════════════════════════
# T3 — the icon table: every token to one of 12 words; unknown refused loudly
# ═══════════════════════════════════════════════════════════════════════════

def test_T3_vocabulary_is_twelve_words():
    assert len(lf.VOCAB) == 12 == len(set(lf.VOCAB))


@pytest.mark.parametrize("token", sorted(lf.NWS_ICON_TABLE))
def test_T3_every_known_token_maps_to_one_house_word(token):
    word = lf.condition_from_token(token)
    assert word in lf.VOCAB
    url = f"https://api.weather.gov/icons/land/day/{token},30?size=medium"
    assert lf.icon_token(url) == token


def test_T3_unknown_token_is_unknown_kept_and_logged(world, caplog):
    hourly = _fixture("forecastHourly.json")
    hourly["properties"]["periods"][0]["icon"] = \
        "https://api.weather.gov/icons/land/day/smoke?size=small"
    world["nws"].override["hourly"] = hourly
    with caplog.at_level(logging.WARNING, logger="energylake.local"):
        h0 = get(world, **LAX).json()["hourly"][0]
    assert h0["condition"] == "unknown"
    assert h0["condition_raw"] == "smoke"
    assert any("[[LOCAL_UNKNOWN_ICON]]" in r.getMessage() and "smoke" in r.getMessage()
               for r in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════
# T4 — the model arm on the bank fake
# ═══════════════════════════════════════════════════════════════════════════

def test_T4_model_arm_48_rows_interp_flags_and_absent(world):
    r = get(world, **VANCOUVER)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "model"
    hourly = b["hourly"]
    assert len(hourly) == 48
    for h, row in enumerate(hourly):
        assert row["interp"] is (h % 6 != 0), h
        assert row["valid"] == lf.iso_z(RUN + timedelta(hours=h))
        for f in ("pop", "precip_amt", "dewpoint", "rh"):
            assert row[f] is None
            assert f"{f}: not banked" in row["absent"], (h, f)
        assert row["t_spread"] is None and "t_spread: gefs not banked" in row["absent"]
    # linear between rungs: t2m is 283.15 + 0.1*fhr K
    assert hourly[6]["t"] == round(283.15 + 0.6 - 273.15, 1)
    assert hourly[3]["t"] == round((hourly[0]["t"] + hourly[6]["t"]) / 2, 1)
    assert hourly[3]["source"] == "model · GFS 12Z f000–f006 interp"
    assert hourly[6]["source"] == "model · GFS 12Z f006"


def test_T4_daily_rows_at_most_ten_and_fhr_range_printed(world):
    b = get(world, **VANCOUVER).json()
    assert 0 < len(b["daily"]) <= 10
    assert b["receipts"]["fhr_range"] == [0, 240]
    assert b["receipts"]["run"] == "2026-09-25T12Z"
    assert b["receipts"]["source"] == "model · GFS 2026-09-25 12Z · global · f000–f240"
    d0 = b["daily"][0]
    assert d0["date"] == "2026-09-26"                 # today began before the run
    assert d0["pop"] is None and "pop: not banked" in d0["absent"]
    assert d0["hi"] > d0["lo"]
    assert b["place"]["tz"] == "Etc/GMT+8" and b["place"]["tz_source"] == "nominal"
    assert b["place"]["country"] is None


def test_T4_reads_are_four_bytes_and_only_the_global_crop(world):
    get(world, **VANCOUVER)
    bank = world["bank"]
    assert set(bank.range_widths) == {4}
    assert all("/global/" in k for k, _ in bank.calls)
    assert not any("/na3/" in k for k, _ in bank.calls)


def test_T4_sky_from_dswrf_against_clearsky_and_night_is_null(world):
    b = get(world, **VANCOUVER).json()
    for h, row in enumerate(b["hourly"]):
        valid = RUN + timedelta(hours=h)
        if lf.solar_elevation(49.28, -123.12, valid) <= 0:
            assert row["sky"] is None and "sky: night" in row["absent"]
            assert row["condition"] == "unknown"
    # f000 (05:00 PDT at Vancouver) is dark; f006 (11:00 PDT) is day.
    f6 = b["hourly"][6]
    cs = lf.clearsky_mean(49.28, -123.12, RUN, RUN + timedelta(hours=6))
    assert f6["sky"] == round(min(1, max(0, 1 - 200.0 / cs)), 2)
    assert f6["condition"] == lf.condition_from_sky(f6["sky"])


def test_T4_haurwitz_is_the_named_formula():
    t = datetime(2026, 6, 21, 19, 10, tzinfo=UTC)      # ~solar noon at LAX
    cz = math.sin(math.radians(lf.solar_elevation(33.94, -118.41, t)))
    assert lf.clearsky_ghi(33.94, -118.41, t) == pytest.approx(1098 * cz * math.exp(-0.057 / cz))
    assert lf.clearsky_ghi(33.94, -118.41, datetime(2026, 6, 21, 8, tzinfo=UTC)) == 0.0


def test_T4_stop_b_a_param_missing_on_the_run_is_named(world):
    world["bank"].absent_params = {"mslp"}
    b = get(world, **VANCOUVER).json()
    assert all(r["mslp"] is None and "mslp: not banked on run" in r["absent"]
               for r in b["hourly"])
    assert b["now"]["mslp"] is None
    assert any("not banked on run: weather/values/gfs/global/20260925/12Z/mslp_f000.json" == n
               for n in b["receipts"]["notes"])


def test_T4_run_discovery_skips_a_run_without_global_t2m(world):
    world["runs"] = [datetime(2026, 9, 25, 18, tzinfo=UTC), RUN]
    b = get(world, **VANCOUVER).json()
    assert b["receipts"]["run"] == "2026-09-25T12Z"


def test_T4_no_run_at_all_is_503_with_no_cache_header(world):
    world["runs"] = [datetime(2026, 9, 25, 18, tzinfo=UTC)]
    r = get(world, **VANCOUVER)
    assert r.status_code == 503
    assert "cache-control" not in r.headers and "etag" not in r.headers
    assert r.json()["detail"]["probed"] == [
        "weather/values/gfs/global/20260925/18Z/t2m_f000.json"]


# ═══════════════════════════════════════════════════════════════════════════
# T5 — D-09-24-09: the model arm is always labelled
# ═══════════════════════════════════════════════════════════════════════════

def test_T5_model_now_source_is_labelled_and_observed_appears_nowhere(world):
    r = get(world, **VANCOUVER)
    b = r.json()
    src = b["now"]["source"]
    assert src.startswith("model ·")
    assert "12Z" in src and "f000" in src
    assert src == "model · GFS 12Z f000"
    assert b["now"]["valid"] == "2026-09-25T12:00:00Z"
    assert b["now"]["age_min"] == 9 * 60 + 10
    assert "observed" not in r.text.lower()
    assert all(row["source"].startswith("model · ") for row in b["hourly"] + b["daily"])


def test_T5_forced_model_arm_in_the_us_is_labelled_too(world):
    r = get(world, **LAX, arm="model")
    b = r.json()
    assert b["receipts"]["arm"] == "model" and b["receipts"]["fallback"] is None
    assert b["now"]["source"].startswith("model ·")
    assert b["place"]["country"] == "US"
    assert "observed" not in r.text.lower()
    assert world["nws"].calls == []                  # forced model never asks NWS


# ═══════════════════════════════════════════════════════════════════════════
# T6 — D-09-25-04: NWS down is not a blank page
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("failure,reason", [
    (503, "HTTP 503"),
    (404, "HTTP 404"),
    (TimeoutError("read timed out"), "TimeoutError"),
])
def test_T6_points_failure_falls_through_to_the_model_arm(world, failure, reason):
    world["nws"] = FakeNws(points=failure)
    world["install"]()
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "model"
    assert b["receipts"]["fallback"] == {"from": "nws", "reason": reason}
    assert b["now"]["source"].startswith("model ·")
    assert r.headers["cache-control"] == "max-age=900"


def test_T6_one_immediate_retry_on_5xx_and_no_more(world):
    world["nws"] = FakeNws(points=503)
    world["install"]()
    get(world, **LAX)
    assert world["nws"].count("points") == 2
    world["nws"] = FakeNws(points=404)
    world["install"]()
    get(world, **LAX)
    assert world["nws"].count("points") == 1


def test_T6_a_later_failure_keeps_the_real_tz(world):
    world["nws"] = FakeNws(hourly=500)
    world["install"]()
    b = get(world, **LAX).json()
    assert b["receipts"]["fallback"] == {"from": "nws", "reason": "HTTP 500"}
    assert (b["place"]["tz"], b["place"]["tz_source"]) == ("America/Los_Angeles", "nws")


def test_T6_malformed_body_falls_through(world):
    world["nws"].override["forecast"] = {"properties": {"periods": "not a list"}}
    b = get(world, **LAX).json()
    assert b["receipts"]["arm"] == "model"
    assert b["receipts"]["fallback"]["from"] == "nws"


def test_T6_blocked_by_nws_is_logged_verbatim(world, caplog):
    world["nws"] = FakeNws(points=403)
    world["install"]()
    with caplog.at_level(logging.WARNING, logger="energylake.local"):
        b = get(world, **LAX).json()
    assert b["receipts"]["fallback"]["reason"] == "HTTP 403"
    assert any("[[LOCAL_NWS_BLOCKED]]" in r.getMessage() for r in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════
# T7 — the memo (D-09-25-03)
# ═══════════════════════════════════════════════════════════════════════════

def test_T7_memo_one_fetch_per_gridpoint_and_alerts_expire_at_two_minutes(world):
    nws, clock = world["nws"], world["clock"]
    b1 = get(world, **LAX).json()
    assert b1["receipts"]["memo"] == {"points": "miss", "forecast": "miss",
                                      "obs": "miss", "alerts": "miss"}
    clock.t += 60
    b2 = get(world, **LAX).json()
    assert nws.count("hourly") == 1 and nws.count("forecast") == 1
    assert b2["receipts"]["memo"] == {"points": "hit", "forecast": "hit",
                                      "obs": "hit", "alerts": "hit"}
    clock.t += 61                                    # 121 s after the first read
    b3 = get(world, **LAX).json()
    assert nws.count("alerts") == 2
    assert b3["receipts"]["memo"]["alerts"] == "miss"
    assert b3["receipts"]["memo"]["forecast"] == "hit"
    assert nws.count("hourly") == 1
    clock.t += 600                                   # past the 10 min TTL
    get(world, **LAX)
    assert nws.count("hourly") == 2


def test_T7_memo_is_keyed_on_the_gridpoint_not_the_click(world):
    get(world, **LAX)
    get(world, lat=33.9401, lon=-118.4102)           # new points read, same gridpoint
    assert world["nws"].count("points") == 2
    assert world["nws"].count("hourly") == 1


def test_T7_every_nws_call_carries_units_si_on_both_forecasts(world):
    get(world, **LAX)
    for kind, _, params in world["nws"].calls:
        if kind in ("hourly", "forecast"):
            assert params == {"units": "si"}


def test_T7_the_real_transport_sends_the_required_headers():
    assert nws_arm.HEADERS == {"User-Agent": "energylake.io (ops@energylake.io)",
                               "Accept": "application/geo+json"}
    assert (nws_arm.CONNECT_TIMEOUT_S, nws_arm.READ_TIMEOUT_S) == (4.0, 8.0)


# ═══════════════════════════════════════════════════════════════════════════
# T8 — headers, 304, 400
# ═══════════════════════════════════════════════════════════════════════════

def test_T8_cache_headers_and_etag_per_arm(world):
    us = get(world, **LAX)
    assert us.headers["cache-control"] == "max-age=300"
    assert us.headers["etag"] == 'W/"nws:2026-09-25T18:31:04Z:KLAX"'
    m = get(world, **VANCOUVER)
    assert m.headers["cache-control"] == "max-age=900"
    assert m.headers["etag"] == 'W/"model:2026-09-25T12:00:00Z:2026-09-25T12Z"'


def test_T8_matching_if_none_match_is_an_empty_304(world):
    tag = get(world, **LAX).headers["etag"]
    r = world["http"].get("/api/local/forecast", params=LAX,
                          headers={"If-None-Match": tag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == tag and r.headers["cache-control"] == "max-age=300"


@pytest.mark.parametrize("params", [
    {"lat": 91, "lon": 0},
    {"lat": 49.28, "lon": -123.12, "arm": "nws"},
    {"lat": 33.94},
    {"lat": "abc", "lon": 0},
    {"lat": 0, "lon": -181},
    {"lat": 0, "lon": 0, "arm": "gfs"},
])
def test_T8_bad_requests_are_400_with_no_cache_header(world, params):
    r = get(world, **params)
    assert r.status_code == 400, r.text
    assert "cache-control" not in r.headers and "etag" not in r.headers
    if "arm" not in params:
        assert r.json()["detail"]["bounds"]["lat"] == [-90, 90]


def test_T8_lon_past_180_is_normalised_and_the_receipt_says_so(world):
    b = get(world, lat=33.94, lon=241.59).json()
    assert b["place"]["lon"] == pytest.approx(-118.41)
    assert b["receipts"]["arm"] == "nws"
    assert b["receipts"]["notes"] == ["lon 241.59 normalised to -118.41"]


# ═══════════════════════════════════════════════════════════════════════════
# T9 — the sun
# ═══════════════════════════════════════════════════════════════════════════

def _z(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def test_T9_sunrise_sunset_within_two_minutes_of_the_pin(world):
    """Pinned from an independent implementation (astral 3.2, zenith 90.833°)
    for LAX on local date 2026-09-25: 13:44:11Z / 01:45:43Z next day."""
    sun = get(world, **LAX).json()["sun"]
    assert abs(_z(sun["sunrise"]) - _z("2026-09-25T13:44:11Z")) <= timedelta(minutes=2)
    assert abs(_z(sun["sunset"]) - _z("2026-09-26T01:45:43Z")) <= timedelta(minutes=2)
    assert sun["source"] == "computed" and sun["absent"] == []
    assert abs(sun["day_length_min"] - 722) <= 2


def test_T9_polar_night_is_null_with_its_reason(world):
    world["now"] = datetime(2026, 12, 15, 12, tzinfo=UTC)
    world["runs"] = [datetime(2026, 12, 15, 6, tzinfo=UTC)]
    world["bank"].run = datetime(2026, 12, 15, 6, tzinfo=UTC)
    b = get(world, lat=70.0, lon=20.0).json()
    sun = b["sun"]
    assert sun["sunrise"] is None and sun["sunset"] is None
    assert "sunrise: polar night" in sun["absent"]
    assert sun["day_length_min"] == 0
    assert all(d["sunrise"] is None and "sunrise: polar night" in d["absent"]
               for d in b["daily"])


def test_T9_midnight_sun_is_not_polar_night():
    s = lf.sun_times(70.0, 20.0, datetime(2026, 6, 21).date(), "Europe/Oslo")
    assert s["sunrise"] is None and "sunrise: midnight sun" in s["absent"]
    assert s["day_length_min"] == 1440


# ═══════════════════════════════════════════════════════════════════════════
# T10 — the neighbours untouched, the README row, the route once
# ═══════════════════════════════════════════════════════════════════════════

UNTOUCHED = {  # sha-256 on origin/main at 536d61b
    "tests/test_weather_point.py": "4a66bafc3f1af5ebd9361b411805d67939c7523c5ca99b8e05090e4f78001c27",
    "tests/test_enso_catalog.py": "a0636f4d1bd3db1731d1b30cffdca534931caf1d36b2a2446e8b5a0608a03e2a",
    "tests/test_cors.py": "c0d34a9a3d599e2c40f133757e3c955f1c800f7e5f074bae54309b2524029c96",
}


@pytest.mark.parametrize("path,sha", sorted(UNTOUCHED.items()))
def test_T10_neighbour_tests_untouched(path, sha):
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha


def test_T10_readme_row_present():
    readme = (ROOT / "README.md").read_text()
    assert readme.count("GET /api/local/forecast?lat=&lon=") == 1


def test_T10_route_registered_once():
    paths = [r.path for r in main.app.routes if getattr(r, "path", None) == "/api/local/forecast"]
    assert paths == ["/api/local/forecast"]
