"""d091568 reds: each break below is applied to a throwaway copy of the tree,
the tests that guard it must FAIL there, and the unbroken tree must PASS them.

    python docs/receipts/solar-outlook-api-d091568/rehearse.py > .../reds.txt
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]

BREAKS = [
    ("G1", "a band with no row borrows another band's score for the same who",
     "solar_outlook.py",
     '        r = by_key.get((band, who))\n',
     '        r = by_key.get((band, who)) or next((x for (b, w), x in by_key.items() if w == who), None)\n',
     "test_G1_band_with_no_row"),
    ("G1", "scored=false served as a score",
     "solar_outlook.py",
     'scored = r is not None and bool(r["scored"]) and r["n_days"] >= MIN_SCORED_DAYS',
     'scored = r is not None',
     "test_G1_scored_false"),
    ("G1", "the score lateral keeps the band but drops `who`",
     "solar_outlook.py",
     "AND s.lead_band = v.lead_band AND s.who = v.who",
     "AND s.lead_band = v.lead_band",
     "test_G1_the_score_read or test_PG_G1"),
    ("G2", "caiso_dam offered beside every area",
     "solar_outlook.py",
     "    dam = _series(\"caiso_dam\") if area_kind in CAISO_DAM_KINDS else None\n",
     "    dam = _series(\"caiso_dam\")\n",
     "test_G2_no_caiso_dam"),
    ("G2", "the score card asks for caiso_dam on every kind",
     "solar_outlook.py",
     "    if area_kind in CAISO_DAM_KINDS:\n        pairs.append((DAM, \"caiso_dam\"))",
     "    pairs.append((DAM, \"caiso_dam\"))",
     "test_G2_no_caiso_dam"),
    ("G3", "previous issuance ranked without the model (the d091557 mistake)",
     "solar_outlook.py",
     '''_PREV_SUBQUERY = f"""(SELECT p.init_ts
             FROM implied_gen_area_hourly p
            WHERE {_AREA_KEY.format(a="p")}''',
     '''_PREV_SUBQUERY = f"""(SELECT p.init_ts
             FROM implied_gen_area_hourly p
            WHERE p.tech = %(tech)s AND p.area_kind = %(area_kind)s AND p.area = %(area)s''',
     "test_G3_previous_issuance_is_ranked or test_PG_G3_previous"),
    ("G3", "previous_mw mixes figures (always the previous registry)",
     "solar_outlook.py",
     '    if h["calibrated_mw"] is not None:\n        return _f(h["prev_calibrated_mw"])\n',
     '',
     "test_G3_calibrated_hours"),
    ("G4", "a line that exists is reported though no row carries it",
     "solar_outlook.py",
     '        ids = {int(h["calibration_id"]) for h in hours\n'
     '               if h["lead_band"] == band and h.get("calibration_id") is not None}',
     '        ids = {int(l["calibration_id"]) for l in lines if l["lead_band"] == band}',
     "test_G4_a_line_the_rows_do_not_carry"),
    ("G5", "one word of the label changed",
     "solar_outlook.py",
     "Not a \"\n         \"forecast of metered output",
     "Not a \"\n         \"prediction of metered output",
     "test_G5"),
    ("D-09-25-75", "the statement timeout dropped from the outlook read",
     "main.py",
     "                await cur.execute(\n                    f\"SET LOCAL statement_timeout = '{SOLAR_STATEMENT_TIMEOUT}'\")\n"
     "                if init is None:",
     "                if init is None:",
     "test_reads_run_after_the_statement_timeout"),
]


def run(tree, k):
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/test_solar_outlook.py", "-k", k],
                       cwd=tree, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    print("# d091568 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091568_red_"))
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
