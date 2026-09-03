"""
Tests for Weather Atlas B — the point API:
  GET /api/weather/point?lat=&lon=&param=&model=gfs&run=&fhr=
  GET /api/weather/point/ladder?lat=&lon=&param=&model=gfs&run=

NO NETWORK, EVER. The sidecar is synthetic: a `FakeSidecar` transport backed by
the pinned header fixture

    tests/fixtures/weather_sidecar_header_na3_2026090306_mslp_f006.json

which is the header contract of the live proof object
`weather/values/gfs/na3/20260903/06Z/mslp_f006.json` — shape [222, 583],
lat0 14.75, lon0 -186.75, dlat/dlon 0.25, lat_order ascending, lon_convention
west_negative_monotonic, float32 little-endian. So the index arithmetic under
test is run against the REAL geometry; only the bytes are ours.

THE SYNTHETIC ARRAY IS INVERTIBLE ON PURPOSE. Cell (j, i) holds the float32
value `j*1024 + i`. Every such value is an integer below 2^24 and so is exact in
float32, and since nx = 583 < 1024 the pair (j, i) is recoverable from the value
by divmod. That makes every value assertion in this file a direct assertion
about the byte offset that produced it: a wrong j or a wrong i cannot decode to
the right number, and a big-endian read cannot decode to a plausible one.

THE TRANSPORT IS ITSELF AN ASSERTION. `FakeSidecar` REFUSES a value read that
arrives without a `Range` header, and records the width of every range served.
`assert_only_four_byte_reads` is called by the read tests, so "the array never
enters the container" is enforced by the harness rather than merely reported by
the response. The 41-frame ladder is checked the same way: 41 requests, 41 x 4
bytes, one header, zero full-object reads.

LIVE RECEIPT (2026-09-03, against the proof object) is recorded at the tail of
this file in `test_live_receipt_pin`, which re-derives the cell, offset and range
string of the receipted click from the fixture geometry so the arithmetic behind
the receipt regress-guards even where the object is not reachable from CI.
"""

import asyncio
import json
import math
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import weather_point as wp


FIXTURES = Path(__file__).parent / "fixtures"
HEADER_FIXTURE = FIXTURES / "weather_sidecar_header_na3_2026090306_mslp_f006.json"

# The proof object's coordinates, spelled once.
RUN = "20260903T06Z"
RUN_ISO = "2026-09-03T06Z"
MODEL, CROP, PARAM, FHR = "gfs", "na3", "mslp", 6
VALUES_KEY = "weather/values/gfs/na3/20260903/06Z/mslp_f006.f32"
HEADER_KEY = "weather/values/gfs/na3/20260903/06Z/mslp_f006.json"
PNG_KEY = "weather/values/gfs/na3/20260903/06Z/mslp_f006.png"

NY, NX = 222, 583
LAT0, LON0, D = 14.75, -186.75, 0.25

# The one cell the fixture array leaves unwritten — the antimeridian strip.
# lat(200) = 64.75, lon(1) = -186.50, which is +173.50 on a wrapped map.
NAN_J, NAN_I = 200, 1


# ---------------------------------------------------------------------------
# The synthetic sidecar
# ---------------------------------------------------------------------------

def synthetic(j: int, i: int) -> float:
    """The invertible cell value: j*1024 + i, exact in float32 (< 2^24)."""
    return float(j * 1024 + i)


def unsynthetic(value: float) -> tuple[int, int]:
    j, i = divmod(int(round(value)), 1024)
    return j, i


