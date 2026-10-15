"""d091741 — the Tropics index routes: /ground, /products, /text, /members (D-09-25-226).

  G1  /ground on recorded GIBS answers (the recon's z3 runs, this lane's z0 runs,
      the 200 and 404 tile fixtures): the newest PRESENT slot, never `default`,
      age computed; no_frame_in_window after 18 misses; gibs_unreachable; the memo
      answers a second call without a probe; East for al, West for ep and cp; each
      probe logged; the attribution sentence is GIBS's published one, pinned
  I1  /products on the ledger slice + the constructed display rows (built from the
      recon's parsed fixtures to recon §7's keys: d091740 had not landed): objects,
      shas and vertices equal the fixture; Rachel 058 ww not_issued; Simon 032
      surge_ww basin_has_none; banked-not-published -> not_published; unknown
      storm and unknown advisory -> 404
  I2  a corrected advisory: the copy in force is revision 1; revision and the
      original's url are both stated
  T1  /text: section kinds and both times from the fixture parse; not_banked where
      the bank holds no text; not_issued for an intermediate's TCD
  M1  /members: not_published for every banked cycle today, not_banked for Trudy;
      the /tracks not_served wording points here; a published manifest is served
      and a too-small one is too_few_members (constructed)
  X1  the plan receipts: on Neon (docs/receipts/tropics-api-index-d091741/
      neon_plans.txt) and locally at Neon's ledger size, no read seq-scans
      tropical_file_vintage, every ledger read walks tfv_identity
  C   every build sets the statement timeout first; the memo is single-flight
  V1  d091673's 11 vectors changed by the two not_served pointers and nothing else

The rehearsal (R) is tests/rehearse_tropics_index_d091741.py.
"""

import hashlib
import json
import logging
import pathlib
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import main
import tropics as tr
import load_bank_d091673 as lb0
import load_bank_d091741 as lb

try:
    import tropics_index as ti
except ImportError:                       # red on main: the module does not exist there
    ti = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = lb.FIX
P = "/api/weather/tropics"
RECEIPTS = ROOT / "docs" / "receipts" / "tropics-api-index-d091741"
INDEX_CACHES = ("_tropics_products_cache", "_tropics_text_cache", "_tropics_members_cache",
                "_tropics_ground_cache")


def _clear():
    for c in INDEX_CACHES:
        if hasattr(main, c):
            getattr(main, c).clear()


@pytest.fixture(autouse=True)
def _cold(monkeypatch):
    _clear()
    monkeypatch.setattr(main, "_utcnow", lambda: lb.NOW)
    yield
    _clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def _need_postgres():
    if lb.initdb_path() is None:
        pytest.skip("no local Postgres (initdb) on this box: apt install postgresql-16")
    pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def pg():
    """The ledger slice and the constructed display rows, padded to Neon's size."""
    _need_postgres()
    with lb.cluster(padded=True) as conn:
        yield conn


@pytest.fixture(scope="module")
def pg_c():
    """pg with the second layer: I2's correction, two manifests, a removed TCD."""
    _need_postgres()
    with lb.cluster(constructed=True, padded=True) as conn:
        yield conn


def get(client, monkeypatch, conn, path, status=200):
    _clear()
    pool = lb.PgPool(conn)
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path)
    assert r.status_code == status, (path, r.text[:500])
    return r.json()


def k1(fixture):
    return json.loads((FIX / "recon" / "recorded_k1.json").read_text())[fixture]


def t1(fixture):
    return json.loads((FIX / "recon" / "recorded_t1.json").read_text())[fixture]


def display(product, storm_id, vkey):
    rows = [r for r in lb.display_rows() if (r["product"], r["storm_id"], r["vintage_key"])
            == (product, storm_id, vkey)]
    assert len(rows) == 1, (product, storm_id, vkey)
    return rows[0]


def banked(product, storm_id, vkey):
    rows = [r for r in lb.rows("ledger") if (r["product"], r["storm_id"], r["vintage_key"])
            == (product, storm_id, vkey) and r["status"] == "banked"]
    return max(rows, key=lambda r: (r["fetch_ts"], r["vintage_id"])) if rows else None


def by_product(items):
    return {o["product"]: o for o in items}


# ═══════════════════════════════════════════════════════════════════════════
# F — the fixtures are what they say
# ═══════════════════════════════════════════════════════════════════════════

def test_F_every_pinned_fixture_file_has_its_sha():
    m = lb.MANIFEST
    for group in ("recon", "gibs", "attribution"):
        for path, meta in m[group].items():
            if meta.get("sha256") is None:
                continue
            assert hashlib.sha256((FIX / path).read_bytes()).hexdigest() == meta["sha256"], path
    for kind in lb.LOAD_ORDER:
        assert len(lb.rows(kind)) == m["bank"][kind]["rows"]           # rows() checks Neon's sha


def test_F_the_display_rows_are_the_generators(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("mdr", FIX / "make_display_rows.py")
    mdr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mdr)
    base = mdr.base_rows()
    assert base == lb.display_rows()
    assert mdr.constructed_rows(base) == lb.constructed_rows()


def test_F_no_display_row_is_in_the_banked_slice_and_each_names_a_banked_source():
    assert not [r for r in lb.rows("ledger") if r["product"].startswith("display:")]
    for r in lb.display_rows():
        src = [b for b in lb.rows("ledger") if b["sha256"] == r["meta"]["source_sha256"]]
        assert len(src) == 1 and src[0]["r2_key"] == r["meta"]["source_r2_key"], r["product"]


