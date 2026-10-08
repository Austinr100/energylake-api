"""d091667 mutation rehearsal (spec R): one break per rule, each applied IN
PLACE to this tree, the tests that guard it run and must FAIL, and the file is
written back from its original bytes before the next break. The whole tree
(every tracked and untracked file outside .git) is hashed before and after:
the run ends with "tree restored byte for byte" or it fails.

    python docs/receipts/asset-runs-d091667/rehearse.py > docs/receipts/asset-runs-d091667/reds.txt
"""
import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = "tests/test_asset_runs_d091667.py"
OUT = "docs/receipts/asset-runs-d091667/reds.txt"
AP, MAIN, VT = "asset_page.py", "main.py", "vintages.py"

# (rule, break, file, old, new, -k selection of the guarding tests)
BREAKS = [
    ("1  D-09-25-167", "newest first: the run before is no longer issuances[k-1]",
     AP, "    for i in inits:\n        if i < lo:", "    for i in reversed(inits):\n        if i < lo:",
     "test_H1_sunzia_serves or test_H1_eight_runs or test_H1_the_run_before or test_H2_a_skipped"),
    ("1  D-09-25-167", "a server-side gap (the page's arithmetic computed here)",
     AP, '            "n_plants": led["run_n_plants"],\n',
     '            "n_plants": led["run_n_plants"],\n            "gap_h": 6,\n',
     "test_H1_no_change"),
    ("1  hand diff", "each series one hour early (t0 from the init, not the first row)",
     AP, 't0, lead0 = rs[0]["target_ts"], rs[0]["lead_h"]',
     't0, lead0 = rs[0]["target_ts"] - _HOUR, rs[0]["lead_h"]',
     "test_H1_every_served or test_H1_the_run_before"),
    ("2  D-09-25-173", "one series across the weather seam",
     AP, 'by_src.setdefault(r["weather_source"], []).append(r)',
     'by_src.setdefault(rows[0]["weather_source"], []).append(r)',
     "test_H3"),
    ("2  D-09-25-173", "the ledger read steps over another model's runs",
     AP, "         WHERE tech = %(tech)s AND model = %(model)s\n         ORDER BY init_ts DESC",
     "         WHERE tech = %(tech)s\n         ORDER BY init_ts DESC",
     "test_PG_H9_the_statement or test_H9_the_runs_sql or test_H3_every_read"),
    ("3  verbatim", "an unknown token mapped to the column's first known label",
     AP, "    return {\"token\": token, \"status\": \"unknown\", \"label\": UNKNOWN_BASIS_LABEL}",
     "    return {\"token\": token, \"status\": \"known\", \"label\": next(iter(BASIS_LABELS[column].values()))}",
     "test_H4_a_made_up"),
    ("3  verbatim", "public_record relabelled as the USWTDB rotor",
     AP, '"public_record": "a named public document (equipment_source quotes it)",\n        "none": "no source the writer reads states it (equipment_source says why)",',
     '"public_record": "USWTDB: the capacity-weighted mean rotor of the turbines listed at the "\n                  "plant\'s EIA id (a blend where they differ)",\n        "none": "no source the writer reads states it (equipment_source says why)",',
     "test_H4_each_label or test_H4_no_two"),
    ("3  verbatim", "equipment_source trimmed (derived from, not served)",
     AP, '        "equipment_source": src,', '        "equipment_source": src and src[:200],',
     "test_H4_equipment_source or test_H6_west_camp"),
    ("3  mixed fleet", "the source text split into parts on ' + '",
     AP, '            "parts": [{"turbine_model": s["turbine_model"], "n_turbines": s["n_turbines"],\n                       "rotor_m": rotor, "hub_height_m": hub_h}],',
     '            "parts": [{"turbine_model": m, "n_turbines": s["n_turbines"], "rotor_m": rotor, "hub_height_m": hub_h}\n                      for m in ((src.split(" + ") if src and " + " in src else None) or [s["turbine_model"]])],',
     "test_H5"),
    ("3  animate", "the rotor turns without a diameter (only speeds checked)",
     AP, 'missing = [k for k in ANIMATE_NEEDS if (rotor if k == "rotor_m" else speeds[k]) is None]',
     'missing = [k for k in ANIMATE_NEEDS if k != "rotor_m" and speeds[k] is None]',
     "test_H6"),
    ("4  absence", "a run without the plant is a silent hole",
     AP, '            body["absent"].append({"init_ts": _iso(i), "reason": "plant_not_in_run",',
     '            None and body["absent"].append({"init_ts": _iso(i), "reason": "plant_not_in_run",',
     "test_H2_a_run_without or test_H2_nothing_held"),
    ("4  absence", "a full ledger's older run called before_history, not evicted",
     AP, '        return "evicted" if full else "before_history"', '        return "before_history"',
     "test_H2_evicted or test_H1_eight_runs"),
    ("4  absence", "the next cycle is not listed as not_yet_landed",
     AP, '    body["absent"].append({"init_ts": _iso(nxt), "reason": "not_yet_landed",',
     '    None and body["absent"].append({"init_ts": _iso(nxt), "reason": "not_yet_landed",',
     "test_H2_today or test_H1_eight_runs"),
    ("5  attribution", "the runs payload drops the notice",
     AP, '        body["attribution"] = wo.ATTRIBUTION\n        body["weather_height_m"]',
     '        body["weather_height_m"]', "test_H7"),
    ("5  attribution", "wind vintages drops the notice (d091644's defect back)",
     VT, '        body["attribution"] = wo.ATTRIBUTION', '        pass', "test_H7"),
    ("6  D-09-25-75", "the runs read loses its statement timeout",
     MAIN, '''                await cur.execute(
                    f"SET LOCAL statement_timeout = '{SOLAR_STATEMENT_TIMEOUT}'")
                await cur.execute(_ap.RUNS_PLANT_SQL[tech],''',
     '''                await cur.execute(_ap.RUNS_PLANT_SQL[tech],''',
     "test_C_timeout"),
    ("6  memo", "the memo key drops n",
     MAIN, "_solar_serve(_asset_runs_cache, (t, code, n_),", "_solar_serve(_asset_runs_cache, (t, code),",
     "test_C_timeout or test_H1_n_reaches"),
    ("6  plan", "the LATERAL loses its fence (OFFSET 0): no run prunes",
     AP, "             AND r.k <= %(n)s\n          OFFSET 0\n", "             AND r.k <= %(n)s\n",
     "test_H9 or test_PG_H9_each_run"),
    ("6  n", "n above 8 trimmed to 8, not refused",
     AP, '        raise ValueError(f"n must be 1-{RUNS_N_MAX} (the history keeps {RUNS_KEEP} runs), got {n}")',
     '        return max(1, min(n, RUNS_N_MAX))', "test_H1_n_out_of_range"),
]