def load_header_doc() -> dict:
    with open(HEADER_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class FakeSidecar:
    """An in-memory sidecar store over the (key, byte_range) surface the real
    `SidecarStore` speaks to the network with.

    Two refusals are the point of it:
      * a value read WITHOUT a Range header raises — nothing in this lane is
        allowed to ask for a whole array, so asking is a test failure, not a
        slow path;
      * `missing` keys answer 404 so the fail-loud bodies can be exercised.
    """

    def __init__(self, *, present=(VALUES_KEY, HEADER_KEY), png=False,
                 header_doc=None, ignore_range_on=()):
        self.present = set(present)
        self.png = png
        self.header_doc = header_doc if header_doc is not None else load_header_doc()
        # Keys for which the store pretends to be a CDN that drops Range and
        # returns 200 + the whole object — the misbehaviour the reader must
        # refuse rather than absorb.
        self.ignore_range_on = set(ignore_range_on)
        self.calls: list[tuple[str, str | None]] = []
        self.range_widths: list[int] = []

    # -- the transport the store is constructed with -------------------------

    async def __call__(self, key: str, byte_range):
        self.calls.append((key, byte_range))

        if key.endswith(".png"):
            return (206, b"\x89") if self.png else (404, b"")

        if key.endswith(".json"):
            if key not in self.present:
                return 404, b""
            return 200, json.dumps(self.header_doc).encode()

        if key not in self.present:
            return 404, b""

        if byte_range is None:
            raise AssertionError(
                f"a value read reached the store with no Range header: {key} "
                "— the array must never enter the container")
        start, end = (int(x) for x in byte_range.split("=", 1)[1].split("-"))
        width = end - start + 1
        self.range_widths.append(width)
        if key in self.ignore_range_on:
            return 200, self.whole_object()
        return 206, self.slice_(start, width)

    # -- the bytes -----------------------------------------------------------

    def cell_bytes(self, j: int, i: int) -> bytes:
        if (j, i) == (NAN_J, NAN_I):
            return struct.pack("<f", float("nan"))
        return struct.pack("<f", synthetic(j, i))

    def slice_(self, start: int, width: int) -> bytes:
        """Serve an arbitrary byte window without materialising the array —
        which is also how the real object behaves, and keeps this fake honest
        about the thing it is standing in for."""
        out = bytearray()
        pos = start
        while len(out) < width:
            j, i = divmod(pos // 4, NX)
            within = pos % 4
            cell = self.cell_bytes(j, i) if j < NY else b"\x00\x00\x00\x00"
            take = min(4 - within, width - len(out))
            out += cell[within:within + take]
            pos += take
        return bytes(out)

    def whole_object(self) -> bytes:
        return self.slice_(0, NY * NX * 4)

    # -- assertions ----------------------------------------------------------

    def value_calls(self):
        return [c for c in self.calls if c[0].endswith(".f32")]

    def header_calls(self):
        return [c for c in self.calls if c[0].endswith(".json")]

    def assert_only_four_byte_reads(self):
        assert self.range_widths, "no range read was made at all"
        assert set(self.range_widths) == {4}, (
            f"a read wider than 4 bytes happened: {sorted(set(self.range_widths))}")


@pytest.fixture
def fake():
    return FakeSidecar()


@pytest.fixture
def client(monkeypatch, fake):
    store = wp.SidecarStore(transport=fake)
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)
    return TestClient(main.app)


def hdr() -> wp.SidecarHeader:
    return wp.SidecarHeader(load_header_doc(), key=HEADER_KEY)


def q(**over) -> dict:
    base = {"lat": 36.75, "lon": -119.75, "param": PARAM, "run": RUN,
            "fhr": FHR, "model": MODEL, "crop": CROP}
    base.update(over)
    return base


# ═══════════════════════════════════════════════════════════════════════════
# 1. The header — the fixture IS the contract
# ═══════════════════════════════════════════════════════════════════════════

def test_header_parses_the_live_geometry():
    h = hdr()
    assert [h.ny, h.nx] == [NY, NX]
    assert (h.lat0, h.lon0, h.dlat, h.dlon) == (LAT0, LON0, D, D)
    assert h.lat_order == "ascending"
    assert h.lon_convention == "west_negative_monotonic"
    assert h.dtype == "float32" and h.endianness == "little"
    assert h.units == "Pa"
    # Nothing was inferred: the live header states both axis conventions.
    assert h.inferred == []


def test_header_bounds_are_the_na3_crop():
    h = hdr()
    assert h.lat_bounds == pytest.approx((14.75, 70.0))
    assert h.lon_bounds == pytest.approx((-186.75, -41.25))
    assert h.expected_bytes == 222 * 583 * 4 == 517704


def test_header_incomplete_names_the_missing_field_and_what_it_had():
    doc = load_header_doc()
    del doc["lat0"]
    with pytest.raises(wp.PointError) as ei:
        wp.SidecarHeader(doc, key=HEADER_KEY)
    assert ei.value.status == 502
    assert ei.value.detail["missing"] == ["lat0"]
    assert ei.value.detail["key"] == HEADER_KEY
    assert "lon0" in ei.value.detail["present"]


def test_header_axis_conventions_are_inferred_and_the_inference_is_declared():
    """Absent lat_order/lon_convention are recoverable from the numbers, so they
    are recovered — and listed in `inferred`, which rides on the response. A
    default nobody can see is the thing this guards against."""
    doc = load_header_doc()
    del doc["lat_order"]
    del doc["lon_convention"]
    h = wp.SidecarHeader(doc, key=HEADER_KEY)
    assert h.lat_order == "ascending"          # dlat > 0
    assert h.lon_convention == "west_negative_monotonic"   # lon0 < 0
    assert sorted(h.inferred) == ["lat_order", "lon_convention"]
    assert sorted(h.axes()["inferred"]) == ["lat_order", "lon_convention"]


def test_header_refuses_a_dtype_it_cannot_decode():
    doc = load_header_doc()
    doc["dtype"] = "float64"
    with pytest.raises(wp.PointError) as ei:
        wp.SidecarHeader(doc, key=HEADER_KEY)
    assert ei.value.status == 502
    assert ei.value.detail["dtype"] == "float64"


def test_descending_lat_order_flips_the_row_axis():
    """The other lat_order the estate could hand us. Row 0 becomes the NORTHern
    edge and lat(j) walks down; the bounds are the same span either way."""
    doc = load_header_doc()
    doc["lat0"] = 70.0
    doc["lat_order"] = "descending"
    h = wp.SidecarHeader(doc, key=HEADER_KEY)
    assert h.lat_step == -0.25
    assert h.lat_at(0) == 70.0
    assert h.lat_at(NY - 1) == pytest.approx(14.75)
    assert h.lat_bounds == pytest.approx((14.75, 70.0))
    assert wp.locate(70.0, -119.75, h)["j"] == 0
    assert wp.locate(14.75, -119.75, h)["j"] == NY - 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. The geometry — four corners, the centre, the antimeridian, the box
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("lat,lon,j,i", [
    (14.75, -186.75, 0, 0),               # SW corner of the array
    (14.75, -41.25, 0, NX - 1),           # SE
    (70.00, -186.75, NY - 1, 0),          # NW
    (70.00, -41.25, NY - 1, NX - 1),      # NE
])
def test_four_corners_land_on_the_corner_cells(lat, lon, j, i):
    cell = wp.locate(lat, lon, hdr())
    assert (cell["j"], cell["i"]) == (j, i)
    assert cell["distance_km"] == pytest.approx(0.0, abs=1e-6)


def test_centre_cell():
    h = hdr()
    j, i = NY // 2, NX // 2
    cell = wp.locate(h.lat_at(j), h.lon_at(i), h)
    assert (cell["j"], cell["i"]) == (j, i)


def test_nearest_cell_rounds_and_reports_the_miss():
    """Fresno at (36.74, -119.79) — the spec's own worked example. The cell is
    the nearest CENTRE, and distance_km states how far off it is."""
    cell = wp.locate(36.74, -119.79, hdr())
    assert cell["lat"] == pytest.approx(36.75)
    assert cell["lon"] == pytest.approx(-119.75)
    assert (cell["j"], cell["i"]) == (88, 268)
    assert 0 < cell["distance_km"] < 5


def test_antimeridian_click_arrives_wrapped_and_is_moved_onto_the_axis():
    """A click in the western Aleutians arrives as +173.50 (no map hands you
    -186.50). The axis runs past -180 precisely so that column exists; the
    normalisation is the single 360-degree shift that finds it."""
    h = hdr()
    assert wp.normalize_lon(173.50, h) == pytest.approx(-186.50)
    cell = wp.locate(64.75, 173.50, h)
    assert (cell["j"], cell["i"]) == (NAN_J, NAN_I)
    assert cell["lon"] == pytest.approx(173.50)      # reported back wrapped
    assert cell["lon_grid"] == pytest.approx(-186.50)  # and on the axis
    assert cell["distance_km"] == pytest.approx(0.0, abs=1e-6)


def test_wrap180_is_total():
    assert wp.wrap180(180.0) == 180.0
    assert wp.wrap180(-180.0) == 180.0
    assert wp.wrap180(360.0) == 0.0
    assert wp.wrap180(-186.5) == pytest.approx(173.5)
    assert wp.wrap180(540.0) == 180.0


@pytest.mark.parametrize("lat,lon,axis", [
    (5.0, -119.75, "lat"),        # south of the crop (Panama)
    (80.0, -119.75, "lat"),       # north of it
    (36.75, 10.0, "lon"),         # Tunisia — off the axis every way round
    (36.75, -20.0, "lon"),        # mid-Atlantic, east of -41
])
def test_outside_the_box_is_a_404_that_states_the_bounds(lat, lon, axis):
    with pytest.raises(wp.PointError) as ei:
        wp.locate(lat, lon, hdr(), crop=CROP)
    d = ei.value.detail
    assert ei.value.status == 404
    assert d["error"] == "point is outside the crop"
    assert d["crop"] == CROP
    assert axis in d["outside"]
    assert d["bounds"]["lat_min"] == pytest.approx(14.75)
    assert d["bounds"]["lat_max"] == pytest.approx(70.0)
    assert d["bounds"]["lon_min"] == pytest.approx(-186.75)
    assert d["bounds"]["lon_max"] == pytest.approx(-41.25)


def test_the_box_is_the_footprint_not_the_centres():
    """Half a cell of tolerance at each edge: the grid COVERS the ground the
    edge cells stand on. 14.63 is inside (rounds to row 0); 14.62 is not."""
    h = hdr()
    assert wp.locate(14.63, -119.75, h)["j"] == 0
    with pytest.raises(wp.PointError):
        wp.locate(14.62, -119.75, h)
    assert wp.locate(70.12, -119.75, h)["j"] == NY - 1
    with pytest.raises(wp.PointError):
        wp.locate(70.13, -119.75, h)


def test_out_of_box_never_clamps_to_an_edge_value():
    """The failure mode this whole 404 exists to prevent: a point in the Gulf
    of Guinea must NOT be answered with the crop's eastern edge column."""
    with pytest.raises(wp.PointError) as ei:
        wp.locate(5.0, 0.0, hdr())
    assert "lat" in ei.value.detail["outside"]
    assert "lon" in ei.value.detail["outside"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. The four bytes — offset, range string, little-endian decode
# ═══════════════════════════════════════════════════════════════════════════

def test_byte_offset_is_row_major():
    h = hdr()
    assert wp.byte_offset(0, 0, h) == 0
    assert wp.byte_offset(0, 1, h) == 4
    assert wp.byte_offset(1, 0, h) == 4 * NX == 2332
    assert wp.byte_offset(88, 268, h) == 4 * (88 * NX + 268) == 206_288
    # The last cell's four bytes are the object's last four.
    assert wp.byte_offset(NY - 1, NX - 1, h) == h.expected_bytes - 4


def test_range_string_is_inclusive_at_both_ends():
    """`bytes=N-N+3`, not N+4. An off-by-one here reads three bytes of one float
    and one of the next, and struct decodes the result without complaint."""
    assert wp.range_header(0) == "bytes=0-3"
    assert wp.range_header(206_288) == "bytes=206288-206291"
    start, end = (int(x) for x in wp.range_header(12).split("=")[1].split("-"))
    assert end - start + 1 == 4


def test_decode_is_little_endian():
    raw = struct.pack("<f", 101325.0)
    assert wp.decode_value(raw) == pytest.approx(101325.0)
    # The same bytes read big-endian are a wildly different, plausible-looking
    # number — which is why the endianness is asserted and not assumed.
    assert struct.unpack(">f", raw)[0] != pytest.approx(101325.0)


def test_decode_rejects_a_short_read():
    with pytest.raises(wp.PointError) as ei:
        wp.decode_value(b"\x00\x00\x00", key=VALUES_KEY, offset=8)
    assert ei.value.status == 502
    assert ei.value.detail["got_bytes"] == 3
    assert ei.value.detail["key"] == VALUES_KEY


def test_decode_maps_nan_and_inf_to_none():
    assert wp.decode_value(struct.pack("<f", float("nan"))) is None
    assert wp.decode_value(struct.pack("<f", float("inf"))) is None
    assert wp.decode_value(struct.pack("<f", 0.0)) == 0.0   # zero is a VALUE


# ═══════════════════════════════════════════════════════════════════════════
# 4. The single-point route
# ═══════════════════════════════════════════════════════════════════════════

def test_point_reads_exactly_four_bytes_and_says_so(client, fake):
    r = client.get("/api/weather/point", params=q())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["source"]["bytes_read"] == 4
    assert body["source"]["requests"] == 1
    assert body["source"]["key"] == VALUES_KEY
    assert body["source"]["range"] == "bytes=206288-206291"
    fake.assert_only_four_byte_reads()
    assert len(fake.value_calls()) == 1


def test_point_value_decodes_to_the_cell_it_claims(client):
    """The synthetic array is invertible, so the value PROVES the offset: a
    wrong j or i cannot decode to j*1024+i for the cell reported."""
    body = client.get("/api/weather/point", params=q()).json()
    j, i = body["cell"]["j"], body["cell"]["i"]
    assert unsynthetic(body["value"]) == (j, i)
    assert (j, i) == (88, 268)


def test_point_envelope_carries_the_spec_shape(client):
    body = client.get("/api/weather/point", params=q()).json()
    assert body["param"] == PARAM
    assert body["model"] == MODEL
    assert body["crop"] == CROP
    assert body["run"] == RUN_ISO
    assert body["fhr"] == 6
    assert body["valid"] == "2026-09-03T12:00:00Z"
    assert body["point"] == {"lat": 36.75, "lon": -119.75}
    assert set(body["cell"]) == {"lat", "lon", "lon_grid", "j", "i",
                                 "distance_km"}
    assert body["units"] == "Pa"
    assert body["header"]["key"] == HEADER_KEY
    assert body["header"]["shape"] == [NY, NX]


def test_point_states_verified_false_and_why(client):
    """The sha256 is over the whole object; a four-byte read cannot check it.
    Echoing the digest while claiming verification would be the exact lie the
    field exists to prevent."""
    body = client.get("/api/weather/point", params=q()).json()
    assert body["verified"] is False
    assert body["header"]["verified"] is False
    assert body["header"]["sha256"] == load_header_doc()["sha256"]
    assert "range read cannot check it" in body["header"]["verified_reason"]


def test_point_display_converts_pascals_and_names_the_conversion(client):
    body = client.get("/api/weather/point", params=q()).json()
    assert body["display"]["units"] == "hPa"
    assert body["display"]["method"] == "pa_to_hpa"
    assert body["display"]["value"] == pytest.approx(body["value"] / 100.0)


def test_nan_cell_is_a_200_with_a_reason_not_an_error(client, fake):
    """The antimeridian strip. `value: null` + `reason: nodata` — a fact about
    the grid, not a failure of the request, and never 0.0."""
    r = client.get("/api/weather/point",
                   params=q(lat=64.75, lon=173.50))
    assert r.status_code == 200
    body = r.json()
    assert body["value"] is None
    assert body["reason"] == "nodata"
    assert body["display"]["value"] is None
    assert body["cell"]["j"] == NAN_J and body["cell"]["i"] == NAN_I
    assert body["source"]["bytes_read"] == 4
    fake.assert_only_four_byte_reads()


def test_point_outside_the_crop_is_404_with_bounds(client, fake):
    r = client.get("/api/weather/point", params=q(lat=5.0, lon=-119.75))
    assert r.status_code == 404
    d = r.json()["detail"]
    assert d["error"] == "point is outside the crop"
    assert d["bounds"]["lat_min"] == pytest.approx(14.75)
    # And nothing was read: the refusal happens before any byte request.
    assert fake.value_calls() == []


def test_missing_sidecar_404_names_both_keys_and_the_png_probe(monkeypatch):
    """'Not written' vs 'not rendered' — the two causes need different people,
    so the body distinguishes them and states the key it probed to do it."""
    fake = FakeSidecar(present=())          # nothing at all exists
    store = wp.SidecarStore(transport=fake)
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)
    r = TestClient(main.app).get("/api/weather/point", params=q())

    assert r.status_code == 404
    d = r.json()["detail"]
    assert d["error"] == "sidecar not found"
    assert d["expected"] == {"header": HEADER_KEY, "values": VALUES_KEY}
    assert d["png"] == {"key": PNG_KEY, "exists": False, "probed": True}
    assert d["diagnosis"] == "no frame for this (model, crop, run, param, fhr)"


def test_rendered_but_unwritten_sidecar_says_so(monkeypatch):
    fake = FakeSidecar(present=(), png=True)
    store = wp.SidecarStore(transport=fake)
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)
    r = TestClient(main.app).get("/api/weather/point", params=q())

    d = r.json()["detail"]
    assert d["png"]["exists"] is True
    assert d["diagnosis"] == (
        "frame rendered but the value sidecar was not written")


