"""d091590 reds: each break below is applied to a throwaway copy of the tree,
the tests that guard it must FAIL there, and the unbroken tree must PASS them.

    python docs/receipts/wind-outlook-api-d091590/rehearse.py > .../reds.txt
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]

BREAKS = [
    ("W1", "the stored calibrated figure is served at every lead (D-09-25-127 ignored)",
     "wind_outlook.py",
     "        elif _fitted(line, h[\"lead_h\"]):\n",
     "        elif True:\n",
     "test_W1_beyond or test_P1_production_hubsum"),
    ("W1", "the boundary hard-coded to today's 66",
     "wind_outlook.py",
     '    return lo is not None and hi is not None and lo <= lead <= hi\n',
     '    return lead <= 66\n',
     "test_W1_the_boundary_moves or test_W1_no_constant or test_W1_a_line_whose"),
    ("W2", "the previous run's calibrated figure is not gated by its own line",
     "wind_outlook.py",
     "if h.get(\"prev_calibrated_mw\") is not None and _fitted(pline, h.get(\"prev_lead_h\"))",
     "if h.get(\"prev_calibrated_mw\") is not None",
     "test_W2"),
    ("W3", "a day across the calibration seam is one total",
     "wind_outlook.py",
     "            if _figure(h) == _figure(run[-1]):\n",
     "            if True:\n",
     "test_W3 or test_P1_production_hubsum"),
    ("W4", "the weather seam is a constant",
     "wind_outlook.py",
     "        if a[\"weather_source\"] != b[\"weather_source\"]:\n",
     "        if a[\"lead_h\"] == 48:\n",
     "test_W4_the_weather_seam"),
    ("W5", "the derivation counts rows written after the line was fitted",
     "wind_outlook.py",
     "           AND r.written_at <= c.fitted_at\n",
     "",
     "test_PG_W5_derived or test_W5_derivation"),
    ("W5", "the derivation ignores the scored set",
     "wind_outlook.py",
     "           AND r.scored_registry_mw > 0\n",
     "",
     "test_PG_W5_derived or test_W5_derivation"),
    ("W6", "CISO is paired with the hub actuals",
     "wind_outlook.py",
     '        return [("actual", "caiso_fuel_mix_hourly", "wind")]\n',
     '        return [("actual", "caiso_renewables_hourly", f"{h}:Wind") for h in so.HUBS]\n',
     "test_W6"),
    ("W7", "ZP26 gets a score card",
     "wind_outlook.py",
     'NO_SCORE_AREAS = ("ZP26",)',
     'NO_SCORE_AREAS = ()',
     "test_W7_zp26 or test_P1_production_ciso"),
    ("W8", "the attribution notice dropped from /sites",
     "wind_outlook.py",
     '        "attribution": ATTRIBUTION,\n        "tech": TECH,\n        "model": model,',
     '        "tech": TECH,\n        "model": model,',
     "test_W8_on_both_routes"),
    ("W9", "the site read becomes a window on target_ts over the whole table",
     "wind_outlook.py",
     "         WHERE x.tech = %(tech)s AND x.plant_code = s.plant_code\n",
     "         WHERE x.tech = %(tech)s\n",
     "test_W9_site_read or test_PG_W9"),
    ("D-09-25-75", "the statement timeout dropped",
     "main.py",
     "                await cur.execute(\n                    f\"SET LOCAL statement_timeout = '{SOLAR_STATEMENT_TIMEOUT}'\")\n"
     "                if init is None:\n                    await cur.execute(_wo.ISSUANCE_NEWEST_SQL, key)",
     "                if init is None:\n                    await cur.execute(_wo.ISSUANCE_NEWEST_SQL, key)",
     "test_reads_run_after_the_statement_timeout"),
]

TESTS = ["tests/test_wind_outlook.py", "tests/test_solar_outlook.py"]


def run(tree, k):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS]
    if k:
        args += ["-k", k]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    print("# d091590 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091590_red_"))
        try:
            shutil.copytree(ROOT, tmp / "t", ignore=shutil.ignore_patterns(".git", "__pycache__"))
            path = tmp / "t" / f
            src = path.read_text()
            if src.count(old) != 1:
                print(f"{tag}  BREAK NOT APPLIED ({what}): needle found {src.count(old)} times")
                ok = False
                continue
            path.write_text(src.replace(old, new))
            rc, tail = run(tmp / "t", k)
            red = rc != 0
            ok &= red
            print(f"{tag:<11} {'RED ' if red else 'NOT RED'}  {what}\n            -k '{k}': {tail}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    rc, tail = run(ROOT, "")
    ok &= rc == 0
    print(f"\nclean tree: {'GREEN' if rc == 0 else 'RED'}  {tail}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
