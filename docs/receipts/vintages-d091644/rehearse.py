"""d091644 reds (spec §2 R): each break below is applied to a throwaway copy of
the tree, the tests that guard it must FAIL there, and the unbroken tree must
PASS them.

    python docs/receipts/vintages-d091644/rehearse.py > docs/receipts/vintages-d091644/reds.txt
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = "tests/test_vintages_d091644.py"

BREAKS = [
    ("V1", "lead0 counted from 0 (lead_h - 1)",
     "vintages.py",
     't0, lead0 = rows[0]["target_ts"], rows[0]["lead_h"]',
     't0, lead0 = rows[0]["target_ts"], rows[0]["lead_h"] - 1',
     "test_V1_66h"),
    ("V1", "whole MW by banker's rounding (half to even)",
     "vintages.py",
     "rounding=ROUND_HALF_UP)",
     "rounding='ROUND_HALF_EVEN')",
     "test_V1_whole_mw"),
    ("V2", "cal ungated: the stored calibrated figure at every lead",
     "vintages.py",
     'cal[i] = whole_mw(h["shown_calibrated_mw"])',
     'cal[i] = whole_mw(h["calibrated_mw"])',
     "test_V2_cal_is_null_at_hours or test_V2_cal_equals or test_PG_V2"),
    ("V2", "an issuance no line covers ships a list of nulls, not null",
     "vintages.py",
     '"cal": cal if any(c is not None for c in cal) else None,',
     '"cal": cal,',
     "test_V2_cal_is_null_for_an_issuance"),
    ("V3", "a missing hour is served as 0",
     "vintages.py",
     "reg: list = [None] * width",
     "reg: list = [0] * width",
     "test_V3 or test_PG_V1_V3"),
    ("V4", "newest first",
     "vintages.py",
     '[_issuance(by_init[i], lines) for i in sorted(by_init)]',
     '[_issuance(by_init[i], lines) for i in sorted(by_init, reverse=True)]',
     "test_V4_oldest_first or test_PG_V4"),
    ("V4", "n above 120 accepted",
     "vintages.py",
     "if not 1 <= n <= N_MAX:",
     "if n < 1:",
     "test_V4_n_out_of_range"),
    ("V4", "the walk steps over another model's run (the d091557 mistake)",
     "vintages.py",
     '                 WHERE {so._AREA_KEY.format(a="p")}',
     "                 WHERE p.tech = %(tech)s AND p.area_kind = %(area_kind)s AND p.area = %(area)s",
     "test_PG_V4"),
    ("V4", "n never reaches the read (always the default)",
     "main.py",
     'await cur.execute(_vt.VINTAGES_SQL, {**key, "n": n})',
     'await cur.execute(_vt.VINTAGES_SQL, {**key, "n": _vt.N_DEFAULT})',
     "test_V4_n_reaches"),
    ("V5", "no absence for an area with nothing banked",
     "vintages.py",
     '    if not body["issuances"]:',
     "    if False:",
     "test_V5_unknown or test_PG_V5"),
    ("V6", "reg carries the shown (calibrated) figure where there is one",
     "vintages.py",
     'reg[i] = whole_mw(h["registry_mw"])',
     'reg[i] = whole_mw(h["shown_calibrated_mw"] if h["shown_calibrated_mw"] is not None '
     'else h["registry_mw"])',
     "test_V6_newest or test_V6_the_banked_outlook"),
    ("D1", "degree days to 0.1, not 0.01",
     "vintages.py",
     '_CENT = Decimal("0.01")',
     '_CENT = Decimal("0.1")',
     "test_D1_values"),
    ("D1", "the 08-29 orphans dropped as noise (an NWS fold lost)",
     "vintages.py",
     '"issuances": [_dd_issuance(per[i]) for i in sorted(per)]',
     '"issuances": [_dd_issuance(per[i]) for i in sorted(per) if i.month != 8]',
     "test_D1_nws"),
    ("D2", "an incomplete day is dropped",
     "vintages.py",
     '    for r in rows:\n        i = (_as_date(r["target_date"]) - d0).days\n',
     '    for r in rows:\n        if not r["basis_complete"]:\n            continue\n'
     '        i = (_as_date(r["target_date"]) - d0).days\n',
     "test_D2"),
    ("D-09-25-75", "the statement timeout dropped from the vintages read",
     "main.py",
     "                await cur.execute(\n"
     "                    f\"SET LOCAL statement_timeout = '{SOLAR_STATEMENT_TIMEOUT}'\")\n"
     "                await cur.execute(_vt.VINTAGES_SQL",
     "                await cur.execute(_vt.VINTAGES_SQL",
     "test_one_read_after_the_statement_timeout"),
    ("memo", "the memo key drops n (n = 4 would serve n = 12's body)",
     "main.py",
     "_solar_vintages_cache, (_so.TECH, area_kind, area, model, n_),",
     "_solar_vintages_cache, (_so.TECH, area_kind, area, model),",
     "test_memo_is_keyed_on_n"),
    ("one series", "the degree-day read stops naming its source",
     "vintages.py",
     "       AND source_product = %(source)s\n",
     "",
     "test_dd_one_read_per_source"),
]


def run(tree, k):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TESTS]
    if k:
        args += ["-k", k]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=600)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    print("# d091644 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091644_red_"))
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
