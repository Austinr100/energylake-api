"""d091693 reds (spec R): one break per rule, each applied to a throwaway copy
of the tree; the tests that guard it must FAIL there, and the unbroken tree
must PASS them. The working tree is never edited, so it is restored byte for
byte by construction; the script also checks its sha-256 before and after.

    python docs/receipts/cpc-api-flip-v2-d091693/rehearse.py > docs/receipts/cpc-api-flip-v2-d091693/reds.txt

The rules: (1) the constant is cpc_curves_v2; (2) it is the only literal;
(3) every reference to the three relations carries the pin, one break per
reference (ten); (4) clause 5 reads the cell's min_base_years; (5) base_reason
rides verbatim; (6) DRAWABLE_RULE quotes 298; (7) the count rides on every
cell; (8) and on every curve; (9) the writer's label rides verbatim.
"""
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = ["tests/test_cpc_flip_v2_d091693.py", "tests/test_cpc_outlooks_d091679.py"]
FILES = ("cpc_outlooks.py", "main.py", *TESTS, "tests/load_bank_d091693.py")
F = "cpc_outlooks.py"
V1 = "test_V1_every_route or test_V1_v1_rows_beside or test_V1_the_vectors"
V4 = "test_V4_every_statement"

BREAKS = [
    ("rule 1", "the constant set back to cpc_curves_v1", F,
     'CPC_METHOD_VERSION = "cpc_curves_v2"', 'CPC_METHOD_VERSION = "cpc_curves_v1"',
     f"{V1} or test_V4_one_constant"),
    ("rule 2", "a second literal beside the constant", F,
     "_PIN = f\"'{CPC_METHOD_VERSION}'\"", "_PIN = \"'cpc_curves_v2'\"",
     "test_V4_one_constant"),
    ("rule 3", "CURVES_SQL: the issuances (iss) unpinned", F,
     "WHERE ci.product = %(product)s AND ci.method_version = {_PIN}",
     "WHERE ci.product = %(product)s", V4),
    ("rule 3", "CURVES_SQL: the window row (w) unpinned", F,
     "            AND w.method_version = {_PIN}\n", "", f"{V1} or {V4}"),
    ("rule 3", "CURVES_SQL: the verdict-in-force lateral (nv) unpinned", F,
     "WHERE cv.method_hash = w.method_hash AND cv.method_version = {_PIN}",
     "WHERE cv.method_hash = w.method_hash", V4),
    ("rule 3", "CURVES_SQL: the verdict cell (vv) unpinned", F,
     "            AND vv.method_version = {_PIN}\n", "", V4),
    ("rule 3", "CURVES_SQL: the view lateral (dv) unpinned", F,
     "               AND dv.method_version = {_PIN}\n", "", V4),
    ("rule 3", "PLACES_SQL: the hashes unpinned", F,
     "        SELECT DISTINCT hv.method_hash FROM cpc_curve_verdicts hv\n"
     "         WHERE hv.method_version = {_PIN}\n",
     "        SELECT DISTINCT hv.method_hash FROM cpc_curve_verdicts hv\n", V4),
    ("rule 3", "PLACES_SQL: the in-force lateral (lv) unpinned", F,
     "WHERE lv.method_hash = h.method_hash AND lv.method_version = {_PIN}",
     "WHERE lv.method_hash = h.method_hash", V4),
    ("rule 3", "PLACES_SQL: the cells (v) unpinned", F,
     "     WHERE v.method_version = {_PIN}\n", "", f"{V1} or {V4}"),
    ("rule 3", "PLACES_NEWEST_SQL: the newest issuance (lc) unpinned", F,
     "WHERE lc.product = p.product AND lc.method_version = {_PIN}",
     "WHERE lc.product = p.product", V4),
    ("rule 3", "PLACES_NEWEST_SQL: the window rows (c) unpinned", F,
     "AND c.day_index IS NULL AND c.method_version = {_PIN}",
     "AND c.day_index IS NULL", f"{V1} or {V4}"),
    ("rule 4", "clause 5 at a hard-coded 30", F,
     'if cell["history_years_in_base"] < cell["min_base_years"]:',
     'if cell["history_years_in_base"] < 30:', "test_V3"),
    ("rule 4", "clause 5 at a hard-coded 22 (v2's floor, not the cell's)", F,
     'if cell["history_years_in_base"] < cell["min_base_years"]:',
     'if cell["history_years_in_base"] < 22:', "test_V3"),
    ("rule 5", "a base_reason derived from the count", F,
     '"base_reason": cell["base_reason"]})',
     '"base_reason": cell["base_reason"] or '
     'f"on {cell[\'history_years_in_base\']} base years"})', "test_V3"),
    ("rule 6", "DRAWABLE_RULE quoting 286's >= 30", F,
     '"drawable = (verdict = \'beats\' AND history_years_in_base >= min_base_years "',
     '"drawable = (verdict = \'beats\' AND history_years_in_base >= 30 "', "test_V3"),
    ("rule 7", "a cell's base_by_station not served", F,
     '"min_base_years", "base_reason", "base_by_station", "base_fewest_stations",',
     '"min_base_years", "base_reason", "base_fewest_stations",', "test_V2 or test_V3"),
    ("rule 8", "a curve's count not served", F,
     "            **{k: head[k] for k in CURVE_BASE_KEYS},\n", "", "test_V2"),
    ("rule 9", "the label's count trimmed (the API's own words, not the writer's)", F,
     '"label": head["label"],', '"label": head["label"].split(", on ")[0],', "test_V2"),
]


def shas():
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in FILES}


def run(tree, k):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS]
    if k:
        args += ["-k", k]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=900)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    before = shas()
    print("# d091693 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091693_red_"))
        try:
            shutil.copytree(ROOT, tmp / "t", ignore=shutil.ignore_patterns(".git", "__pycache__"))
            path = tmp / "t" / f
            src = path.read_text()
            if src.count(old) != 1:
                print(f"{tag:<7} BREAK NOT APPLIED ({what}): needle found {src.count(old)} times")
                ok = False
                continue
            path.write_text(src.replace(old, new))
            rc, tail = run(tmp / "t", k)
            red = rc == 1
            ok &= red
            print(f"{tag:<7} {'RED ' if red else 'NOT RED'}  {what}\n        -k '{k}': {tail}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    rc, tail = run(ROOT, "")
    ok &= rc == 0
    after = shas()
    ok &= before == after
    print(f"\nclean tree: {'GREEN' if rc == 0 else 'RED'}  {tail}")
    print(f"tree restored byte for byte: {before == after} "
          f"({', '.join(f'{f} {m[:12]}' for f, m in after.items())})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