def test_F_the_filler_names_nothing_a_route_reads(pg):
    n = pg.execute("SELECT count(*) AS n FROM tropical_file_vintage").fetchone()["n"]
    assert n == lb.NEON_LEDGER_ROWS
    hit = pg.execute("""SELECT count(*) AS n FROM tropical_file_vintage WHERE vintage_id >= 800000
                          AND vintage_id < 900000 AND (storm_id IN (SELECT storm_id FROM tropical_storms)
                          OR product IN ('cone_kmz','ww_kmz','surge_ww_kml','tcd','tcp','storm_entry',
                                         'wsp_5km_zip','wsp_halfdeg_zip','tracks:ecmwf_ens',
                                         'tracks:ecmwf_aifs_ens','tracks:adeck')
                          OR product LIKE 'display:%%')""").fetchone()["n"]
    assert hit == 0


# ═══════════════════════════════════════════════════════════════════════════
# G1 — /ground on recorded GIBS answers
# ═══════════════════════════════════════════════════════════════════════════

TIME_IN_URL = re.compile(r"/default/([^/]+)/GoogleMapsCompatible_Level7/")


def recon_runs():
    return [json.loads(l) for l in (FIX / "gibs" / "recon_d091737_z3_runs.jsonl").read_text().splitlines()]


def z0_runs():
    return [json.loads(l) for l in (FIX / "gibs" / "d091741_z0_runs.jsonl").read_text().splitlines()]


class Recorded:
    """A GIBS that answers from a recording; an unrecorded slot is an error
    (never a guess at present or absent). Counts and keeps every URL asked."""

    def __init__(self, answers: dict, layer: str):
        self.answers, self.layer, self.urls = answers, layer, []

    async def __call__(self, url):
        self.urls.append(url)
        assert f"/{self.layer}/" in url, url
        t = TIME_IN_URL.search(url).group(1)
        if t not in self.answers:
            raise LookupError(f"unrecorded slot {t}")
        return self.answers[t]


def from_recon(run) -> Recorded:
    day = run["probed_utc"][:10]
    ans = {}
    for hhmm, v in run["slots"].items():
        ct = "image/png" if v["status"] == 200 else "text/html; charset=iso-8859-1"
        ans[f"{day}T{hhmm}:00Z"] = (v["status"], ct, None)
    return Recorded(ans, run["layer"])


def from_z0(run) -> Recorded:
    return Recorded({s["slot_utc"]: (s["status"], s["content_type"], s.get("layer_time_actual"))
                     for s in run["slots"]}, run["layer"])


def ground(client, monkeypatch, basin, fetch, now, status=200):
    monkeypatch.setattr(main, "_utcnow", lambda: now)
    monkeypatch.setattr(main, "_tropics_gibs_fetch", fetch)
    r = client.get(f"{P}/ground?basin={basin}")
    assert r.status_code == status, r.text
    return r.json()


def basin_of(layer):
    return "al" if layer.startswith("GOES-East") else "ep"


# The recon's own table (§1): newest present tile and its age, per probe and layer.
RECON_FRAMES = {("2026-10-15T13:02:35+00:00", "GOES-East_ABI_GeoColor"): ("12:30", 32.6),
                ("2026-10-15T13:02:35+00:00", "GOES-West_ABI_GeoColor"): ("12:30", 32.6),
                ("2026-10-15T13:21:51+00:00", "GOES-East_ABI_GeoColor"): ("12:40", 41.9),
                ("2026-10-15T13:21:51+00:00", "GOES-West_ABI_GeoColor"): ("12:50", 31.9),
                ("2026-10-15T13:27:22+00:00", "GOES-East_ABI_GeoColor"): ("12:40", 47.4),
                ("2026-10-15T13:27:22+00:00", "GOES-West_ABI_GeoColor"): ("12:50", 37.4)}


@pytest.mark.parametrize("i", range(6))
def test_G1_the_recons_runs_give_the_newest_present_slot_and_its_age(client, monkeypatch, i):
    run = recon_runs()[i]
    fetch = from_recon(run)
    now = datetime.fromisoformat(run["probed_utc"])
    b = ground(client, monkeypatch, basin_of(run["layer"]), fetch, now)
    hhmm, age = RECON_FRAMES[(run["probed_utc"], run["layer"])]
    assert run["newest_present_slot"] == hhmm                              # the recon's word
    (layer,) = b["layers"]
    assert layer["id"] == run["layer"] and b["absence"] is None
    assert layer["frame_utc"] == f"{run['probed_utc'][:10]}T{hhmm}:00+00:00"
    assert layer["age_min"] == age
    assert layer["probed_utc"] == now.isoformat()
    # recent_present: only slots the recording calls present; the unrecorded are unknown
    for s in layer["recent_present"]:
        assert run["slots"][s[11:16]]["status"] == 200
    assert len(layer["recent_present"]) <= 12


@pytest.mark.parametrize("i", range(len(z0_runs())))
def test_G1_this_lanes_z0_runs_give_the_first_present_slot_from_now(client, monkeypatch, i):
    run = z0_runs()[i]
    fetch = from_z0(run)
    now = datetime.fromisoformat(run["probed_utc"])
    b = ground(client, monkeypatch, basin_of(run["layer"]), fetch, now)
    first = next(s for s in run["slots"] if s["status"] == 200)
    (layer,) = b["layers"]
    assert layer["frame_utc"] == datetime.fromisoformat(first["slot_utc"].replace("Z", "+00:00")).isoformat()
    frame = datetime.fromisoformat(layer["frame_utc"])
    assert layer["age_min"] == round((now - frame).total_seconds() / 60, 1)
    # the frame and the 11 slot positions below it, the present ones only
    lowest = (frame - timedelta(minutes=110)).strftime("%Y-%m-%dT%H:%M:%SZ")
    present = [s["slot_utc"] for s in run["slots"] if s["status"] == 200]
    assert layer["recent_present"] == [s for s in present if lowest <= s <= first["slot_utc"]]
    absent_between = [s["slot_utc"] for s in run["slots"]
                      if s["status"] != 200 and s["slot_utc"] < first["slot_utc"]]
    assert not set(absent_between) & set(layer["recent_present"])          # a hole stays a hole