def test_a_store_that_ignores_range_is_refused_not_absorbed(monkeypatch):
    """A CDN that drops `Range` and answers 200 with all 517 KB. Accepting it
    would put the array in this container and make `bytes_read: 4` a lie on the
    very response reporting it — so it is a 502 that names the hint."""
    fake = FakeSidecar(ignore_range_on=(VALUES_KEY,))
    store = wp.SidecarStore(transport=fake)
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)
    r = TestClient(main.app).get("/api/weather/point", params=q())

    assert r.status_code == 502
    d = r.json()["detail"]
    assert d["error"] == "range read was not honoured"
    assert d["got_bytes"] == 517704 and d["expected_bytes"] == 4


def test_unconfigured_store_is_503_not_a_crash(monkeypatch):
    store = wp.SidecarStore()               # no base url, no credentials
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)
    r = TestClient(main.app).get("/api/weather/point", params=q())
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


@pytest.mark.parametrize("bad", [
    {"model": "../d2"}, {"crop": "na3/../.."}, {"param": "mslp;rm"},
    {"param": "MSLP"}, {"model": "gfs feed"},
])
def test_a_hostile_slug_never_reaches_the_store(client, fake, bad):
    r = client.get("/api/weather/point", params=q(**bad))
    assert r.status_code == 400
    assert fake.calls == []


