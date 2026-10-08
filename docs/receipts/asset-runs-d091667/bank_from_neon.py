"""d091667: build tests/fixtures/asset_runs_d091667/bank_2026_10_08.json from
the Neon connector's saved results (read-only, 2026-10-08 20:32-20:43Z).

The three reads, as run through the connector (their full text is in
explains.sql), each returned per-run ARRAYS so the bank stays compact:

    history_wind_66923.json   implied_gen_site_history, wind/hrrr_gfs, SunZia
                              Wind South, every held run, one row per run
    history_solar_58388.json  the same, solar_pv/gfs, Solar Star 1
    wind_sites.json           implied_gen_wind_sites, all 323 plants, the
                              asset route's site columns + equipment_source

Each run carries the md5 Neon computed over its rows in a canonical text
(`pgtext` below reproduces it); the site table's md5 was read separately
(SITES_MD5). The test suite re-checks every md5 on load, so a hand edit of the
bank is a red test, not a silent change.

    python docs/receipts/asset-runs-d091667/bank_from_neon.py <dir with the three files>
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parents[2] / "tests" / "fixtures" / "asset_runs_d091667" / "bank_2026_10_08.json"

READ_AT = "2026-10-08T20:43:13.410Z"

# implied_gen_site_history_runs, the whole ledger, read 2026-10-08 20:43:13Z
# (and identical at 20:32:51Z, before the history reads).
LEDGER = [
    {"tech": "solar_pv", "model": "gfs", "init_ts": "2026-10-08T00:00:00.000Z", "n_rows": 394800,
     "n_plants": 1645, "method_version": "solar_pv_v1", "landed_at": "2026-10-08T05:30:04.976Z"},
    {"tech": "solar_pv", "model": "gfs", "init_ts": "2026-10-08T06:00:00.000Z", "n_rows": 394800,
     "n_plants": 1645, "method_version": "solar_pv_v1", "landed_at": "2026-10-08T11:26:45.214Z"},
    {"tech": "solar_pv", "model": "gfs", "init_ts": "2026-10-08T12:00:00.000Z", "n_rows": 394800,
     "n_plants": 1645, "method_version": "solar_pv_v1", "landed_at": "2026-10-08T17:25:50.489Z"},
    {"tech": "wind", "model": "hrrr_gfs", "init_ts": "2026-10-08T00:00:00.000Z", "n_rows": 77520,
     "n_plants": 323, "method_version": "wind_v1", "landed_at": "2026-10-08T03:04:17.538Z"},
    {"tech": "wind", "model": "hrrr_gfs", "init_ts": "2026-10-08T06:00:00.000Z", "n_rows": 77520,
     "n_plants": 323, "method_version": "wind_v1", "landed_at": "2026-10-08T09:08:41.445Z"},
    {"tech": "wind", "model": "hrrr_gfs", "init_ts": "2026-10-08T12:00:00.000Z", "n_rows": 77520,
     "n_plants": 323, "method_version": "wind_v1", "landed_at": "2026-10-08T14:58:03.655Z"},
]

# md5 over implied_gen_wind_sites' equipment columns, every plant, read through
# the connector (SITES_MD5_SQL in explains.sql); SITES_MD5_COLS is its order.
SITES_MD5 = "f71c2fbad1e3ff659dff119f1282c7b2"
SITES_MD5_COLS = ("plant_code", "plant_name", "turbine_model", "turbine_model_basis", "n_turbines",
                  "n_turbines_basis", "rotor_m", "rotor_basis", "hub_height_m", "hub_height_basis",
                  "curve_turbine_type", "curve_hub_height_m", "curve_basis", "cut_in_ms", "rated_ms",
                  "cut_out_ms", "equipment_source", "nameplate_mw", "hub")

# The order of each run's md5 text, per tech.
RUN_MD5_COLS = {
    "wind": ("lead_h", "weather_source", "implied_mw", "outage", "cap", "mv", "hub_ws_ms", "gust_ms"),
    "solar_pv": ("lead_h", "weather_source", "implied_mw", "outage", "cap", "mv", "ghi_wm2",
                 "clearsky_ghi_wm2", "clearsky_mw", "tcc_pct", "precip_mm"),
}


def pgtext(v) -> str:
    """Postgres 17's text for a value as the canonical md5 text writes it:
    NULL as '', a float8 shortest-exact (an integral float without '.0')."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    return str(v)


def run_md5_text(tech: str, run: dict) -> str:
    cols = RUN_MD5_COLS[tech]
    lines = []
    for i in range(len(run["lead_h"])):
        vals = [run["mv"] if c == "mv" else run[c][i] for c in cols]
        lines.append("|".join(pgtext(v) for v in vals))
    return "\n".join(lines)


def sites_md5_text(rows: list[dict]) -> str:
    return "\n".join("|".join(pgtext(r[c]) for c in SITES_MD5_COLS)
                     for r in sorted(rows, key=lambda r: r["plant_code"]))


def _runs(path: pathlib.Path, tech: str) -> list[dict]:
    out = []
    for r in json.loads(path.read_text()):
        assert r["off_ladder"] == "0" and r["mv"] == r["mv2"], r["init_ts"]
        run = {k: v for k, v in r.items() if k not in ("off_ladder", "mv2", "n")}
        run["n"] = int(r["n"])
        out.append(run)
    return out


def main(src: pathlib.Path) -> None:
    import hashlib
    sites = json.loads((src / "wind_sites.json").read_text())[0]["rows"]
    bank = {
        "read_at": READ_AT,
        "ledger": LEDGER,
        "runs": {"wind/66923": _runs(src / "history_wind_66923.json", "wind"),
                 "solar_pv/58388": _runs(src / "history_solar_58388.json", "solar_pv")},
        "wind_sites": sites,
        "wind_sites_md5": SITES_MD5,
    }
    for key, runs in bank["runs"].items():
        tech = key.split("/")[0]
        for run in runs:
            got = hashlib.md5(run_md5_text(tech, run).encode()).hexdigest()
            assert got == run["md5"], (key, run["init_ts"], got, run["md5"])
    assert hashlib.md5(sites_md5_text(sites).encode()).hexdigest() == SITES_MD5
    OUT.write_text(json.dumps(bank, separators=(",", ":"), sort_keys=True) + "\n")
    print(f"wrote {OUT} ({OUT.stat().st_size:,} B)")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