def test_G1_a_hole_below_the_frame_is_not_listed_present(client, monkeypatch):
    run = [r for r in z0_runs() if r["probed_utc"] == "2026-10-15T13:59:23+00:00"
           and r["layer"] == "GOES-East_ABI_GeoColor"][0]
    b = ground(client, monkeypatch, "al", from_z0(run), datetime.fromisoformat(run["probed_utc"]))
    layer = b["layers"][0]
    assert layer["frame_utc"] == "2026-10-15T13:30:00+00:00"              # 13:20 was 404, 13:30 not
    assert "2026-10-15T13:20:00Z" not in layer["recent_present"]
    assert layer["recent_present"][:2] == ["2026-10-15T13:30:00Z", "2026-10-15T13:10:00Z"]


def test_G1_never_default(client, monkeypatch):
    for run in recon_runs() + z0_runs():
        fetch = from_recon(run) if "default_is_slot" in run else from_z0(run)
        _clear()
        b = ground(client, monkeypatch, basin_of(run["layer"]), fetch,
                   datetime.fromisoformat(run["probed_utc"]))
        layer = b["layers"][0]
        for url in fetch.urls + [layer["template"]]:
            t = TIME_IN_URL.search(url).group(1)
            assert t != "default" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d0:00Z", t), url
        assert TIME_IN_URL.search(layer["template"]).group(1) == layer["frame_utc"].replace("+00:00", "Z")
        assert layer["time_template"].count("{Time}") == 1
        assert "default/default" not in json.dumps(b)


def test_G1_no_frame_in_window_after_18_misses(client, monkeypatch):
    class Never:
        urls = []

        async def __call__(self, url):
            self.urls.append(url)
            return 404, "text/html; charset=iso-8859-1", None
    f = Never()
    b = ground(client, monkeypatch, "ep", f, lb.NOW)
    assert b["layers"] == [] and b["absence"]["reason"] == "no_frame_in_window"
    assert len(f.urls) == 18
    assert "18 ten-minute slots probed" in b["absence"]["detail"]
    assert b["probe"]["frame_utc"] is None and b["probe"]["refusal"] == "no_frame_in_window"


def test_G1_the_404_fixture_is_absent_and_the_200_fixture_present():
    h200 = (FIX / "gibs" / "tile200.headers").read_text().lower()
    h404 = (FIX / "gibs" / "tile404.headers").read_text().lower()
    assert "content-type: image/png" in h200 and "layer-time-actual: 2026-10-15t13:00:00z" in h200
    assert "content-type: text/html" in h404
    assert (FIX / "gibs" / "tile200_east_z0_2026-10-15T1300Z.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert len((FIX / "gibs" / "tile404_east_z0_2027-01-01T0000Z.html").read_bytes()) == 196
    s = "2026-10-15T13:00:00Z"
    assert ti.classify(s, (200, "image/png", "2026-10-15T13:00:00Z")) == "present"
    assert ti.classify(s, (404, "text/html; charset=iso-8859-1", None)) == "absent"
    assert ti.classify(s, (200, "image/png", "2026-10-15T12:50:00Z")) == "absent"   # snapped
    assert ti.classify(s, (200, "text/html", None)) == "absent"
    assert ti.classify(s, (503, "text/html", None)) == "error"


def test_G1_gibs_unreachable(client, monkeypatch):
    async def down(url):
        raise ConnectionError("CONNECT tunnel failed")
    b = ground(client, monkeypatch, "al", down, lb.NOW)
    assert b["layers"] == [] and b["absence"]["reason"] == "gibs_unreachable"
    _clear()

    async def five(url):
        return 503, "text/html", None
    b = ground(client, monkeypatch, "al", five, lb.NOW)
    assert b["absence"]["reason"] == "gibs_unreachable" and "503" in b["absence"]["detail"]


def test_G1_the_memo_answers_a_second_call_without_a_probe(client, monkeypatch):
    run = z0_runs()[0]
    fetch = from_z0(run)
    now = datetime.fromisoformat(run["probed_utc"])
    first = ground(client, monkeypatch, "al", fetch, now)
    asked = len(fetch.urls)
    assert asked > 0 and first["cache"]["state"] == "miss"
    second = ground(client, monkeypatch, "al", fetch, now + timedelta(minutes=4))
    assert len(fetch.urls) == asked                                        # no probe
    assert second["cache"]["state"] == "fresh" and second["layers"] == first["layers"]
    main._tropics_ground_cache._entries["GOES-East_ABI_GeoColor"].built_mono -= 301
    ground(client, monkeypatch, "al", fetch, now)
    assert len(fetch.urls) > asked                                         # past 5 min: probed again


def test_G1_east_for_al_west_for_ep_and_cp(client, monkeypatch):
    east = [r for r in z0_runs() if r["layer"].startswith("GOES-East")][0]
    west = [r for r in z0_runs() if r["layer"].startswith("GOES-West")][0]
    fe, fw = from_z0(east), from_z0(west)                    # each asserts its layer in every URL
    now = datetime.fromisoformat(east["probed_utc"])
    a = ground(client, monkeypatch, "al", fe, now)
    e = ground(client, monkeypatch, "ep", fw, now)
    asked = len(fw.urls)
    c = ground(client, monkeypatch, "cp", fw, now)
    assert len(fw.urls) == asked                             # ep and cp share West's memo
    assert (a["basin"], a["layers"][0]["id"], a["layers"][0]["satellite"]) == (
        "al", "GOES-East_ABI_GeoColor", "GOES-East")
    assert (e["basin"], e["layers"][0]["id"]) == ("ep", "GOES-West_ABI_GeoColor")
    assert (c["basin"], c["layers"][0]["id"]) == ("cp", "GOES-West_ABI_GeoColor")


def test_G1_the_layer_is_the_maplibre_raster_the_recon_specified(client, monkeypatch):
    run = z0_runs()[0]
    b = ground(client, monkeypatch, "al", from_z0(run), datetime.fromisoformat(run["probed_utc"]))
    layer = b["layers"][0]
    assert (layer["tile_matrix_set"], layer["tile_size"], layer["minzoom"], layer["maxzoom"]) == (
        "GoogleMapsCompatible_Level7", 256, 0, 6)
    assert layer["template"].startswith(
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/")
    assert layer["template"].endswith("/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png")
    md = json.loads((FIX / "attribution" / "gibs_layer-metadata_GOES-East_ABI_GeoColor.json").read_text())
    assert layer["title"] == md["title"]


def test_G1_each_probe_is_logged(client, monkeypatch, caplog):
    run = z0_runs()[0]
    with caplog.at_level(logging.INFO, logger="energylake.tropics.ground"):
        b = ground(client, monkeypatch, "al", from_z0(run), datetime.fromisoformat(run["probed_utc"]))
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "energylake.tropics.ground"]
    assert len(lines) == 1
    assert lines[0] == b["probe"]
    assert (lines[0]["probed_utc"], lines[0]["frame_utc"], lines[0]["age_min"]) == (
        b["layers"][0]["probed_utc"], b["layers"][0]["frame_utc"], b["layers"][0]["age_min"])


def test_G1_the_service_path_probes_through_httpx_on_each_requests_own_loop(client, monkeypatch):
    """No recorded fetcher: main opens an httpx client per probe. TestClient runs
    each request on a fresh event loop, as a restarted worker would; a client
    kept across loops failed the second probe with 'Event loop is closed'
    (found live, 2026-10-15 14:20Z)."""
    import httpx
    runs = {r["layer"]: from_z0(r) for r in z0_runs()[-2:]}

    def handler(request):
        url = str(request.url)
        layer = "GOES-East_ABI_GeoColor" if "GOES-East" in url else "GOES-West_ABI_GeoColor"
        t = TIME_IN_URL.search(url).group(1)
        status, ct, actual = runs[layer].answers.get(t, (404, "text/html", None))
        headers = {"content-type": ct or "text/html"}
        if actual:
            headers["layer-time-actual"] = actual
        assert request.headers["user-agent"] == main.GIBS_USER_AGENT
        return httpx.Response(status, headers=headers, content=b"x")

    import asyncio

    class LoopBound(httpx.AsyncBaseTransport):
        """Bound to the loop of its first request, as httpcore's pooled
        connections are; another loop's request fails the way they do."""

        def __init__(self):
            self.loop = None

        async def handle_async_request(self, request):
            loop = asyncio.get_running_loop()
            if self.loop is None:
                self.loop = loop
            elif self.loop is not loop:
                raise RuntimeError("Event loop is closed")
            return handler(request)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=LoopBound(), **kw))
    monkeypatch.setattr(main, "_tropics_gibs_fetch", None)
    now = datetime.fromisoformat(z0_runs()[-1]["probed_utc"])
    monkeypatch.setattr(main, "_utcnow", lambda: now)
    for basin in ("al", "ep"):
        r = client.get(f"{P}/ground?basin={basin}")
        assert r.status_code == 200 and r.json()["absence"] is None, r.text


