"""d091611: production's rows, replayed through the load routes' statements.

tests/fixtures/load_outlook_d091611/production_2026_10_05_0330z.json holds
what each of load_outlook.py's statements returned on Neon at NOW (read-only,
through the Neon connector; docs/receipts/load-net-demand-api-d091611/render.py
renders the exact text). `pool()` answers each statement from it the way
Postgres would: by the statement's own parameters (area, dataset, series, the
ts range, the inits), so a route served from it is production's answer.

Not a test module (no test_ prefix): the tests and the receipts' sample.py
both import it.
"""

import json
import pathlib
from datetime import date, datetime, timezone

from test_solar_outlook import FakePool

FIX = (pathlib.Path(__file__).resolve().parent / "fixtures" / "load_outlook_d091611"
       / "production_2026_10_05_0330z.json")
NOW = datetime(2026, 10, 5, 3, 30, tzinfo=timezone.utc)

_EIA = {"BPAT": "BPAT", "LADWP": "LDWP"}


def _ts(s):
    return datetime.fromisoformat(s)


def load() -> dict:
    b = json.loads(FIX.read_text())
    out = {"now": _ts(b["now"]), "usability": b["usability"], "lines": [], "fcst": {},
           "series": {}, "hub": [], "truth": [], "newest": {}, "gen": {}, "bt": {}}
    for k, v in b.items():
        if k.startswith("fcst:"):
            out["fcst"][k[5:]] = [{"product": p, "ts": _ts(t), "value": x, "publish_time": pt}
                                  for p, t, x, pt in v]
        elif k.startswith("df:"):
            out["series"][("wecc_load_forecast_da_hourly", k[3:])] = [
                {"ts": _ts(t), "value": x} for t, x in v]
        elif k.startswith("eia_d:"):
            out["series"][("wecc_load_hourly", k[6:])] = [{"ts": _ts(t), "value": x} for t, x in v]
        elif k == "native":
            out["series"][("caiso_load_hourly", "actual")] = [{"ts": _ts(t), "value": x} for t, x in v]
        elif k == "hub":
            out["hub"] = [{"series": s, "ts": _ts(t), "value": x} for s, t, x in v]
        elif k == "truth":
            out["truth"] = [{"part": p, "ts": _ts(t), "value": x} for p, t, x in v]
        elif k.startswith("newest:"):
            out["newest"][k[7:]] = _ts(v[0][0]) if v else None
        elif k.startswith(("gen:", "bt:")):
            kind, part = k.split(":")
            out[kind][part] = [dict(zip(("init_ts", "target_ts", "lead_h", "lead_band",
                                         "registry_mw", "calibrated_mw", "calibration_id"),
                                        (_ts(r[0]), _ts(r[1]), *r[2:]))) for r in v]
    for l in b["lines"]:
        out["lines"].append({**l, "fit_end": date.fromisoformat(l["fit_end"]),
                             "fitted_at": _ts(l["fitted_at"])})
    return out


def _in(rows, p, key="ts"):
    return [r for r in rows if p["lo"] <= r[key] < p["hi"]]


def pool(b: dict) -> FakePool:
    part_of = {"solar_pv": "solar", "wind": "wind"}

    def fcst(p):
        return _in(b["fcst"].get(p["area"], []), p)

    def series(p):
        return _in(b["series"].get((p["dataset"], p["series"]), []), p)

    def usability(p):
        return [r for r in b["usability"] if r["area"] in p["areas"]]

    def newest(p):
        t = b["newest"].get(part_of[p["tech"]])
        return [{"init_ts": t}] if t else []

    def gen_rows(p):
        part = part_of[p["tech"]]
        src = b["gen"][part] + b["bt"][part]
        inits = set(p["inits"])
        seen, out = set(), []
        for r in src:
            k = (r["init_ts"], r["target_ts"])
            if r["init_ts"] in inits and p["lo"] <= r["target_ts"] < p["hi"] and k not in seen:
                seen.add(k)
                out.append(r)
        return sorted(out, key=lambda r: (r["init_ts"], r["target_ts"]))

    def lines(p):
        return [l for l in b["lines"] if l["calibration_id"] in set(p["ids"])]

    return FakePool([
        ("SET LOCAL", []),
        ("AS p(product, dataset)", fcst),
        ("WITH m AS", usability),
        ("AS s(series)", lambda p: _in(b["hub"], p)),
        ("AS s(part, dataset, series)", lambda p: _in(b["truth"], p)),
        ("ANY(%(inits)s)", gen_rows),
        ("ORDER BY g.init_ts DESC", newest),
        ("FROM implied_gen_calibration", lines),
        ("t.dataset = %(dataset)s", series),
    ])
