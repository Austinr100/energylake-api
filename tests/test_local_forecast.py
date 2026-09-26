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
T11..T16, T6′ (`test_T6p_…`), T7 (`test_T7_locator_…`) and T10′ are d091485's
(the follow-ups: the scoped fallback D-09-25-09, hourly from now D-09-25-10,
days 8–10 from the model arm, `weather_point.ladder()` lifted, the full-circle
wrap). T2, T4 and T8 carry the amendments those rulings make to #78's pins.
T17..T25 are d091488's (D-09-25-15 the content ETag, D-09-25-16 `units=us`,
D-09-25-17 `pm180` read from the pantry's committed header bytes); T7 and T8
carry its amendments.
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
PROD_HEADER_PATH = (Path(__file__).parent / "fixtures"
                    / "weather_sidecar_header_global_2026092518_t2m_f000.json")
PROD_HEADER_SHA = "9ff46b1a592f42a0cc01d445637a4562a791ad6e07d164e10967f6116c982818"
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
    #: D-09-25-16: a `units=us` forecast read gets the US-unit twin, as NWS would
    #: answer it; the SI originals stay for the tests that pin SI conversion.
    FILES_US = {"hourly": "forecastHourly.us.json", "forecast": "forecast.us.json"}

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
        if (params or {}).get("units") == "us" and kind in self.FILES_US:
            return 200, _fixture(self.FILES_US[kind])
        return 200, _fixture(self.FILES[kind])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------------------
# The model arm's bank fake — the global sidecars
# ---------------------------------------------------------------------------

#: D-09-25-17 — the pantry's own bytes: the t2m global header for 20260925 18Z
#: f000, rebuilt and accepted only because its sha equals the `header_sha256`
#: the pantry stamped in `d2_render_runs.meta.sidecar` (pantry
#: docs/receipts/local-forecast-etag-units-d091488/fixtures/). The fake below is
#: DERIVED from it, never typed beside it (#77: d091485's hand-typed
#: "west_negative_monotonic" passed every test and 503'd every global point).
PROD_HEADER = json.loads(PROD_HEADER_PATH.read_bytes())
GEOMETRY_KEYS = ("shape", "lat0", "lon0", "dlat", "dlon", "lat_order",
                 "lon_convention", "dtype", "byte_order")
GLOBAL_HEADER = {k: PROD_HEADER[k] for k in GEOMETRY_KEYS + ("model", "crop")}
UNITS = {"t2m": "K", "wind10m": "m s-1", "mslp": "Pa", "dswrf": "W m-2"}


def bank_value(param, fhr):
    """Weather-shaped, and linear in fhr so interpolation is checkable."""
    return {"t2m": 283.15 + 0.1 * fhr, "wind10m": 5.0 + fhr / 60.0,
            "mslp": 101300.0 - 10.0 * fhr, "dswrf": 200.0}[param]