@pytest.mark.parametrize("run,ok", [
    ("20260903T06Z", True), ("2026-09-03T06Z", True), ("2026090306", True),
    ("20260903T07Z", False),          # not a synoptic cycle
    ("20260932T06Z", False),          # not a date
    ("nonsense", False),
])
def test_run_spellings(run, ok):
    if ok:
        assert wp.format_run(wp.parse_run(run)) == RUN_ISO
    else:
        with pytest.raises(wp.PointError) as ei:
            wp.parse_run(run)
        assert ei.value.status == 400


def test_header_is_cached_forever_by_key(client, fake):
    """Immutable per frame, so there is no TTL to get wrong — and a second
    click on the same frame costs one range read, not two requests."""
    for _ in range(3):
        client.get("/api/weather/point", params=q())
    assert len(fake.header_calls()) == 1


def test_repeat_click_on_the_same_cell_is_served_from_the_value_lru(client, fake):
    client.get("/api/weather/point", params=q())
    client.get("/api/weather/point", params=q())
    assert len(fake.value_calls()) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. The ladder — 41 range GETs, one header, zero full-object reads
# ═══════════════════════════════════════════════════════════════════════════

def test_ladder_fhrs_are_41_frames():
    fhrs = wp.ladder_fhrs()
    assert len(fhrs) == 41
    assert fhrs[0] == 0 and fhrs[-1] == 240
    assert all(f % 6 == 0 for f in fhrs)