def test_G1_the_attribution_is_gibs_published_sentence():
    doc = (FIX / "attribution" / "gibs-api-docs_docs_index.md").read_text()
    m = re.search(r"^> _(.+)_$", doc, re.M)
    assert m and m.group(1) == ti.GIBS_ACKNOWLEDGEMENT
    assert "Data Use Guidance and Acknowledgements" in doc
    assert "afb15814aee74b09e672466adb731b2e34d51d8b" in ti.GIBS_ATTRIBUTION["source"]


def test_G1_bad_basin_is_400(client):
    for q in ("", "?basin=", "?basin=wp", "?basin=AL"):
        assert client.get(f"{P}/ground{q}").status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# I1 — /products
# ═══════════════════════════════════════════════════════════════════════════

ADVISORY_OBJECTS = [("display:cone", "ep202026", "032", "ep202026_adv032_cone_kmz.kmz"),
                    ("display:cone", "ep182026", "058", "ep182026_adv058_cone_kmz.kmz"),
                    ("display:ww", "ep182026", "057A", "ep182026_adv057A_ww_kmz.kmz"),
                    ("display:ww", "ep202026", "028A", "ep202026_adv028A_ww_kmz.kmz"),
                    ("display:ww", "al092026", "014A", "al092026_adv014A_ww_kmz.kmz"),
                    ("display:surge_ww", "al092026", "014A", "al092026_adv014A_surge_ww_kml.kml")]


@pytest.mark.parametrize("product,sid,adv,fixture", ADVISORY_OBJECTS,
                         ids=[f"{a[1]}-{a[2]}-{a[0]}" for a in ADVISORY_OBJECTS])
def test_I1_each_object_equals_the_fixture(client, monkeypatch, pg, product, sid, adv, fixture):
    b = get(client, monkeypatch, pg, f"{P}/products?storm_id={sid}&advisory={adv}")
    name = product.split(":", 1)[1]
    o = by_product(b["objects"])[name]
    row, rec = display(product, sid, adv), k1(fixture)
    layer = rec["layers"]["_"]
    assert o["url"] == f"https://display.energylake.io/tropics/{sid}/adv{adv}/{name}.geojson"
    assert (o["sha256"], o["bytes"], o["vertices"], o["geometry_types"]) == (
        layer["parsed_sha256"], layer["parsed_bytes"], layer["vertices"], layer["geometry_types"])
    assert o["sha256"] == row["sha256"]
    src = banked(rec["product"], sid, adv)
    assert (o["source_r2_key"], o["source_sha256"]) == (src["r2_key"], src["sha256"])
    assert o["source_sha256"] == rec["input_sha256"]
    assert (o["revision"], o["corrected"], o["original"], o["source_in_force"]) == (0, False, None, True)