class FakeGlobalBank:
    """`placed[(j, i)]` adds a delta to every param's value at that cell, so a
    test can prove WHICH cell a point read (d091485 T16)."""

    def __init__(self, run=RUN, absent_params=(), absent_keys=(), placed=None):
        self.run = run
        self.absent_params = set(absent_params)
        self.absent_keys = set(absent_keys)
        self.placed = dict(placed or {})
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
            return 200, json.dumps({**PROD_HEADER, "param": param,
                                    "units": UNITS[param]}).encode()
        assert byte_range is not None, "a value read with no Range header"
        start, end = (int(x) for x in byte_range.split("=")[1].split("-"))
        self.range_widths.append(end - start + 1)
        cell = divmod(start // 4, GLOBAL_HEADER["shape"][1])
        return 206, struct.pack("<f", bank_value(param, fhr) + self.placed.get(cell, 0.0))


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
    # 14 NWS periods starting on a day → 7 paired rows; d091485 appends days
    # 8–10 from the model arm (T15), so the NWS rows are the first seven.
    nws_rows = [d for d in b["daily"] if d["source"].startswith("nws ·")]
    assert len(nws_rows) == 7 and b["daily"][:7] == nws_rows
    assert len(b["daily"]) == 10
    his_f = [77, 79, 81, 84, 80, 75, 73]
    los_f = [63, 64, 66, 65, 62, 61, 60]
    for row, hf, lf_ in zip(b["daily"], his_f, los_f):
        assert row["hi"] == round((hf - 32) * 5 / 9, 1)
        assert row["lo"] == round((lf_ - 32) * 5 / 9, 1)
        assert row["hi"] > row["lo"]
    assert b["daily"][0]["date"] == "2026-09-25"
    assert b["daily"][6]["date"] == "2026-10-01"


def test_T2_si_units_a_known_fahrenheit_arrives_as_celsius(world):
    # d091488: the route now asks for units=us; this pin is conversion FROM SI,
    # so the SI hourly original is served explicitly (§2.3).
    world["nws"].override["hourly"] = _fixture("forecastHourly.json")
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
    # D-09-25-10 trims to the current hour; at the run's own hour, row h is f00h.
    world["now"] = RUN + timedelta(minutes=10)
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
    # D-09-25-10 trims to the current hour; at the run's own hour, row h is f00h.
    world["now"] = RUN + timedelta(minutes=10)
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


def test_T7_every_nws_call_carries_units_us_on_both_forecasts(world):
    # d091488 amendment (D-09-25-16): units=si → units=us.
    get(world, **LAX)
    seen = [params for kind, _, params in world["nws"].calls
            if kind in ("hourly", "forecast")]
    assert len(seen) == 2
    assert all(params == {"units": "us"} for params in seen)


def test_T7_the_real_transport_sends_the_required_headers():
    assert nws_arm.HEADERS == {"User-Agent": "energylake.io (ops@energylake.io)",
                               "Accept": "application/geo+json"}
    assert (nws_arm.CONNECT_TIMEOUT_S, nws_arm.READ_TIMEOUT_S) == (4.0, 8.0)


# ═══════════════════════════════════════════════════════════════════════════
# T8 — headers, 304, 400
# ═══════════════════════════════════════════════════════════════════════════

def test_T8_cache_headers_and_etag_per_arm(world):
    # d091488 amendment (D-09-25-15): the tag is W/"<arm>:<sha256[:32]>" of the
    # content, not W/"<arm>:<issued_at>:<station|run>".
    us = get(world, **LAX)
    assert us.headers["cache-control"] == "max-age=300"
    assert us.headers["etag"] == lf.content_etag(us.json())
    assert re.fullmatch(r'W/"nws:[0-9a-f]{32}"', us.headers["etag"])
    m = get(world, **VANCOUVER)
    assert m.headers["cache-control"] == "max-age=900"
    assert m.headers["etag"] == lf.content_etag(m.json())
    assert re.fullmatch(r'W/"model:[0-9a-f]{32}"', m.headers["etag"])


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
    assert b["receipts"]["notes"] == ["lon 241.59 normalised to -118.41",
                                      "hourly from 21Z, 48 rows",       # D-09-25-10
                                      "days 8–10 from model · GFS 12Z"]  # d091485 §2.3


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


# ═══════════════════════════════════════════════════════════════════════════
# d091485 — the follow-ups
# ═══════════════════════════════════════════════════════════════════════════

# ── T11 — D-09-25-09: an observation failure is stated in place ─────────────

def test_T11_observation_404_stays_on_the_nws_arm(world):
    world["nws"] = FakeNws(obs=404)
    world["install"]()
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "nws"
    assert b["receipts"]["fallback"] is None
    now = b["now"]
    assert now["t"] is None and "t: obs unavailable (HTTP 404)" in now["absent"]
    for f in ("feels", "dewpoint", "rh", "sky", "mslp", "age_min"):
        assert now[f] is None and f"{f}: obs unavailable (HTTP 404)" in now["absent"]
    assert "KLAX" in now["source"] and "no recent observation" in now["source"]
    assert now["source"] == "nws · KLAX · no recent observation"
    assert "observed" not in now["source"]
    assert len(b["hourly"]) == 48 and len(b["daily"]) >= 7
    assert b["hourly"][0]["source"].startswith("nws · gridpoint")
    assert b["alerts"] and b["receipts"]["station"] == "KLAX"


def test_T11_a_failed_observation_is_not_memoised(world):
    world["nws"] = FakeNws(obs=404)
    world["install"]()
    get(world, **LAX)
    world["clock"].t += 1
    b = get(world, **LAX).json()
    assert world["nws"].count("obs") == 2                 # retried, not memoised
    assert world["nws"].count("hourly") == 1              # the forecast was
    assert b["receipts"]["memo"]["forecast"] == "hit"


def test_T11_station_list_failure_and_malformed_obs_are_garnish_too(world):
    world["nws"] = FakeNws(stations=500)
    world["install"]()
    b = get(world, **LAX).json()
    assert b["receipts"]["arm"] == "nws" and b["receipts"]["fallback"] is None
    assert b["now"]["source"] == "nws · station unknown · no recent observation"
    assert "t: obs unavailable (HTTP 500)" in b["now"]["absent"]

    world["nws"] = FakeNws()
    world["nws"].override["obs"] = {"no": "properties"}
    world["install"]()
    b = get(world, **LAX).json()
    assert b["receipts"]["arm"] == "nws"
    assert "t: obs unavailable (KeyError)" in b["now"]["absent"]


# ── T12 — D-09-25-09: an alerts failure is [] WITH its note ─────────────────

def test_T12_alerts_503_is_an_empty_list_with_the_note(world):
    world["nws"] = FakeNws(alerts=503)
    world["install"]()
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "nws" and b["receipts"]["fallback"] is None
    assert b["alerts"] == []
    assert "alerts unavailable (HTTP 503)" in b["receipts"]["notes"]
    assert b["now"]["source"].startswith("nws · KLAX · observed")


def test_T12_a_real_no_alerts_answer_carries_no_note(world):
    world["nws"].override["alerts"] = {"features": []}
    b = get(world, **LAX).json()
    assert b["alerts"] == []
    assert not any(n.startswith("alerts unavailable") for n in b["receipts"]["notes"])


# ── T13 — the forecast calls still fall back exactly as #78's T6 ───────────

@pytest.mark.parametrize("kind", ["points", "forecast", "hourly"])
@pytest.mark.parametrize("failure,reason", [
    (503, "HTTP 503"),
    (404, "HTTP 404"),
    (TimeoutError("read timed out"), "TimeoutError"),
])
def test_T13_forecast_critical_failure_falls_back(world, kind, failure, reason):
    world["nws"] = FakeNws(**{kind: failure})
    world["install"]()
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "model"
    assert b["receipts"]["fallback"] == {"from": "nws", "reason": reason}
    assert b["now"]["source"].startswith("model ·")
    assert r.headers["cache-control"] == "max-age=900"


# ── T14 — D-09-25-10: hourly from the current hour ─────────────────────────

def test_T14_trim_to_now_floors_the_hour_and_names_it():
    rows = [{"valid": lf.iso_z(RUN + timedelta(hours=h))} for h in range(60)]
    now = datetime(2026, 9, 25, 14, 37, tzinfo=UTC)
    kept, note = lf.trim_to_now(rows, now)
    assert kept[0]["valid"] == "2026-09-25T14:00:00Z"
    assert all(lf.parse_iso(r["valid"]) >= datetime(2026, 9, 25, 14, tzinfo=UTC)
               for r in kept)
    assert len(kept) == 48
    assert note == "hourly from 14Z, 48 rows"
    kept, note = lf.trim_to_now(rows, RUN + timedelta(hours=50, minutes=5))
    assert len(kept) == 10 and note == "hourly from 14Z, 10 rows"   # fewer is correct


def test_T14_nws_hourly_starts_this_hour(world):
    world["now"] = datetime(2026, 9, 26, 14, 37, tzinfo=UTC)
    b = get(world, **LAX).json()
    assert b["receipts"]["arm"] == "nws"
    hourly = b["hourly"]
    assert hourly[0]["valid"] == "2026-09-26T14:00:00Z"
    assert not any(h["valid"] < "2026-09-26T14:00:00Z" for h in hourly)
    # the fixture's 60 periods run to 2026-09-28T08:00Z → 43 rows from 14Z
    assert len(hourly) == 43
    assert "hourly from 14Z, 43 rows" in b["receipts"]["notes"]


def test_T14_model_hourly_starts_this_hour(world):
    world["now"] = datetime(2026, 9, 25, 14, 37, tzinfo=UTC)
    b = get(world, **VANCOUVER).json()
    hourly = b["hourly"]
    assert hourly[0]["valid"] == "2026-09-25T14:00:00Z"
    assert hourly[0]["source"] == "model · GFS 12Z f000–f006 interp"
    assert len(hourly) == 48
    assert "hourly from 14Z, 48 rows" in b["receipts"]["notes"]
    assert b["now"]["source"] == "model · GFS 12Z f000"          # now stays f000


def test_T14_model_arm_invents_nothing_past_its_last_f_hour(world):
    world["now"] = datetime(2026, 9, 25, 14, 37, tzinfo=UTC)
    world["bank"].absent_keys = {
        wp.value_key("gfs", "global", RUN, "t2m", f) for f in wp.ladder_fhrs() if f > 24}
    b = get(world, **VANCOUVER).json()
    hourly = b["hourly"]
    assert b["receipts"]["fhr_range"] == [0, 24]
    assert hourly[-1]["valid"] == lf.iso_z(RUN + timedelta(hours=24))
    assert len(hourly) == 23                                      # 14Z..12Z+24h
    assert "hourly from 14Z, 23 rows" in b["receipts"]["notes"]
    world["bank"].absent_keys = set()
    model_arm._run_memo.clear()
    world["now"] = RUN + timedelta(hours=230, minutes=37)
    hourly = get(world, **VANCOUVER).json()["hourly"]
    assert len(hourly) == 11
    assert hourly[-1]["valid"] == lf.iso_z(RUN + timedelta(hours=240))


# ── T15 — days 8–10 from the model arm, each row labelled ──────────────────

def test_T15_days_8_to_10_from_the_model_arm(world):
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "nws"
    daily = b["daily"]
    assert len(daily) == 10
    assert [d["date"] for d in daily[7:]] == ["2026-10-02", "2026-10-03", "2026-10-04"]
    assert all(d["source"].startswith("nws ·") for d in daily[:7])
    for d in daily[7:]:
        assert d["source"].startswith("model · GFS"), d["source"]
        assert d["source"].startswith("model · GFS 12Z f")
    assert "days 8–10 from model · GFS 12Z" in b["receipts"]["notes"]
    assert "observed" not in json.dumps(daily[7:])


def test_T15_model_arm_raising_leaves_seven_rows_and_the_note(world, monkeypatch):
    async def boom(*a, **k):
        raise model_arm.ModelArmError("no banked gfs run carries a global t2m f000 sidecar")
    monkeypatch.setattr(main._model_arm, "answer", boom)
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "nws"
    assert len(b["daily"]) == 7
    assert "days 8–10 unavailable: model arm 503" in b["receipts"]["notes"]


def test_T15_production_today_no_global_sidecar_still_200(world):
    world["runs"] = []                       # the 503 production answers today
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["daily"]) == 7
    assert "days 8–10 unavailable: model arm 503" in b["receipts"]["notes"]