def test_ladder_refuses_a_fan_out_wider_than_the_cap():
    with pytest.raises(wp.PointError) as ei:
        wp.ladder_fhrs(step=1, fhr_max=240)
    assert ei.value.status == 400
    assert ei.value.detail["frames"] == 241


@pytest.fixture
def ladder_fake():
    keys = ["weather/values/gfs/na3/20260903/06Z/mslp_f%03d.f32" % f
            for f in wp.ladder_fhrs()]
    keys += [k.replace(".f32", ".json") for k in keys]
    return FakeSidecar(present=keys)


@pytest.fixture
def ladder_client(monkeypatch, ladder_fake):
    store = wp.SidecarStore(transport=ladder_fake)
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)
    return TestClient(main.app)


def test_ladder_is_41_four_byte_reads_and_one_header(ladder_client, ladder_fake):
    r = ladder_client.get("/api/weather/point/ladder",
                          params={"lat": 36.74, "lon": -119.79,
                                  "param": PARAM, "run": RUN})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["count"] == 41
    assert len(body["values"]) == 41
    assert len(ladder_fake.value_calls()) == 41
    assert len(ladder_fake.header_calls()) == 1
    ladder_fake.assert_only_four_byte_reads()

    src = body["source"]
    assert src["bytes_per_point"] == 4
    assert src["bytes_read"] == 41 * 4 == 164
    assert src["requests"] == 41
    assert src["header_requests"] == 1
    assert src["full_object_reads"] == 0
    assert src["concurrency"] == 8
    # 164 bytes, against 41 x 517,704 for the same answer read whole.
    assert src["bytes_read"] * 129_000 < 41 * 517_704