def test_I1_rachel_058_ww_not_issued_on_nhcs_word(client, monkeypatch, pg):
    b = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep182026")
    assert (b["advisory"], b["advisory_requested"]) == ("058", False)          # the default
    assert b["init_ts"] == "2026-10-11T12:00:00+00:00" and b["issued_ts"] == "2026-10-11T15:00:00+00:00"
    a = by_product(b["absent"])
    assert a["ww"]["reason"] == "not_issued" and "links no ww_kmz" in a["ww"]["detail"]
    assert "ww_kmz" not in b["nhc_entry"]["refs"]
    assert a["surge_ww"]["reason"] == "basin_has_none"
    assert [o["product"] for o in b["objects"]] == ["cone"]
    assert by_product(b["objects"])["cone"]["vertices"] == 210


def test_I1_simon_032_surge_basin_has_none_and_ww_not_issued(client, monkeypatch, pg):
    b = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep202026&advisory=032")
    a = by_product(b["absent"])
    assert a["surge_ww"]["reason"] == "basin_has_none" and "'ep'" in a["surge_ww"]["detail"]
    assert a["ww"]["reason"] == "not_issued"
    assert by_product(b["objects"])["cone"]["vertices"] == 1260


def test_I1_the_wsp_cycle_is_the_synoptic_time(client, monkeypatch, pg):
    r = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep182026&advisory=058")
    s = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep202026&advisory=032")
    assert (r["wsp"]["cycle"], s["wsp"]["cycle"]) == ("2026101112", "2026101506")   # recon §3
    assert r["wsp"]["synoptic_ts"] == r["init_ts"]
    objs = by_product(r["wsp"]["objects"])
    assert sorted(objs) == ["wsp34_bands", "wsp34_grid", "wsp50_bands", "wsp50_grid",
                            "wsp64_bands", "wsp64_grid"]
    for kt in ("34", "50", "64"):
        bands, grid = k1("wsp_2026101112_wsp_5km_zip.zip"), k1("wsp_2026101112_wsp_halfdeg_zip.zip")
        assert objs[f"wsp{kt}_bands"]["vertices"] == bands["layers"][kt]["vertices"]
        assert objs[f"wsp{kt}_bands"]["sha256"] == bands["layers"][kt]["parsed_sha256"]
        assert objs[f"wsp{kt}_grid"]["vertices"] == grid["layers"][kt]["vertices"]
    sa = by_product(s["wsp"]["absent"])
    assert {k: v["reason"] for k, v in sa.items()} == {
        "wsp34_grid": "not_published", "wsp50_grid": "not_published", "wsp64_grid": "not_published"}


def test_I1_banked_not_published_is_not_published(client, monkeypatch, pg):
    b = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep202026&advisory=031")
    assert b["objects"] == []
    cone = by_product(b["absent"])["cone"]
    assert cone["reason"] == "not_published"
    assert banked("cone_kmz", "ep202026", "031")["sha256"] in cone["detail"]
    i = get(client, monkeypatch, pg, f"{P}/products?storm_id=al092026&advisory=014A")
    assert by_product(i["absent"])["cone"]["reason"] == "not_published"
    assert sorted(by_product(i["objects"])) == ["surge_ww", "ww"]
    assert all(a["reason"] == "not_published" for a in i["wsp"]["absent"])


def test_I1_an_earlier_advisorys_link_is_not_issued(client, monkeypatch, pg):
    """Isaias 013A's newest entry links 013's surge KML: NHC issued none with 013A.
    Isaias 015's newest entry links no ww (its earlier entries linked 014A's)."""
    b = get(client, monkeypatch, pg, f"{P}/products?storm_id=al092026&advisory=013A")
    surge = by_product(b["absent"])["surge_ww"]
    assert surge["reason"] == "not_issued" and "links advisory 013's surge_ww_kml" in surge["detail"]
    w = get(client, monkeypatch, pg, f"{P}/products?storm_id=al092026&advisory=015")
    ww = by_product(w["absent"])["ww"]
    assert ww["reason"] == "not_issued" and "links no ww_kmz" in ww["detail"]


def test_I1_the_default_is_nhcs_newest_advisory_text_only_included(client, monkeypatch, pg):
    b = get(client, monkeypatch, pg, f"{P}/products?storm_id=al092026")
    assert b["advisory"] == "020" and b["init_ts"] is None                 # post-tropical: text only
    assert {a["product"]: a["reason"] for a in b["absent"]} == {
        "cone": "not_issued", "ww": "not_issued", "surge_ww": "not_issued"}
    assert b["wsp"]["absence"]["reason"] == "no_synoptic_time"
    assert get(client, monkeypatch, pg, f"{P}/products?storm_id=ep202026")["advisory"] == "032"
    assert get(client, monkeypatch, pg, f"{P}/products?storm_id=ep212026")["advisory"] == "008"


def test_I1_unknown_storm_and_advisory_are_404_bad_params_400(client, monkeypatch, pg):
    get(client, monkeypatch, pg, f"{P}/products?storm_id=al992026", status=404)
    get(client, monkeypatch, pg, f"{P}/text?storm_id=al992026", status=404)
    get(client, monkeypatch, pg, f"{P}/members?storm_id=al992026", status=404)
    d = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep202026&advisory=099", status=404)
    assert "no advisory 099" in d["detail"]
    for q in ("storm_id=EP202026", "storm_id=ep202026&advisory=32", "storm_id=ep202026&advisory=032a",
              ""):
        assert TestClient(main.app).get(f"{P}/products?{q}").status_code == 400, q
    for q in ("storm_id=ep202026&cycle=2026101113", "storm_id=ep202026&cycle=20261011",
              "storm_id=ep202026&cycle=2026023112"):
        assert TestClient(main.app).get(f"{P}/members?{q}").status_code == 400, q