# ── T16 — the model arm on a pm180 global header ───────────────────────────

def _pm180_cell(lat, lon):
    """Independent of weather_point: nearest centre on the pm180 grid, the
    longitude index taken modulo nx (pantry d2/values.locate_cell)."""
    j = int(math.floor((lat + 90.0) / 0.25 + 0.5))
    i = int(math.floor((lon + 180.0) / 0.25 + 0.5)) % 1440
    return j, i


@pytest.mark.parametrize("name,lat,lon", [
    ("Vancouver", 49.28, -123.12),
    ("Fiji", -17.8, 178.0),
    ("Samoa", -13.8, -172.1),
    ("equator 179.9E", 0.0, 179.9),
    ("equator 179.9W", 0.0, -179.9),
])
def test_T16_model_arm_reads_the_placed_cell_on_pm180(world, name, lat, lon):
    assert (GLOBAL_HEADER["lon0"], GLOBAL_HEADER["dlon"], GLOBAL_HEADER["shape"]) \
        == (-180.0, 0.25, [721, 1440])
    j, i = _pm180_cell(lat, lon)
    # The placed cell reads +7 K; its seam-side neighbours read -20 K, so a
    # point that lands one column off (a clamp at the seam) is caught.
    world["bank"].placed = {(j, i): 7.0, (j, (i - 1) % 1440): -20.0,
                            (j, (i + 1) % 1440): -20.0}
    r = get(world, lat=lat, lon=lon, arm="model")
    assert r.status_code == 200, (name, r.text)
    b = r.json()
    assert b["receipts"]["arm"] == "model"
    assert b["now"]["t"] == round(283.15 + 7.0 - 273.15, 1), name
    off = 4 * (j * 1440 + i)
    assert all(rng == f"bytes={off}-{off + 3}"
               for k, rng in world["bank"].calls if k.endswith(".f32")), name


