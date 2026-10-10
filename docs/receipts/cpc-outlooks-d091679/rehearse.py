"""d091679 reds (spec R): one break per rule, each applied to a throwaway copy
of the tree; the tests that guard it must FAIL there, and the unbroken tree
must PASS them. The working tree is never edited, so it is restored byte for
byte by construction; the script also checks its sha-256 before and after.

    python docs/receipts/cpc-outlooks-d091679/rehearse.py > docs/receipts/cpc-outlooks-d091679/reds.txt
"""
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = "tests/test_cpc_outlooks_d091679.py"
FILES = ("cpc_outlooks.py", "main.py", TESTS)

BREAKS = [
    # (1) serve what the bank states
    ("rule 1", "an equal-odds column served as the reweighted one",
     "cpc_outlooks.py",
     'def _values(r: dict) -> dict:\n    return {k: r[k] for k in VALUE_COLS}',
     'def _values(r: dict) -> dict:\n    return {k: r[k.replace("eq_", "")] for k in VALUE_COLS}',
     "test_T1_curves_equal"),
    ("rule 1", "a percentile rounded on the way out",
     "cpc_outlooks.py",
     'def _values(r: dict) -> dict:\n    return {k: r[k] for k in VALUE_COLS}',
     'def _values(r: dict) -> dict:\n    return {k: round(r[k], 1) for k in VALUE_COLS}',
     "test_T1_curves_equal"),
    ("rule 1", "notes dropped",
     "cpc_outlooks.py",
     '        "notes": r0["w_notes"],',
     '        "notes": [],',
     "test_T1_curves_equal"),
    ("rule 1", "a verdict cell field restated (band_share rounded)",
     "cpc_outlooks.py",
     '         **{k: r[prefix + k] for k in CELL_KEYS},',
     '         **{k: (round(r[prefix + k], 2) if k == "band_share" and r[prefix + k] '
     'is not None else r[prefix + k]) for k in CELL_KEYS},',
     "test_T5_every_cell"),
    # (2) a curve only where the bank says drawable; never re-derived
    ("rule 2", "the curve read from cpc_outlook_curves, not the view",
     "cpc_outlooks.py",
     "              FROM v_cpc_curves_drawable dv\n",
     "              FROM cpc_outlook_curves dv\n",
     "test_T8_every_read_is_pinned or test_S_every_read"),
    ("rule 2", "drawable derived from the fields, not the column",
     "cpc_outlooks.py",
     '    c = {"season": r[prefix + "season"], "strength": r[prefix + "strength"],\n'
     '         "drawable": r[prefix + "drawable"],',
     '    c = {"season": r[prefix + "season"], "strength": r[prefix + "strength"],\n'
     '         "drawable": r[prefix + "verdict"] == "beats" and '
     'r[prefix + "history_years_in_base"] >= 30,',
     "test_S_drawable_is_the_banks_column"),
    ("rule 2", "a curve kept when the view holds no row (made from the window row)",
     "cpc_outlooks.py",
     '    view = [r for r in rows if r["day_index"] is not None or r["label"] is not None]',
     '    view = [r for r in rows if r["day_index"] is not None or r["label"] is not None '
     'or r["w_season"] is not None]',
     "test_S_a_curve_is_the_views or test_T2_a_written"),
    ("rule 2", "a not-drawable cell served without its reasons",
     "cpc_outlooks.py",
     '                   "reasons": verdict["reasons"]}',
     '                   "reasons": []}',
     "test_T2_a_written"),
    ("rule 2", "the place's reason codes left off a place with no curve",
     "cpc_outlooks.py",
     '        if absence is not None and absence["reason"] == "not_written" and pv:',
     '        if False:',
     "test_T2_a_place_with_no_curve"),
    ("rule 2", "reasons read the base-years floor as 25",
     "cpc_outlooks.py",
     '    if cell["history_years_in_base"] < 30:',
     '    if cell["history_years_in_base"] < 25:',
     "test_T2_every_undrawable"),
    # (3) ruling 172's label rides through
    ("rule 3", "the band sentence replaced by the API's own words",
     "cpc_outlooks.py",
     '            "band_sentence": head["band_sentence"],',
     '            "band_sentence": "The band is overconfident when CPC is confident.",',
     "test_T3"),
    ("rule 3", "the band claim dropped from the curve",
     "cpc_outlooks.py",
     '            "band_claim": head["band_claim"],',
     '            "band_claim": None,',
     "test_T3 or test_T1_curves_equal"),
    # (4) nothing on the play loop's step path costs a round trip
    ("rule 4", "vintages served newest first",
     "cpc_outlooks.py",
     '    return [groups[k] for k in sorted(groups)]',
     '    return [groups[k] for k in sorted(groups, reverse=True)]',
     "test_T4_vintages_are"),
    ("rule 4", "vintages read one issuance (a round trip per step)",
     "main.py",
     '"place": pl, "weighting": w, "n": n_})',
     '"place": pl, "weighting": w, "n": 1})',
     "test_T4"),
    ("rule 4", "n above the cap trimmed to 14, not refused",
     "main.py",
     '        if not 1 <= n_ <= _co.VINTAGES_MAX_N:',
     '        n_ = min(n_, _co.VINTAGES_MAX_N)\n        if not 1 <= n_ <= _co.VINTAGES_MAX_N:',
     "test_T4_n_outside"),
    # (5) unbanked outlooks are a stated absence
    ("rule 5", "an unbanked product silently left out",
     "cpc_outlooks.py",
     '            prods.append({"product": product, "cpc_title": CPC_TITLES[product],\n'
     '                          "issued_date": None,',
     '            continue\n'
     '            prods.append({"product": product, "cpc_title": CPC_TITLES[product],\n'
     '                          "issued_date": None,',
     "test_T6"),
    ("rule 5", "banked-but-unparsed told as not banked",
     "cpc_outlooks.py",
     '            elif bytes_newest:',
     '            elif False:',
     "test_T6_bytes_banked"),
    # (6) reads, memo, timeout, plan receipts
    ("rule 6", "a curve read loses its statement timeout",
     "main.py",
     '            _dd_timed_read(_co.CURVES_SQL, {"product": p,',
     '            _dd_read(_co.CURVES_SQL, {"product": p,',
     "test_S_every_read or test_T7_database"),
    ("rule 6", "the curves memo held an hour",
     "main.py",
     "_CPC_TTL = 900.0",
     "_CPC_TTL = 3600.0",
     "test_S_memo"),
    ("rule 6", "the verdicts in force picked by DISTINCT ON (d091551)",
     "cpc_outlooks.py",
     "    WITH hashes AS (\n        SELECT DISTINCT method_hash FROM cpc_curve_verdicts\n",
     "    WITH hashes AS (\n        SELECT DISTINCT ON (method_hash) method_hash FROM cpc_curve_verdicts\n",
     "test_T8"),
    ("rule 6", "the view's LATERAL unfenced (a walk of the place's rows)",
     "cpc_outlooks.py",
     "               AND dv.method_version = w.method_version\n            OFFSET 0) d ON TRUE",
     "               AND dv.method_version = w.method_version) d ON TRUE",
     "test_T8"),
    ("rule 6", "the newest issuances read per place (the trap: an unwritten place walks)",
     "cpc_outlooks.py",
     "        SELECT DISTINCT issued_date\n          FROM cpc_outlook_curves\n"
     "         WHERE product = %(product)s\n",
     "        SELECT DISTINCT issued_date\n          FROM cpc_outlook_curves\n"
     "         WHERE product = %(product)s AND place = %(place)s\n",
     "test_T8"),
    ("rule 6", "an unknown place read anyway (no 404)",
     "main.py",
     '    if key not in places["_known"]:\n        raise _cpc_404(*key)',
     '    if False:\n        raise _cpc_404(*key)',
     "test_T7_unknown"),
    ("rule 6", "a down database answered 200 with an empty body",
     "main.py",
     '    except asyncio.TimeoutError:\n        raise _dd_503_on_timeout("the outlooks", _CPC_BUILD_TIMEOUT)',
     '    except (asyncio.TimeoutError, HTTPException):\n        return {"families": []}',
     "test_T7_database"),
]


def shas():
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in FILES}


def run(tree, k):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TESTS]
    if k:
        args += ["-k", k]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=600)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    before = shas()
    print("# d091679 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091679_red_"))
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
            red = rc != 0
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