def test_I1_every_body_states_not_served_and_reads_no_r2(client, monkeypatch, pg):
    for path in (f"{P}/products?storm_id=ep202026", f"{P}/text?storm_id=ep202026",
                 f"{P}/members?storm_id=ep202026"):
        b = get(client, monkeypatch, pg, path)
        assert "object_bytes" in b["not_served"] and "reads no R2" in b["not_served"]["object_bytes"]
        text = json.dumps(b)
        for url in re.findall(r'"(?:url|manifest_url)": "([^"]+)"', text):
            assert url.startswith("https://display.energylake.io/tropics/"), url


# ═══════════════════════════════════════════════════════════════════════════
# I2 — a corrected advisory
# ═══════════════════════════════════════════════════════════════════════════

REVISION_READ_XFAIL = pytest.mark.xfail(strict=True, reason=(
    "D-09-05-T, the second half: the publisher keys a correction as '<ADV>r<n>' (pantry 314: "
    "tfv_display_vintage_key, tfv_display_immutable — one row per (product, storm_id, vintage_key), "
    "12 such rows in the first backfill), and this reader asks vintage_key = '<ADV>' through 294's "
    "tropical_file_vintage_in_force, so a corrected advisory is served its revision-0 object. The "
    "fixture here models the correction the way the bank does not (a second row at the same key); "
    "the display read moves to tropical_display_in_force (advisory_or_cycle, revision) in the "
    "revision-read lane, on banked r1 rows, and these two turn green there."))


@REVISION_READ_XFAIL
def test_I2_the_copy_in_force_is_revision_1_and_the_original_is_stated(client, monkeypatch, pg_c, pg):
    b = get(client, monkeypatch, pg_c, f"{P}/products?storm_id=ep202026&advisory=032")
    cone = by_product(b["objects"])["cone"]
    r0 = display("display:cone", "ep202026", "032")
    r1 = [r for r in lb.constructed_rows() if r["vintage_id"] == 910101][0]
    assert (cone["revision"], cone["corrected"]) == (1, True)
    assert cone["url"] == "https://display.energylake.io/" + r1["r2_key"]
    assert (cone["sha256"], cone["vertices"]) == (r1["sha256"], r1["meta"]["vertices"])
    assert cone["original"] == {"url": "https://display.energylake.io/" + r0["r2_key"],
                                "sha256": r0["sha256"], "bytes": r0["byte_size"], "absence": None}
    assert (b["revision"], b["corrected"]) == (1, True)
    assert [o["product"] for o in b["objects"]] == ["cone"]                 # one copy, never both
    live = by_product(get(client, monkeypatch, pg, f"{P}/products?storm_id=ep202026&advisory=032")["objects"])
    assert (live["cone"]["revision"], live["cone"]["sha256"]) == (0, r0["sha256"])


@REVISION_READ_XFAIL
def test_I2_the_ledger_holds_both_copies_and_the_view_one(pg_c):
    rows = pg_c.execute("""SELECT vintage_id FROM tropical_file_vintage WHERE product = 'display:cone'
                             AND storm_id = 'ep202026' AND vintage_key = '032' ORDER BY 1""").fetchall()
    assert [r["vintage_id"] for r in rows] == [910001, 910101]
    v = pg_c.execute("""SELECT vintage_id, revision, corrected FROM tropical_file_vintage_in_force
                         WHERE product = 'display:cone' AND storm_id = 'ep202026'
                           AND vintage_key = '032'""").fetchall()
    assert v == [{"vintage_id": 910101, "revision": 1, "corrected": True}]


# ═══════════════════════════════════════════════════════════════════════════
# T1 — /text
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sid,adv", [("ep182026", "058"), ("ep202026", "032")])
def test_T1_section_kinds_and_both_times_are_the_parses(client, monkeypatch, pg, sid, adv):
    b = get(client, monkeypatch, pg, f"{P}/text?storm_id={sid}&advisory={adv}")
    for product in ("tcd", "tcp"):
        o, rec = b["products"][product], t1(f"{sid}_adv{adv}_{product}.txt")
        assert o["absence"] is None
        assert [s["kind"] for s in o["sections"]] == rec["section_kinds"]
        assert (o["issued_utc"], o["transmitted_utc"], o["forecaster"]) == (
            rec["issued_utc"], rec["transmitted_utc"], rec["forecaster"])
        assert o["issued_utc"] != o["transmitted_utc"]                     # two times, and they differ
        assert o["sha256"] == rec["parsed_sha256"]
        assert o["url"] == f"https://display.energylake.io/tropics/{sid}/adv{adv}/{product}.json"
        src = banked(product, sid, adv)
        assert (o["source_r2_key"], o["source_sha256"]) == (src["r2_key"], src["sha256"])
        parse = json.loads((FIX / "recon" / f"{sid}_adv{adv}_{product}.json").read_text())
        assert [s["title"] for s in o["sections"]] == [s["title"] for s in parse["sections"]]


def test_T1_not_banked_when_the_bank_holds_no_text(client, monkeypatch, pg_c):
    b = get(client, monkeypatch, pg_c, f"{P}/text?storm_id=al092026&advisory=019")
    tcd = b["products"]["tcd"]
    assert tcd["url"] is None and tcd["absence"]["reason"] == "not_banked"
    assert "links advisory 019's tcd" in tcd["absence"]["detail"]
    assert b["products"]["tcp"]["absence"]["reason"] == "not_published"


def test_T1_not_published_and_an_intermediates_tcd_not_issued(client, monkeypatch, pg):
    b = get(client, monkeypatch, pg, f"{P}/text?storm_id=ep182026&advisory=057A")
    assert b["products"]["tcd"]["absence"]["reason"] == "not_issued"
    assert "links advisory 057's tcd" in b["products"]["tcd"]["absence"]["detail"]
    assert b["products"]["tcp"]["absence"]["reason"] == "not_published"
    i = get(client, monkeypatch, pg, f"{P}/text?storm_id=al092026")
    assert i["advisory"] == "020"
    assert {k: v["absence"]["reason"] for k, v in i["products"].items()} == {
        "tcd": "not_published", "tcp": "not_published"}