def test_T16_both_sides_of_the_seam_are_one_cell():
    assert _pm180_cell(0.0, 179.9) == _pm180_cell(0.0, -179.9) == (360, 0)


# ── T6′ — /api/weather/point/ladder byte-identical to main ─────────────────

#: sha-256 of each response body on origin/main 7a4e7c6 (before the lift),
#: with `time.perf_counter` pinned so `elapsed_ms` is 0.0.
LADDER_SHA_MAIN = {
    "default": (200, "1fba61038d2cba75eb14b990e186dce237da228910ca827c6ce535fcc26df016"),
    "step24": (200, "743cdfd3a9438a531b50ed336b30f2da05a82ee4cc70d2158089182b546f57c5"),
    "chain": (200, "a45652fad15e16601246dc8b4ca9f950c110655827716ddf46086584dbd1236c"),
    "no_f000": (200, "b8939b17b5f6d2c1024e9f080d82645e5c4b77c0df8e41dffb86f3841a8b3823"),
    "outside": (404, "346ca6bc36a4b1ad632a876696477054be296f07dc7a0bbfe8c96c2c8f8f70b7"),
    "no_run": (404, "66aeb325d1d88614b6395ab08611dde1b4b76274656f0f364f54fe63cc77cfba"),
}
LADDER_CASES = {
    "default": ({}, ()),
    "step24": ({"fhr_step": 24, "fhr_max": 240}, ()),
    "chain": ({"chain": 1}, ()),
    "no_f000": ({}, ("weather/values/gfs/na3/20260903/06Z/mslp_f000",)),
    "outside": ({"lat": 5.0}, ()),
    "no_run": ({"run": "20260904T00Z"}, ()),
}


