"""Bank the dashboard lane's vectors and today's production bytes, and list
every body difference between them, place by place. Run from the repo root:

    python docs/receipts/cpc-api-flip-v2-d091693/sample.py

Both sides are served by the real routes over the same local bank (`both`:
Neon's v1 and v2 rows as they stood 2026-10-11 ~13:47Z, tests/load_bank_d091693.py;
test_V1_the_local_bank_answers_as_neon holds it read for read):

  * the vectors (body_*.json) through this branch's cpc_outlooks.py, v2;
  * production (prod/body_*.json) through main a10b6f2's cpc_outlooks.py
    (`git show a10b6f2:cpc_outlooks.py`, loaded as a module and put where
    main.py reads `_co`), v1: what the deployed API serves over the same rows.
    The deployed host asks for auth (a 401), so its bytes were not fetched.

Each body is written as served, cache block included (a cold memo: the clock,
built_at and build_seconds, is the only part a re-run changes). bytes.psv:
name | path | raw bytes | gzip -6 bytes | sha-256 of the file.
body_differences.md is generated from the two sets of bodies.
"""
import gzip
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fastapi.testclient import TestClient   # noqa: E402
import main                                  # noqa: E402
import cpc_outlooks as co                    # noqa: E402
import load_bank_d091673 as lb               # noqa: E402
import load_bank_d091693 as bank             # noqa: E402

MAIN_REV = "a10b6f2"
CACHES = ("_cpc_places_cache", "_cpc_curves_cache", "_cpc_vintages_cache",
          "_cpc_outlooks_cache")


def P(key):
    pk, pl, w = key
    return {"place_kind": pk, "place": pl, **({"weighting": w} if w else {})}


BODIES = [
    ("body_curves_ksan.json", "/api/weather/cpc/curves", P(bank.KSAN)),
    ("body_curves_kden.json", "/api/weather/cpc/curves", P(bank.KDEN)),
    ("body_curves_san_jose.json", "/api/weather/cpc/curves", P(bank.SJC)),
    ("body_curves_pnw_population.json", "/api/weather/cpc/curves", P(bank.PNW_POP)),
    ("body_curves_sp15_load_share_365d.json", "/api/weather/cpc/curves", P(bank.SP15_LOAD)),
    ("body_vintages_814temp_ksan_n14.json", "/api/weather/cpc/curves/vintages",
     {"product": "814temp", **P(bank.KSAN), "n": 14}),
    ("body_vintages_610temp_pnw_population.json", "/api/weather/cpc/curves/vintages",
     {"product": "610temp", **P(bank.PNW_POP)}),
    ("body_vintages_610temp_kden_n14.json", "/api/weather/cpc/curves/vintages",
     {"product": "610temp", **P(bank.KDEN), "n": 14}),
    ("body_places.json", "/api/weather/cpc/places", {}),
]


def main_module():
    """main a10b6f2's cpc_outlooks.py, as a module."""
    src = subprocess.run(["git", "show", f"{MAIN_REV}:cpc_outlooks.py"], cwd=ROOT,
                         check=True, capture_output=True, text=True).stdout
    spec = importlib.util.spec_from_loader("cpc_outlooks_main", loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(src, f"{MAIN_REV}:cpc_outlooks.py", "exec"), mod.__dict__)
    return mod


def serve(client, conn, module, path, params):
    for c in CACHES:
        getattr(main, c).clear()
    saved, main._co, main._pool = main._co, module, lb.PgPool(conn)
    try:
        r = client.get(path, params=params)
    finally:
        main._co = saved
    assert r.status_code == 200, (path, params, r.text)
    return r.content


def q(params):
    return "&".join(f"{k}={v}" for k, v in params.items())


def frame(prod):
    v = prod["vintage"]
    if v is None:
        return "no issuance"
    c, a = v["curve"], prod["absence"]
    if c is not None:
        extra = f", base_years {c['base_years']}" if "base_years" in c else ""
        return f"{v['issued_date']} curve, {c['band_claim']}{extra}"
    if a["reason"] == "not_written":
        return f"{v['issued_date']} not_written"
    codes = ",".join(r["code"] for r in a.get("reasons", []))
    return f"{v['issued_date']} not_drawable ({codes}; band_basis {v['verdict']['band_basis']})"


def differences(client, conn, prod_mod):
    """The /curves table for every place, from both modules over the same bank."""
    places = json.loads(serve(client, conn, co, "/api/weather/cpc/places", {}))
    rows = []
    for p in places["places"]:
        key = (p["place_kind"], p["place"], p["weighting"])
        a = json.loads(serve(client, conn, prod_mod, "/api/weather/cpc/curves", P(key)))
        b = json.loads(serve(client, conn, co, "/api/weather/cpc/curves", P(key)))
        h = sorted({c["history_years_in_base"] for pr in p["products"]
                    for s in pr["seasons"] for c in s["cells"]})
        for pa, pb in zip(a["products"], b["products"]):
            rows.append((key, h, pa["product"], frame(pa), frame(pb),
                         (pb["vintage"]["curve"] or {}).get("label")))
    return rows


def main_():
    client = TestClient(main.app)
    prod_mod = main_module()
    assert prod_mod.CPC_METHOD_VERSION == "cpc_curves_v1"
    (HERE / "prod").mkdir(exist_ok=True)
    out = []
    with bank.banks() as conns:
        for name, path, params in BODIES:
            for where, mod in ((HERE, co), (HERE / "prod", prod_mod)):
                b = serve(client, conns["both"], mod, path, params)
                (where / name).write_bytes(b)
            b = (HERE / name).read_bytes()
            gz = len(gzip.compress(b, 6, mtime=0))
            out.append(f"{name}|{path}{'?' + q(params) if params else ''}|{len(b)}|{gz}|"
                       f"{hashlib.sha256(b).hexdigest()}")
            print(out[-1])
        rows = differences(client, conns["both"], prod_mod)
    (HERE / "bytes.psv").write_text("\n".join(out) + "\n")
    (HERE / "curves_by_place.json").write_text(json.dumps(
        [{"place_kind": k[0], "place": k[1], "weighting": k[2], "base_years": h,
          "product": prod, "production": a, "flip": b, "label": lab}
         for k, h, prod, a, b, lab in rows], indent=1) + "\n")
    print(len(rows), "frames")


if __name__ == "__main__":
    main_()