def test_ladder_rows_carry_their_own_key_and_valid_time(ladder_client):
    body = ladder_client.get("/api/weather/point/ladder",
                             params={"lat": 36.74, "lon": -119.79,
                                     "param": PARAM, "run": RUN}).json()
    first, last = body["values"][0], body["values"][-1]
    assert first["fhr"] == 0
    assert first["key"] == "weather/values/gfs/na3/20260903/06Z/mslp_f000.f32"
    assert first["valid"] == "2026-09-03T06:00:00Z"
    assert last["fhr"] == 240
    assert last["key"] == "weather/values/gfs/na3/20260903/06Z/mslp_f240.f32"
    assert last["valid"] == "2026-09-13T06:00:00Z"
    assert all(row["bytes_read"] == 4 for row in body["values"])


def test_ladder_reads_one_cell_across_every_frame(ladder_client):
    """One offset, 41 frames. The synthetic array is the same in every frame, so
    every rung must decode to the SAME (j, i) — which is the assertion that the
    offset was computed once from one header and reused, not recomputed per
    frame against something that drifted."""
    body = ladder_client.get("/api/weather/point/ladder",
                             params={"lat": 36.74, "lon": -119.79,
                                     "param": PARAM, "run": RUN}).json()
    cells = {unsynthetic(row["value"]) for row in body["values"]}
    assert cells == {(88, 268)}


def test_ladder_survives_one_unwritten_frame(monkeypatch):
    """A run is written forward; a missing rung must cost that rung, not the
    ladder. The absent frame carries its own error on its own row."""
    keys = ["weather/values/gfs/na3/20260903/06Z/mslp_f%03d.f32" % f
            for f in wp.ladder_fhrs()]
    keys += [k.replace(".f32", ".json") for k in keys]
    gone = "weather/values/gfs/na3/20260903/06Z/mslp_f120.f32"
    fake = FakeSidecar(present=[k for k in keys if k != gone])
    store = wp.SidecarStore(transport=fake)
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)

    body = TestClient(main.app).get(
        "/api/weather/point/ladder",
        params={"lat": 36.74, "lon": -119.79, "param": PARAM, "run": RUN}).json()

    rows = {row["fhr"]: row for row in body["values"]}
    assert rows[120]["available"] is False
    assert rows[120]["value"] is None
    assert rows[120]["error"]["key"] == gone
    assert rows[120]["bytes_read"] == 0
    assert rows[126]["available"] is True
    assert body["source"]["bytes_read"] == 40 * 4