@pytest.mark.parametrize("case", sorted(LADDER_CASES))
def test_T6p_ladder_route_is_byte_identical_to_main(monkeypatch, case):
    import test_weather_point as twp     # its FakeSidecar is the ladder_fake's

    over, drop = LADDER_CASES[case]
    keys = ["weather/values/gfs/na3/20260903/06Z/mslp_f%03d.f32" % f
            for f in wp.ladder_fhrs()]
    keys += [k.replace(".f32", ".json") for k in keys]
    keys = [k for k in keys if not any(k.startswith(d) for d in drop)]
    store = wp.SidecarStore(transport=twp.FakeSidecar(present=keys))
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)
    monkeypatch.setattr(main.time, "perf_counter", lambda: 0.0)
    params = {"lat": 36.74, "lon": -119.79, "param": twp.PARAM, "run": twp.RUN, **over}
    r = TestClient(main.app).get("/api/weather/point/ladder", params=params)
    assert (r.status_code, hashlib.sha256(r.content).hexdigest()) == LADDER_SHA_MAIN[case]


def test_T6p_model_arm_reads_through_weather_point_ladder(world, monkeypatch):
    seen = []
    real = wp.ladder

    async def spy(store, model, crop, run_dt, param, lat, lon, fhrs):
        seen.append((model, crop, param))
        return await real(store, model, crop, run_dt, param, lat, lon, fhrs)
    monkeypatch.setattr(wp, "ladder", spy)
    assert get(world, **VANCOUVER).status_code == 200
    assert sorted(seen) == sorted(("gfs", "global", p) for p in model_arm.PARAMS)
    src = (ROOT / "model_arm.py").read_text()
    assert "wp.locate(" not in src and "get_values(" not in src   # the copy is gone


# ── T7 — weather_point's locator wraps a full-circle header ────────────────

def _hdr(lon0):
    return wp.SidecarHeader({**GLOBAL_HEADER, "lon0": lon0})


def test_T7_locator_wraps_a_full_circle_header():
    h = _hdr(-180.0)
    assert wp.is_full_circle(h)
    east, west = wp.locate(0.0, 179.9, h), wp.locate(0.0, -179.9, h)
    assert east["i"] == west["i"] == 0                       # both inside: the 180° column
    last = wp.locate(0.0, 179.8, h)["i"]
    assert last == 1439 and (last + 1) % 1440 == east["i"]   # adjacent across the seam
    assert wp.locate(0.0, -179.8, h)["i"] == 1               # and on the other side
    assert wp.locate(0.0, 179.875, h)["i"] == 0              # the seam midpoint: no 404
    assert wp.locate(0.0, 180.0, h)["i"] == 0
    assert wp.locate(0.0, 539.9, h)["i"] == 0                # any wrapping


def test_T7_locator_matches_pantry_locate_cell_on_both_axis_origins():
    for lon0 in (-180.0, 0.0):
        h = _hdr(lon0)
        for k in range(-3600, 3601):
            lon = k * 0.05
            fi = (wp.wrap180(lon) - lon0) / 0.25
            want = int(math.floor(fi + 0.5)) % 1440
            assert wp.locate(10.0, lon, h)["i"] == want, (lon0, lon)


def test_T7_latitude_still_refuses_and_regional_crops_still_do_not_wrap():
    with pytest.raises(wp.PointError) as ei:
        wp.locate(95.0, 0.0, _hdr(-180.0))
    assert ei.value.status == 400
    na3 = wp.SidecarHeader({"shape": [222, 583], "lat0": 14.75, "lon0": -186.75,
                            "dlat": 0.25, "dlon": 0.25})
    assert not wp.is_full_circle(na3)
    with pytest.raises(wp.PointError) as ei:
        wp.locate(40.0, 0.0, na3)
    assert ei.value.status == 404


# ═══════════════════════════════════════════════════════════════════════════
# d091488 — pm180 from the pantry's bytes, the content ETag, units=us
# ═══════════════════════════════════════════════════════════════════════════

# ── T22 — D-09-25-17: the committed production header reads as pm180 ───────

def test_T22_production_header_is_the_pantrys_bytes_and_reads_as_pm180():
    raw = PROD_HEADER_PATH.read_bytes()
    assert len(raw) == 660
    assert hashlib.sha256(raw).hexdigest() == PROD_HEADER_SHA
    h = wp.SidecarHeader(json.loads(raw),
                         key="weather/values/gfs/global/20260925/18Z/t2m_f000.json")
    assert h.lon_convention == "pm180"
    assert h.inferred == []                    # every field read, none guessed
    assert wp.is_full_circle(h)
    assert h.axes()["lon_convention"] == "pm180"