# ═══════════════════════════════════════════════════════════════════════════
# M1 — /members
# ═══════════════════════════════════════════════════════════════════════════

def parsed_cycles(sid):
    out = {}
    for r in lb.rows("ledger"):
        if r["source"] == "parsed" and r["storm_id"] == sid and r["product"].startswith("tracks:"):
            out.setdefault(r["vintage_key"], set()).add(r["product"])
    return out


@pytest.mark.parametrize("sid", ["al092026", "ep182026", "ep202026"])
def test_M1_every_banked_cycle_is_not_published_today(client, monkeypatch, pg, sid):
    b = get(client, monkeypatch, pg, f"{P}/members?storm_id={sid}")
    held = parsed_cycles(sid)
    assert b["cycles"] == [] and b["absence"]["reason"] == "none_published"
    assert b["cycles_held"] == sorted(held)
    product = dict(ti.ENSEMBLES)
    for a in b["absent"]:
        expect = "not_published" if product[a["ensemble"]] in held[a["cycle"]] else "not_banked"
        assert a["reason"] == expect, a
    assert {(a["cycle"], a["ensemble"]) for a in b["absent"]} == {
        (c, e) for c in held for e, _ in ti.ENSEMBLES}
    assert sum(a["reason"] == "not_published" for a in b["absent"]) == sum(len(v) for v in held.values())


def test_M1_trudy_is_not_banked(client, monkeypatch, pg):
    b = get(client, monkeypatch, pg, f"{P}/members?storm_id=ep212026")
    assert b["name"] == "Trudy" and b["cycles"] == []
    ecmwf = [a for a in b["absent"] if a["ensemble"] in ("ecmwf_ens", "ecmwf_aifs_ens")]
    assert ecmwf and all(a["reason"] == "not_banked" for a in ecmwf)
    assert len(ecmwf) == 2 * len(b["cycles_held"])
    gefs = [a for a in b["absent"] if a["ensemble"] == "gefs_adeck"]
    assert all(a["reason"] == "not_published" and "told by source name" in a["detail"] for a in gefs)


def test_M1_the_tracks_wording_points_here(client, monkeypatch):
    assert "GET /api/weather/tropics/members?storm_id=&cycle=" in tr.NOT_SERVED["ensemble_members"]
    assert "R2 parquet" in tr.NOT_SERVED["ensemble_members"]                # d091673's test still holds
    assert "GET /api/weather/tropics/products?storm_id=&advisory=" in tr.NOT_SERVED["cone_and_warnings"]
    body = json.loads((lb0.FIX / "bodies" / "tracks_ep182026_n4.json").read_text())
    assert body["not_served"]["ensemble_members"] == tr.NOT_SERVED["ensemble_members"]


def test_M1_a_published_manifest_is_served_and_a_small_one_is_too_few(client, monkeypatch, pg_c):
    s = get(client, monkeypatch, pg_c, f"{P}/members?storm_id=ep202026&cycle=2026101112")
    (cyc,) = s["cycles"]
    (ens,) = cyc["ensembles"]
    dry = json.loads((FIX / "recon" / "members_dry_ep202026_2026101112_ecmwf_ens_manifest.json").read_text())
    assert (cyc["cycle"], ens["ensemble"], ens["n_members"], ens["min_members"]) == (
        "2026101112", "ecmwf_ens", dry["n_members"], 5)
    assert ens["taus"] == sorted(int(t) for t in dry["members_by_tau"])
    assert ens["manifest_url"] == "https://display.energylake.io/tropics/ep202026/2026101112/ecmwf_ens/manifest.json"
    assert ens["sha256"] == hashlib.sha256(
        (FIX / "recon" / "members_dry_ep202026_2026101112_ecmwf_ens_manifest.json").read_bytes()).hexdigest()
    assert "CC BY 4.0" in ens["notice"]
    assert {a["ensemble"]: a["reason"] for a in s["absent"]} == {
        "ecmwf_aifs_ens": "not_published", "gefs_adeck": "not_published"}
    r = get(client, monkeypatch, pg_c, f"{P}/members?storm_id=ep182026&cycle=2026101112")
    few = [a for a in r["absent"] if a["ensemble"] == "ecmwf_ens"][0]
    assert few["reason"] == "too_few_members" and "1 members against its own minimum of 5" in few["detail"]
    assert few["manifest_url"].endswith("/tropics/ep182026/2026101112/ecmwf_ens/manifest.json")
    assert r["cycles"] == []
    i = get(client, monkeypatch, pg_c, f"{P}/members?storm_id=al092026&cycle=2026101006")
    aifs = [a for a in i["absent"] if a["ensemble"] == "ecmwf_aifs_ens"][0]
    assert aifs["reason"] == "too_few_members"                       # 7 against the manifest's own 10
    assert "7 members against its own minimum of 10" in aifs["detail"]


def test_M1_a_cycle_the_bank_never_saw_is_not_banked_for_every_ensemble(client, monkeypatch, pg):
    b = get(client, monkeypatch, pg, f"{P}/members?storm_id=ep202026&cycle=2026100100")
    assert [a["reason"] for a in b["absent"]] == ["not_banked"] * 3


# ═══════════════════════════════════════════════════════════════════════════
# X1 — the plan receipts: index scans only on tropical_file_vintage
# ═══════════════════════════════════════════════════════════════════════════

def neon_plans():
    text = (RECEIPTS / "neon_plans.txt").read_text()
    out = {}
    for block in text.split("\n## ")[1:]:
        head, _, plan = block.partition("\n")
        out[head.split("   (")[0].strip()] = plan
    return out


