"""d091666 reds (spec R): each break below is applied to a throwaway copy of
the tree, the tests that guard it must FAIL there, and the unbroken tree must
PASS them. The working tree is never edited, so it is restored byte for byte
by construction; the script also checks its md5 before and after.

    python docs/receipts/dd-board-d091666/rehearse.py > docs/receipts/dd-board-d091666/reds.txt
"""
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = "tests/test_dd_board_d091666.py"
FILES = ("dd_board.py", "main.py", "degree_days.py", TESTS)

BREAKS = [
    ("S1", "an unscored cell is given a MAE of 0",
     "dd_board.py",
     '            **{k: r[k] for k in SCORE_METRICS},\n            "excluded"',
     '            **{k: (r[k] if r["scored"] else 0.0) for k in SCORE_METRICS},\n            "excluded"',
     "test_S1_nothing_unscored"),
    ("S1", "unscored cells dropped from the scoreboard",
     "dd_board.py",
     '    for r in rows:\n        key = (r["place_kind"], r["place"], r["weighting"])',
     '    for r in rows:\n        if not r["scored"]:\n            continue\n'
     '        key = (r["place_kind"], r["place"], r["weighting"])',
     "test_S1_a_scored or test_S1_nothing_unscored"),
    ("D-09-25-75", "the scores read loses its statement timeout",
     "main.py",
     '_dd_timed_read(_db.SCORES_SQL, {"place_kind": k})',
     '_dd_read(_db.SCORES_SQL, {"place_kind": k})',
     "test_S1_one_read_per_place_kind"),
    ("D-09-25-75", "the scores read stops naming its place_kind",
     "dd_board.py",
     "     WHERE place_kind = %(place_kind)s\n",
     "     WHERE %(place_kind)s::text IS NOT NULL\n",
     "test_S1_place_kind or test_S6_every"),
    ("TTL", "the scores memo held an hour past the daily write",
     "main.py",
     "_DD_SCORES_TTL = 900.0",
     "_DD_SCORES_TTL = 3600.0",
     "test_S1_scores_memo"),
    ("S2", "the blend read goes to dd_blend_forecast",
     "dd_board.py",
     "      FROM v_dd_blend_drawable\n",
     "      FROM dd_blend_forecast\n",
     "test_S2_the_blend_never or test_S6_every"),
    ("S2", "what is not served is no longer stated",
     "dd_board.py",
     '        "not_served": BLEND_NOT_SERVED,',
     '        "not_served": None,',
     "test_S2_a_row_the_view"),
    ("S2", "an empty window is served as a bare empty list",
     "dd_board.py",
     '        "absence": None if shaped else {\n            "reason": "no_drawable_blend",',
     '        "absence": None if True else {\n            "reason": "no_drawable_blend",',
     "test_S2_no_drawable"),
    ("S3", "a member label made outside SOURCE_LABELS",
     "dd_board.py",
     '"members": [{"member": m, "label": _dd.source_label(m)} for m in members],',
     '"members": [{"member": m, "label": {"gridpoints_raw": "NWS"}.get(m, m)} for m in members],',
     "test_S3_every_label"),
    ("S3", "best_member printed as stored, not labelled",
     "dd_board.py",
     '"best_member_label": _dd.source_label(r["best_member"]),',
     '"best_member_label": r["best_member"],',
     "test_S3_labels_reach"),
    ("S4", "the banked p10/p90 served as a band",
     "dd_board.py",
     '        "band": None,\n        "percentile_rule": None,',
     '        "band": {"p05": 10.143, "p95": 11.432},\n        "percentile_rule": None,',
     "test_S4"),
    ("S5", "the desk serves a level where the change belongs",
     "dd_board.py",
     '                "change": c["change"],',
     '                "change": c["change"] and {**c["change"], "hdd": c["hdd"], "cdd": c["cdd"]},',
     "test_S5_the_desks_change or test_S5_the_change_is_never"),
    ("S5", "the score cell keyed by the Pacific date of the issuance",
     "dd_board.py",
     "    ts = datetime.fromisoformat(issued_ts).astimezone(timezone.utc)\n",
     "    ts = datetime.fromisoformat(issued_ts).astimezone(timezone.utc)"
     " - __import__('datetime').timedelta(hours=7)\n",
     "test_S5_the_score_cell"),
    ("S5", "not yet scored read off n_target_days, not the view's flag",
     "dd_board.py",
     '    return {"state": "scored" if cell["scored"] else "not_yet_scored",',
     '    return {"state": "scored" if cell["n_target_days"] >= 11 else "not_yet_scored",',
     "test_S5_the_score_cell or test_S5_not_yet or test_S5_the_state_is"),
    ("S5", "EL_BLEND accepted at the desk (a region blend invented)",
     "main.py",
     '    if source == "EL_BLEND":\n',
     '    if False:\n',
     "test_S5_desk_params"),
    ("S7", "the blend added to the region board's sources",
     "degree_days.py",
     "        for src in sorted(src_cells):\n            cs = src_cells[src]\n",
     "        src_cells.setdefault('EL_BLEND', [])\n"
     "        for src in sorted(src_cells):\n            cs = src_cells[src]\n",
     "test_S7"),
]


def md5s():
    return {f: hashlib.md5((ROOT / f).read_bytes()).hexdigest() for f in FILES}


def run(tree, k):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TESTS]
    if k:
        args += ["-k", k]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=600)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    before = md5s()
    print("# d091666 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091666_red_"))
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
    after = md5s()
    ok &= before == after
    print(f"\nclean tree: {'GREEN' if rc == 0 else 'RED'}  {tail}")
    print(f"tree restored byte for byte: {before == after} "
          f"({', '.join(f'{f} {m[:8]}' for f, m in after.items())})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