@pytest.mark.parametrize("name,lat,lon", [
    ("Vancouver", 49.283, -123.121),
    ("Fiji", -17.8, 178.0),
    ("Samoa", -13.8, -172.1),
    ("seam 179.9E", 0.0, 179.9),
    ("seam 179.9W", 0.0, -179.9),
])
def test_T22_locate_on_the_production_header_matches_the_independent_cell(name, lat, lon):
    h = wp.SidecarHeader(json.loads(PROD_HEADER_PATH.read_bytes()))
    got = wp.locate(lat, lon, h)
    assert (got["j"], got["i"]) == _pm180_cell(lat, lon), name


# ── T23 — the fake is derived from the bytes, field for field ──────────────

def test_T23_global_header_agrees_with_the_production_header():
    for k in ("shape", "lat0", "lon0", "dlat", "dlon", "lat_order",
              "lon_convention", "dtype", "byte_order"):
        assert GLOBAL_HEADER[k] == PROD_HEADER[k], k
    assert GLOBAL_HEADER["lon_convention"] == "pm180"


# ── T24 — the route on the production header ───────────────────────────────

def test_T24_vancouver_answers_from_the_model_arm_on_the_production_header(world):
    r = get(world, lat=49.283, lon=-123.121)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "model"
    assert b["now"]["source"] == "model · GFS 12Z f000"
    headers = [k for k, _ in world["bank"].calls if k.endswith(".json")]
    assert headers and all("/global/" in k for k in headers)


def test_T24_lax_days_8_to_10_from_the_model_on_the_production_header(world):
    r = get(world, **LAX)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["receipts"]["arm"] == "nws"
    assert len(b["daily"]) == 10
    for d in b["daily"][7:]:
        assert d["source"].startswith("model · GFS 12Z"), d["source"]
    assert not any("unavailable" in n for n in b["receipts"]["notes"])


# ── T25 — the full-circle guard, and the unchanged unknown-convention 502 ──

NA3 = {"shape": [222, 583], "lat0": 14.75, "lon0": -186.75, "dlat": 0.25, "dlon": 0.25,
       "lat_order": "ascending"}


def test_T25_pm180_on_a_partial_window_is_a_502_naming_the_shape():
    with pytest.raises(wp.PointError) as ei:
        wp.SidecarHeader({**NA3, "lon_convention": "pm180"}, key="k")
    assert ei.value.status == 502
    body = ei.value.detail
    assert body["error"] == "pm180 lon_convention on a partial window"
    assert body["shape"] == [222, 583] and body["lon_convention"] == "pm180"
    assert body["lon_span"] == 583 * 0.25


def test_T25_an_unknown_convention_is_the_unchanged_502():
    with pytest.raises(wp.PointError) as ei:
        wp.SidecarHeader({**NA3, "lon_convention": "greenwich_sideways"}, key="k")
    assert ei.value.status == 502
    assert ei.value.detail == {"error": "unknown lon_convention", "key": "k",
                             "lon_convention": "greenwich_sideways"}


# ── T17 — D-09-25-15: the tag is the content ───────────────────────────────

def _payload(world, **params):
    r = get(world, **(params or LAX))
    assert r.status_code == 200, r.text
    return r.json()


def test_T17_request_only_fields_do_not_move_the_tag(world):
    base = _payload(world)
    other = json.loads(json.dumps(base))
    other["receipts"]["generated_at"] = "2030-01-01T00:00:00Z"
    other["receipts"]["memo"] = {"points": "hit", "forecast": "hit",
                                 "obs": "hit", "alerts": "hit"}
    assert lf.content_etag(other) == lf.content_etag(base)
    assert lf.content_etag(base).startswith('W/"nws:')
    assert lf.content_etag(_payload(world, **VANCOUVER)).startswith('W/"model:')


@pytest.mark.parametrize("field", ["now.valid", "now.age_min", "alerts[0]",
                                   "hourly[0].valid", "daily[0].source"])
def test_T17_every_weather_field_moves_the_tag(world, field):
    base = _payload(world)
    other = json.loads(json.dumps(base))
    if field == "now.valid":
        other["now"]["valid"] = "2026-09-25T21:05:00Z"
    elif field == "now.age_min":
        other["now"]["age_min"] += 1
    elif field == "alerts[0]":
        other["alerts"][0]["severity"] = "Severe"
    elif field == "hourly[0].valid":
        other["hourly"][0]["valid"] = "2026-09-25T22:00:00Z"
    else:
        other["daily"][0]["source"] = "model · GFS 12Z f024"
    assert lf.content_etag(other) != lf.content_etag(base), field