def tree_hash() -> str:
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"],
                           capture_output=True, text=True, check=True).stdout.split()
    h = hashlib.sha256()
    for f in sorted(set(files) - {OUT}):       # the receipt this run is writing
        p = ROOT / f
        if p.is_file():
            h.update(f.encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def run(k: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                        TESTS, "-k", k], cwd=ROOT, capture_output=True, text=True)
    return r.returncode, (r.stdout.strip().splitlines() or [""])[-1]


def main() -> int:
    before = tree_hash()
    rc, tail = run("test_")
    print(f"clean tree: {'GREEN' if rc == 0 else 'RED'}  ({tail})")
    ok = rc == 0
    for rule, what, f, old, new, k in BREAKS:
        p = ROOT / f
        orig = p.read_bytes()
        text = orig.decode()
        if text.count(old) != 1:
            print(f"{rule:16} {what}: the break's anchor is not unique in {f} -- NOT RUN")
            ok = False
            continue
        try:
            p.write_text(text.replace(old, new))
            rc, tail = run(k)
        finally:
            p.write_bytes(orig)
        red = rc != 0
        ok &= red
        print(f"{rule:16} {'RED ' if red else 'GREEN (!)'} {what}  [{f}]  ({tail})")
    after = tree_hash()
    print(f"tree {'restored byte for byte' if after == before else 'CHANGED'}: sha256 {after[:16]}")
    return 0 if ok and after == before else 1


if __name__ == "__main__":
    sys.exit(main())