def test_ladder_takes_the_header_from_the_first_frame_that_has_one(monkeypatch):
    """f000 absent is a real state (the writer works forward), and it must not
    cost the ladder its geometry."""
    keys = ["weather/values/gfs/na3/20260903/06Z/mslp_f%03d.f32" % f
            for f in wp.ladder_fhrs()]
    keys += [k.replace(".f32", ".json") for k in keys]
    keys = [k for k in keys
            if not k.startswith("weather/values/gfs/na3/20260903/06Z/mslp_f000")]
    fake = FakeSidecar(present=keys)
    store = wp.SidecarStore(transport=fake)
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)

    body = TestClient(main.app).get(
        "/api/weather/point/ladder",
        params={"lat": 36.74, "lon": -119.79, "param": PARAM, "run": RUN}).json()

    assert body["header"]["key"] == HEADER_KEY      # f006's
    assert body["source"]["header_requests"] == 2   # f000 missed, f006 hit
    assert body["values"][0]["available"] is False  # f000's VALUES still absent


def test_ladder_outside_the_crop_reads_nothing(ladder_client, ladder_fake):
    r = ladder_client.get("/api/weather/point/ladder",
                          params={"lat": 5.0, "lon": -119.79,
                                  "param": PARAM, "run": RUN})
    assert r.status_code == 404
    assert ladder_fake.value_calls() == []


def test_ladder_step_and_max_are_overridable(ladder_client):
    body = ladder_client.get("/api/weather/point/ladder",
                             params={"lat": 36.74, "lon": -119.79,
                                     "param": PARAM, "run": RUN,
                                     "fhr_step": 24, "fhr_max": 240}).json()
    assert body["count"] == 11
    assert body["source"]["bytes_read"] == 44


def test_ladder_concurrency_is_bounded_at_eight():
    """Spec §2.4. Proven by counting how many reads are in flight at once, not
    by reading the constant back."""
    inflight = {"now": 0, "peak": 0}

    async def transport(key, byte_range):
        if key.endswith(".json"):
            return 200, json.dumps(load_header_doc()).encode()
        inflight["now"] += 1
        inflight["peak"] = max(inflight["peak"], inflight["now"])
        await asyncio.sleep(0)
        inflight["now"] -= 1
        return 206, struct.pack("<f", 1.0)

    store = wp.SidecarStore(transport=transport)
    pairs = [(f"k{n}.f32", n * 4) for n in range(41)]
    out = asyncio.run(store.get_values(pairs))
    assert len(out) == 41
    assert inflight["peak"] <= wp.LADDER_CONCURRENCY == 8


# ═══════════════════════════════════════════════════════════════════════════
# 6. Units, the chain stub, the signer
# ═══════════════════════════════════════════════════════════════════════════

def test_kelvin_anomaly_converts_by_identity_not_by_273():
    """The one conversion in the table that is a trap: an ANOMALY in kelvin is a
    difference, and a +3.12 K anomaly is +3.12 °C, not -270.03 °C."""
    d = wp.display_value(3.12, "K", "t2m_anom")
    assert d == {"value": 3.12, "units": "°C", "method": "delta_identity"}


def test_absolute_kelvin_does_subtract_273():
    d = wp.display_value(300.0, "K", "t2m")
    assert d["units"] == "°C" and d["value"] == pytest.approx(26.85)
    assert d["method"] == "kelvin_to_celsius"


def test_unknown_units_pass_through_unconverted():
    d = wp.display_value(5.0, "kg m-2", "apcp")
    assert d == {"value": 5.0, "units": "kg m-2", "method": "identity"}


@pytest.fixture
def anom_client(monkeypatch):
    """A sidecar for `t2m_anom` — same na3 geometry, kelvin units. The chain's
    first rung is a temperature rung, so it needs a temperature sidecar."""
    doc = load_header_doc()
    doc.update({"param": "t2m_anom", "units": "K"})
    stem = "weather/values/gfs/na3/20260903/06Z/t2m_anom_f006"
    fake = FakeSidecar(present=(stem + ".f32", stem + ".json"), header_doc=doc)
    store = wp.SidecarStore(transport=fake)
    monkeypatch.setattr(main, "_get_weather_store", lambda: store)
    return TestClient(main.app)


def test_chain_stub_has_all_four_rungs_with_reasons(anom_client):
    """Charter §7: the SHAPE exists on day one so the panel can render the rungs
    as they light up. A dark rung carries a reason, never an empty object."""
    body = anom_client.get("/api/weather/point",
                           params=q(param="t2m_anom", chain=1)).json()
    chain = body["chain"]
    assert chain["rungs"] == ["temperature", "degree_day", "load", "lmp"]
    assert chain["filled"] == ["temperature"]
    assert chain["chain"]["temperature"]["available"] is True
    for rung in ("degree_day", "load", "lmp"):
        assert chain["chain"][rung]["available"] is False
        assert chain["chain"][rung]["reason"]


def test_chain_names_the_nearest_station_from_the_ruled_basket(anom_client):
    body = anom_client.get("/api/weather/point",
                           params=q(param="t2m_anom", chain=1)).json()
    station = body["chain"]["chain"]["degree_day"]["station"]
    assert station["icao"] == "KFAT"          # Fresno, nearest to (36.75,-119.75)
    assert station["distance_km"] < 20