def test_T17_a_payload_without_an_arm_is_refused_and_the_old_tag_is_gone():
    with pytest.raises(ValueError):
        lf.content_etag({"receipts": {}})
    with pytest.raises(ValueError):
        lf.content_etag({})
    assert not hasattr(lf, "etag")


# ── T18 — the production incident: a new observation / alert is a 200 ─────

def _latest_at(ts):
    doc = _fixture("latest.json")
    doc["properties"]["timestamp"] = ts
    doc["id"] = f"https://api.weather.gov/stations/KLAX/observations/{ts}"
    return doc


def _six_minutes_later(world):
    world["now"] = NOW + timedelta(minutes=6)
    world["clock"].t += 360          # obs (5 min) and alerts (2 min) memo expire
    world["nws"].calls.clear()


def test_T18_a_new_observation_under_the_same_issuance_is_a_200(world):
    r1 = get(world, **LAX)
    tag_a = r1.headers["etag"]
    assert r1.json()["now"]["source"] == "nws · KLAX · observed 20:53Z"
    _six_minutes_later(world)
    world["nws"].override["obs"] = _latest_at("2026-09-25T21:10:00+00:00")
    r2 = world["http"].get("/api/local/forecast", params=LAX,
                           headers={"If-None-Match": tag_a})
    assert r2.status_code == 200, r2.status_code
    b = r2.json()
    assert b["receipts"]["issued_at"] == r1.json()["receipts"]["issued_at"]
    assert world["nws"].count("hourly") == 0          # same forecast, from the memo
    assert b["now"]["source"] == "nws · KLAX · observed 21:10Z"
    assert r2.headers["etag"] != tag_a


def test_T18_a_new_alert_under_the_same_issuance_is_a_200(world):
    r1 = get(world, **LAX)
    tag_a = r1.headers["etag"]
    _six_minutes_later(world)
    alerts = _fixture("alerts.json")
    alerts["features"].append({
        "id": "https://api.weather.gov/alerts/urn:oid:fixture.002.1",
        "type": "Feature",
        "properties": {"id": "urn:oid:fixture.002.1", "event": "Wind Advisory",
                       "severity": "Moderate",
                       "headline": "Wind Advisory issued September 25 at 2:10PM PDT",
                       "onset": "2026-09-25T14:10:00-07:00",
                       "ends": "2026-09-26T06:00:00-07:00"}})
    world["nws"].override["alerts"] = alerts
    r2 = world["http"].get("/api/local/forecast", params=LAX,
                           headers={"If-None-Match": tag_a})
    assert r2.status_code == 200, r2.status_code
    b = r2.json()
    assert b["receipts"]["issued_at"] == r1.json()["receipts"]["issued_at"]
    assert "Wind Advisory" in [a["event"] for a in b["alerts"]]
    assert r2.headers["etag"] != tag_a


# ── T19 — nothing changed (same age_min) → 304 ─────────────────────────────

def test_T19_an_unchanged_body_is_an_empty_304_with_the_same_headers(world):
    r1 = get(world, **LAX)
    assert r1.json()["receipts"]["memo"]["obs"] == "miss"
    world["now"] = NOW + timedelta(seconds=20)   # generated_at moves, age_min does not
    world["clock"].t += 20                       # every memo entry now a hit
    r2 = world["http"].get("/api/local/forecast", params=LAX,
                           headers={"If-None-Match": r1.headers["etag"]})
    assert r2.status_code == 304
    assert r2.content == b""
    assert r2.headers["etag"] == r1.headers["etag"]
    assert r2.headers["cache-control"] == r1.headers["cache-control"] == "max-age=300"


# ── T20 — D-09-25-16: NWS in its own units ─────────────────────────────────

def _c_to_f(c):
    """The page's cToF: °C → integer °F, half away from zero."""
    f = c * 9 / 5 + 32
    return int(math.copysign(math.floor(abs(f) + 0.5), f))


def test_T20_forecast_urls_carry_units_us(world):
    get(world, **LAX)
    fc = [(kind, params) for kind, _, params in world["nws"].calls
          if kind in ("hourly", "forecast")]
    assert sorted(k for k, _ in fc) == ["forecast", "hourly"]
    assert all(params == {"units": "us"} for _, params in fc)


