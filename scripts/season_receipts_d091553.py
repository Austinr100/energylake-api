"""
Bank d091553's receipts: the Sacramento (USW00023232) and LAX (USW00023174)
station-precipitation /season bodies, before and after the tolerant base.

Each body is `build_season` on d091550's banked rows (tests/fixtures/
season_precip_{station}.json, frontier 2026-09-26) with the test suite's ENSO
bins (tests/test_season_missing_days.py `_build`), so ENSO touches nothing
below but `vs_category`. "Before" is a given season.py (main's), "after" this
checkout's. Writes, into docs/receipts/season-tolerant-base-d091553/:

    season_precip_{station}_{before,after}.json   the bodies
    bodies_summary.json                           the readable cut and the diff
    SHA256SUMS                                    every file in the directory

Usage:

    python scripts/season_receipts_d091553.py <path to main's season.py>
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import season  # noqa: E402
from test_season_missing_days import _build, banked  # noqa: E402

OUT = ROOT / "docs" / "receipts" / "season-tolerant-base-d091553"
STATIONS = ("USW00023232", "USW00023174")
DAYS = ("02-01", "02-18", "02-19", "04-01", "09-26", "09-30")


def _load(path: str):
    spec = importlib.util.spec_from_file_location("season_before", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _diff(a, b, path="") -> list[str]:
    """Every key path whose value differs, a key-walk (lists compared whole)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in list(a) + [k for k in b if k not in a]:
            p = f"{path}.{k}" if path else k
            if k not in a:
                out.append(f"+ {p}")
            elif k not in b:
                out.append(f"- {p}")
            else:
                out += _diff(a[k], b[k], p)
        return out
    return [] if a == b else [f"~ {path}"]


def _cut(p: dict) -> dict:
    ax, base = p["axis"], p["base"]
    pct = p["percentiles"]
    return {
        "frontier": p["frontier"],
        "base": {"rule": base["rule"], "n": base["n"], "tolerance": base.get("tolerance"),
                 "excluded": [e["season"] for e in base["excluded"]]},
        "p50_at": {d: pct["p50"][ax.index(d)] for d in DAYS} if pct else None,
        "this_season_through": p["this_season"]["through"],
        "readout": p["readout"],
        "readout_absence": p["readout_absence"],
        "method": p["source"]["method"],
    }


def main(before_path: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before_mod = _load(before_path)
    summary = {}
    for st in STATIONS:
        area = f"station:{st}"
        daily = banked(st)
        bodies = {"before": _build(daily, area=area, mod=before_mod),
                  "after": _build(daily, area=area, mod=season)}
        for side, body in bodies.items():
            (OUT / f"season_precip_{st}_{side}.json").write_text(json.dumps(body))   # d091550's bytes
            summary[f"{st} {side}"] = _cut(body)
        b, a = bodies["before"], bodies["after"]
        moved = _diff(b, a)
        summary[f"{st} diff"] = {
            "paths": [m for m in moved if not m.startswith("~ years")
                      and not m.startswith("~ curves")],
            "years_to_date_moved": sum(1 for x, y in zip(b["years"], a["years"])
                                       if x["to_date"] != y["to_date"]),
            "years_complete_flipped": [y["season"] for x, y in zip(b["years"], a["years"])
                                       if x["complete"] != y["complete"]],
            "curves_added": [k for k in a["curves"] if k not in b["curves"]],
        }
    (OUT / "bodies_summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    sums = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}"
            for f in sorted(OUT.iterdir()) if f.name != "SHA256SUMS"]
    (OUT / "SHA256SUMS").write_text("\n".join(sums) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