def test_chain_is_absent_unless_asked_for(client):
    assert "chain" not in client.get("/api/weather/point", params=q()).json()


def test_nearest_station_is_pure_and_honest_about_distance():
    stations = [{"station_id": "A", "icao": "KAAA", "lat": 40.0, "lon": -100.0},
                {"station_id": "B", "icao": "KBBB", "lat": 41.0, "lon": -100.0}]
    got = wp.nearest_station(40.2, -100.0, stations)
    assert got["station_id"] == "A"
    assert got["distance_km"] == pytest.approx(22.2, abs=0.5)


def test_sigv4_signature_is_deterministic_and_pinned():
    """The signer is forty lines of stdlib standing in for a boto3 import, so it
    is pinned against fixed inputs — a silent change to the canonical request
    would otherwise only ever show up as a 403 in production."""
    import datetime
    sig = wp.sigv4_headers(
        method="GET", host="acct.r2.cloudflarestorage.com",
        path="/archive/weather/values/gfs/na3/20260903/06Z/mslp_f006.f32",
        region="auto", service="s3", access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        now=datetime.datetime(2026, 9, 3, 6, 0, 0, tzinfo=datetime.timezone.utc),
    )
    assert sig["x-amz-date"] == "20260903T060000Z"
    # The empty-body payload hash is a constant of SigV4 and must be exactly it.
    assert sig["x-amz-content-sha256"] == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in sig["Authorization"]
    assert ("Credential=AKIDEXAMPLE/20260903/auto/s3/aws4_request"
            in sig["Authorization"])
    # Stable across calls with the same clock.
    again = wp.sigv4_headers(
        method="GET", host="acct.r2.cloudflarestorage.com",
        path="/archive/weather/values/gfs/na3/20260903/06Z/mslp_f006.f32",
        region="auto", service="s3", access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        now=datetime.datetime(2026, 9, 3, 6, 0, 0, tzinfo=datetime.timezone.utc),
    )
    assert again == sig


# ═══════════════════════════════════════════════════════════════════════════
# 7. The key scheme and the live receipt
# ═══════════════════════════════════════════════════════════════════════════

def test_key_scheme_reproduces_the_proof_object():
    run_dt = wp.parse_run(RUN)
    assert wp.header_key(MODEL, CROP, run_dt, PARAM, FHR) == HEADER_KEY
    assert wp.value_key(MODEL, CROP, run_dt, PARAM, FHR) == VALUES_KEY
    assert wp.render_key(MODEL, CROP, run_dt, PARAM, FHR) == PNG_KEY
    assert HEADER_KEY.startswith(wp.VALUES_ROOT)


def test_fhr_is_always_three_digits():
    run_dt = wp.parse_run(RUN)
    assert wp.value_key(MODEL, CROP, run_dt, PARAM, 0).endswith("mslp_f000.f32")
    assert wp.value_key(MODEL, CROP, run_dt, PARAM, 240).endswith("mslp_f240.f32")


def test_live_receipt_pin():
    """LIVE RECEIPT — 2026-09-03, against the dispatched proof object
    `weather/values/gfs/na3/20260903/06Z/mslp_f006.json` + `.f32`.

    The click receipted in the handback is (36.74, -119.79) — Fresno. The
    arithmetic BEHIND that receipt is re-derived here from the pinned header so
    it regress-guards wherever the object itself is not reachable: the cell, the
    byte offset, the range string and the four-byte width are all properties of
    the header contract alone, and none of them may drift.
    """
    h = hdr()
    cell = wp.locate(36.74, -119.79, h, crop=CROP)
    assert (cell["j"], cell["i"]) == (88, 268)
    assert (cell["lat"], cell["lon"]) == (pytest.approx(36.75),
                                          pytest.approx(-119.75))
    assert cell["distance_km"] == pytest.approx(3.733, abs=0.01)

    offset = wp.byte_offset(cell["j"], cell["i"], h)
    assert offset == 206_288
    assert wp.range_header(offset) == "bytes=206288-206291"
    assert offset + 4 <= h.expected_bytes == 517_704


def test_the_store_keeps_one_pooled_client_not_one_per_read():
    """MEASURED (local origin, 41 frames): 2.39 s when every range GET built
    its own client; 68 ms cold and 3 ms warm sharing one. The four bytes are the
    cheap part — the connection was the whole cost — so the client is
    process-wide, and this pins it."""
    store = wp.SidecarStore(base_url="https://example.invalid")
    first = store._get_client()
    assert store._get_client() is first
    asyncio.run(store.aclose())
    assert store._client is None
    # A client stranded by a closed loop is rebuilt, not raised on.
    second = store._get_client()
    assert second is not first
    asyncio.run(store.aclose())