def test_T20_us_twin_round_trips_to_nws_own_integer_fahrenheit(world):
    assert lf.to_celsius(80, "F") == 26.7 and _c_to_f(26.7) == 80
    twin = {p["startTime"]: p for p in
            _fixture("forecastHourly.us.json")["properties"]["periods"]}
    assert all(p["temperatureUnit"] == "F" and isinstance(p["temperature"], int)
               for p in twin.values())
    hourly = _payload(world)["hourly"]
    assert len(hourly) == 48
    by_valid = {lf.iso_z(lf.parse_iso(k)): p for k, p in twin.items()}
    for row in hourly:
        f = by_valid[row["valid"]]["temperature"]
        assert row["t"] == round((f - 32) * 5 / 9, 1), row["valid"]
        assert _c_to_f(row["t"]) == f, row["valid"]


def test_T20_wind_in_mph_is_read_to_a_tenth_of_a_metre_per_second(world):
    assert lf.parse_wind_speed("10 mph") == 4.5
    hourly = _fixture("forecastHourly.us.json")
    for p in hourly["properties"]["periods"]:
        p["windSpeed"] = "10 mph"
    world["nws"].override["hourly"] = hourly
    b = _payload(world)
    assert {h["wind"]["speed"] for h in b["hourly"]} == {4.5}


def test_T20_twins_are_current_with_their_si_sources():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_nws_us_fixtures.py"),
                        "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("forecast.us.json", "forecastHourly.us.json"):
        assert "hand-built" in _fixture(name)["_provenance"]


# ═══════════════════════════════════════════════════════════════════════════
# d091491 — a cold read under two seconds, the rest of today, `unknown` says why
# ═══════════════════════════════════════════════════════════════════════════

# ── T1 (d091491) — Server-Timing, never the body (D-09-25-27) ──────────────

PREVIEW_ORIGIN = "https://energylake-git-lane-austinrodriguez221-6328s-projects.vercel.app"
STRANGER_ORIGIN = "https://energylake.example.com"


def _timing_names(r):
    return [p.strip().split(";")[0] for p in r.headers["server-timing"].split(",")]


def _timing_ok(r):
    for p in r.headers["server-timing"].split(","):
        assert re.fullmatch(r"[a-z_]+;dur=\d+\.\d", p.strip()), p
    names = _timing_names(r)
    assert names == [n for n in lf.TIMING_NAMES if n in names], names
    assert names[-1] == "total"
    return names


def test_T1f_server_timing_on_200_304_and_503_in_the_ruled_order(world):
    r = get(world, **LAX)
    assert r.status_code == 200
    assert _timing_ok(r) == list(lf.TIMING_NAMES)
    r304 = world["http"].get("/api/local/forecast", params=LAX,
                             headers={"If-None-Match": r.headers["etag"]})
    assert r304.status_code == 304
    assert _timing_ok(r304)[-1] == "total"
    m = get(world, **VANCOUVER)
    assert m.status_code == 200
    assert _timing_ok(m) == ["model_run", "model_ladders", "build", "total"]
    world["runs"] = []
    model_arm._run_memo.clear()
    r503 = get(world, lat=10.0, lon=10.0)
    assert r503.status_code == 503
    names = _timing_ok(r503)
    assert not any(n.startswith("nws_") for n in names) and "build" not in names


@pytest.mark.parametrize("origin,tao", [
    ("allowed", True), (PREVIEW_ORIGIN, True), (STRANGER_ORIGIN, False), (None, False)])
def test_T1f_expose_headers_and_timing_allow_origin(world, origin, tao):
    if origin == "allowed":
        origin = main.ALLOWED_ORIGINS[0]
    headers = {"Origin": origin} if origin else {}
    r = world["http"].get("/api/local/forecast", params=VANCOUVER, headers=headers)
    assert r.status_code == 200
    assert r.headers["access-control-expose-headers"] == "Server-Timing, ETag"
    assert r.headers.get("timing-allow-origin") == (origin if tao else None)


def test_T1f_body_and_etag_are_byte_identical_with_and_without_the_recorder(world, monkeypatch):
    real = lf.Timings
    ticks = iter(range(10 ** 6))

    def slow():                                   # every read of the clock is 0.25 s later
        return next(ticks) * 0.25

    out = []
    for clock in (lambda: 0.0, slow):
        monkeypatch.setattr(lf, "Timings", lambda clock=clock: real(clock=clock))
        world["install"]()                        # cold both times: the memo is in the body
        model_arm._run_memo.clear()
        for where in (LAX, VANCOUVER):
            r = get(world, **where)
            assert r.status_code == 200
            out.append((where["lat"], r.content, r.headers["etag"],
                        r.headers["server-timing"]))
    for a, b in zip(out[:2], out[2:]):
        assert a[0] == b[0]
        assert a[1] == b[1] and a[2] == b[2]      # D-09-25-15 unharmed
        assert a[3] != b[3]                       # while the header did move