def test_X1_the_neon_receipts_show_index_scans_only():
    plans = neon_plans()
    stated = json.loads((RECEIPTS / "explains.json").read_text())
    assert sorted(plans) == sorted(s["name"] for s in stated)
    for name, plan in plans.items():
        assert "Seq Scan on tropical_file_vintage" not in plan, name
        assert "Seq Scan on tropical_track_points" not in plan, name
        if "tropical_file_vintage" in plan:
            assert "Index Scan using tfv_identity on tropical_file_vintage" in plan, name
        ms = float(re.search(r"Execution Time: ([0-9.]+) ms", plan).group(1))
        assert ms < 5.0, (name, ms)
    # the receipts' statements are the routes' own, with the routes' parameter builders
    from_code = {s["name"]: s["sql"] for s in stated}
    assert "v.product = ANY ('{cone_kmz,ww_kmz,surge_ww_kml,display:cone,display:ww,display:surge_ww}'" \
        in from_code["FILES_SQL products ep202026 032"]
    assert " ".join(ti.FILES_SQL.split()).split(" WHERE v.source")[0] in from_code["FILES_SQL products ep202026 032"]


LOCAL = [("ENTRY_NEWEST_SQL", lambda: ti.ENTRY_NEWEST_SQL, {"storm_id": "ep202026"}),
         ("ENTRY_SQL", lambda: ti.ENTRY_SQL, {"storm_id": "ep182026", "entry_prefix": "058:%"}),
         ("FILES_SQL products", lambda: ti.FILES_SQL,
          lambda: ti.files_params("ep202026", "032", ti.ADVISORY_PRODUCTS)),
         ("FILES_SQL wsp", lambda: ti.FILES_SQL, lambda: ti.files_params("", "2026101506", ti.WSP_PRODUCTS)),
         ("FILES_SQL text", lambda: ti.FILES_SQL, lambda: ti.files_params("ep202026", "032", ti.TEXT_PRODUCTS)),
         ("MEMBERS_SQL all", lambda: ti.MEMBERS_SQL, lambda: ti.members_params("ep202026", None)),
         ("MEMBERS_SQL cycle", lambda: ti.MEMBERS_SQL, lambda: ti.members_params("ep202026", "2026101112"))]


def _nodes(plan):
    yield plan
    for p in plan.get("Plans", []):
        yield from _nodes(p)


@pytest.mark.parametrize("name,sql,params", LOCAL, ids=[x[0] for x in LOCAL])
@pytest.mark.parametrize("which", ["pg", "pg_c"])
def test_X1_locally_at_neons_size_no_ledger_read_is_a_seq_scan(request, name, sql, params, which):
    conn = request.getfixturevalue(which)
    p = params() if callable(params) else params
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + sql(), p).fetchone()["QUERY PLAN"][0]["Plan"]
    nodes = list(_nodes(plan))
    rel = [n["Node Type"] for n in nodes if n.get("Relation Name") == "tropical_file_vintage"]
    assert rel and "Seq Scan" not in rel, rel
    # an Index Scan on tfv_identity, or a Bitmap Heap Scan whose bitmap is tfv_identity's
    assert any(n.get("Index Name") == "tfv_identity" for n in nodes), rel


def test_X1_no_index_was_added():
    for f in (ROOT / "migrations").glob("*.sql"):
        assert "tropical_file_vintage" not in f.read_text(), f.name


# ═══════════════════════════════════════════════════════════════════════════
# C — statement timeout first; the memo
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", [f"{P}/products?storm_id=ep202026", f"{P}/text?storm_id=ep182026",
                                  f"{P}/members?storm_id=ep212026",
                                  f"{P}/products?storm_id=ep202026&advisory=031"])
def test_C_each_build_sets_the_statement_timeout_first_and_is_memoised(client, monkeypatch, pg, path):
    _clear()
    pool = lb.PgPool(pg)
    monkeypatch.setattr(main, "_pool", pool)
    r1 = client.get(path)
    assert r1.status_code == 200
    assert pool.calls[0][0] == f"SET LOCAL statement_timeout = '{main.TROPICS_STATEMENT_TIMEOUT}'"
    n = len(pool.calls)
    assert all("tropical_file_vintage\n" not in q and "FROM tropical_file_vintage " not in q
               for q, _ in pool.calls[1:] if "tropical_file_vintage_in_force" not in q and "LATERAL" not in q)
    r2 = client.get(path)
    assert len(pool.calls) == n and r2.json()["cache"]["state"] == "fresh"
    assert r2.headers["X-Cache"] == "hit" and r2.headers["Cache-Control"] == "max-age=300"


def test_C_every_ledger_read_is_of_the_view_in_force():
    for name in ("ENTRY_NEWEST_SQL", "ENTRY_SQL", "FILES_SQL", "MEMBERS_SQL"):
        sql = getattr(ti, name)
        assert "FROM tropical_file_vintage_in_force" in sql, name
    # the one bare read is the original's, only where the copy in force is corrected
    assert ti.FILES_SQL.count("FROM tropical_file_vintage AS a") == 1
    assert "WHERE v.corrected" in ti.FILES_SQL


# ═══════════════════════════════════════════════════════════════════════════
# V1 — d091673's vectors: the two pointers and nothing else
# ═══════════════════════════════════════════════════════════════════════════

BEFORE = json.loads((FIX / "vectors_before_d091741.json").read_text())


@pytest.mark.parametrize("name", sorted(BEFORE["bodies"]))
def test_V1_each_vector_minus_the_two_pointers_is_mains(name):
    raw = (lb0.FIX / "bodies" / name).read_text()
    body = lb.revert_not_served(json.loads(raw))
    old = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(old).hexdigest() == BEFORE["bodies"][name]
    assert hashlib.sha256(raw.encode()).hexdigest() != BEFORE["bodies"][name]
