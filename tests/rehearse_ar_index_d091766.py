"""d091766 mutation rehearsal: one break per rule, each must turn the suite red.

    python3 -B tests/rehearse_ar_index_d091766.py > docs/receipts/ar-index-d091766/reds.txt

Each break is a text substitution applied IN PLACE; the guarding tests run (pytest -B, so no
stale bytecode outlives a restore); the file is written back from its original bytes. The tree
is hashed before and after, and the clean suite is run first and last. The brief's four (R): a
probability served for an unscored cell (A2/A3), n250 served for an unscored cell (A2), DISTINCT
ON for the newest init (A5), a region derived with the band edge on the wrong side (A4b); then
one break per other rule the routes hold. Not a test module.
"""

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST = "tests/test_ar_index_d091766.py"
SUITES = [TEST]
RECEIPT = "docs/receipts/ar-index-d091766/reds.txt"

WITHHELD = "                withheld[str(thr)] = w\n"
BREAKS = [
    # the brief's four
    ("R p", "a probability is served for an unscored cell", "ar_index.py",
     WITHHELD,
     WITHHELD + "                row[f\"p{thr}\"] = round(r[f\"n{thr}\"] / r[\"n_present\"], 4)\n",
     "A2 or A3"),
    ("R n250", "n250 is served for an unscored cell", "ar_index.py",
     WITHHELD,
     WITHHELD + "                if thr == 250:\n                    row[\"n250\"] = r[\"n250\"]\n",
     "A2"),
    ("R distinct", "the newest init is read DISTINCT ON (level_set)", "ar_index.py",
     "    SELECT l.init_ts\n      FROM ar_landfall_latest AS l\n     WHERE l.level_set = %(level_set)s\n"
     "     ORDER BY l.point_code, l.valid_ts\n     LIMIT 1\n",
     "    SELECT DISTINCT ON (l.level_set) l.init_ts\n      FROM ar_landfall_latest AS l\n"
     "     WHERE l.level_set = %(level_set)s\n     ORDER BY l.level_set, l.init_ts DESC\n",
     "A5"),
    ("R band edge", "a band's top half-degree is derived into the band above", "ar_index.py",
     "           if Decimal(str(r[\"lat_min\"])) <= lat <= Decimal(str(r[\"lat_max\"]))]",
     "           if Decimal(str(r[\"lat_min\"])) - Decimal(\"0.5\") <= lat < Decimal(str(r[\"lat_max\"]))]",
     "A4b"),
    # the gate
    ("gate", "a scored, not-shown cell is served as shown (show is not None)", "ar_index.py",
     "    shown = row[\"show\"] is True", "    shown = row[\"show\"] is not None", "A3"),
    ("gate", "an unscored cell counts as shown", "ar_index.py",
     "                \"withheld\": {\"reason\": \"not_yet_scored\"}}",
     "                \"withheld\": None}", "A2"),
    ("no_cell", "lead 0 is put in d1-2 (gt_h <= lead_h)", "ar_index.py",
     "        if b[\"gt_h\"] < lead_h <= b[\"le_h\"]:", "        if b[\"gt_h\"] <= lead_h <= b[\"le_h\"]:",
     "A6"),
    ("no_cell", "no_cell loses the edges' rule", "ar_index.py",
     "        return {\"reason\": \"no_cell\", \"rule\": EDGES[\"lead_rule\"]}",
     "        return {\"reason\": \"no_cell\"}", "A6"),
    # the pin
    ("pin", "the pin is not checked (a changed constant still serves)", "ar_index.py",
     "    return edges_sha256() == EDGES_PIN[\"edges_sha256\"]", "    return True", "A4"),
    ("pin", "the pinned sha is mistyped", "ar_index.py",
     "    \"edges_sha256\": \"5b0068645d1c3514365f50f15a6900c15bdd6758cde6c1580c2e82ccfb6cc0fe\",",
     "    \"edges_sha256\": \"5b0068645d1c3514365f50f15a6900c15bdd6758cde6c1580c2e82ccfb6cc0ff\",", "A4"),
    # one region per point
    ("A4b", "a point whose rows disagree is silently picked, not refused", "ar_index.py",
     "        elif out[pc][:2] != (lat, lon):", "        elif False:", "A4b"),
    # the reads
    ("A5", "the index build sets no statement timeout", "main.py",
     "                await cur.execute(f\"SET LOCAL statement_timeout = '{AR_STATEMENT_TIMEOUT}'\")\n"
     "                await cur.execute(_ar.INDEX_SQL, p)",
     "                await cur.execute(_ar.INDEX_SQL, p)", "A5"),
    ("A5", "/landfall builds on every call (the memo removed)", "main.py",
     "    return await _solar_serve(_ar_landfall_cache, t, lambda: _ar_landfall_build(t), response)",
     "    _ar_landfall_cache.clear()\n"
     "    return await _solar_serve(_ar_landfall_cache, t, lambda: _ar_landfall_build(t), response)", "A5"),
    ("A5", "the coast_ivt LATERAL is pulled up (no LIMIT 1): a (level_set, kind) walk", "ar_index.py",
     "               AND d.display_key = {_CYCLE_KEY.format(kind=\"'coast_ivt'\")}\n             LIMIT 1) AS c ON true",
     "               AND d.display_key = {_CYCLE_KEY.format(kind=\"'coast_ivt'\")}) AS c ON true", "A5"),
    # the index
    ("A1", "the index serves the odds object's url", "ar_index.py",
     "                      \"coast_ivt\": coast, \"odds_published\": bool(r[\"odds_published\"])})",
     "                      \"coast_ivt\": coast, \"odds_published\": bool(r[\"odds_published\"]),\n"
     "                      \"odds_url\": display_url(r[\"coast_key\"].replace(\"coast_ivt\", \"odds\")) "
     "if r[\"coast_key\"] else None})", "A1"),
    ("A2", "the control is withheld with the counts", "ar_index.py",
     "               \"ens_mean\": _num(r[\"ens_mean\"]), \"ctrl\": _num(r[\"ctrl\"]),",
     "               \"ens_mean\": _num(r[\"ens_mean\"]), \"ctrl\": None,", "A2"),
]


def tree_hash() -> str:
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"],
                           capture_output=True, text=True, check=True).stdout.split()
    h = hashlib.sha256()
    for f in sorted(files):
        if f == RECEIPT:                    # this run's own output
            continue
        p = ROOT / f
        if p.is_file():
            h.update(f.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def run(k: str | None) -> tuple[bool, str]:
    cmd = [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *SUITES]
    if k:
        cmd += ["-k", k]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    last = [l for l in r.stdout.splitlines() if " passed" in l or " failed" in l or " error" in l]
    return r.returncode == 0, (last[-1] if last else r.stdout[-200:]).strip()


def main():
    before = tree_hash()
    ok, line = run(None)
    print(f"clean tree: {'GREEN' if ok else 'RED'} ({line})")
    assert ok
    reds = 0
    for i, (rule, what, rel, old, new, k) in enumerate(BREAKS, 1):
        path = ROOT / rel
        orig = path.read_bytes()
        text = orig.decode()
        assert text.count(old) == 1, (what, text.count(old))
        try:
            path.write_text(text.replace(old, new))
            green, line = run(k)
        finally:
            path.write_bytes(orig)
        reds += not green
        print(f"{i:2d}. {rule:10s} {what:72s} -> {'GREEN (missed)' if green else 'RED'}  [{line}]")
    ok, line = run(None)
    after = tree_hash()
    print(f"clean tree again: {'GREEN' if ok else 'RED'} ({line})")
    print(f"{reds} of {len(BREAKS)} breaks red; tree {'restored byte for byte' if before == after else 'CHANGED'} "
          f"(sha256 {before[:16]} -> {after[:16]})")
    sys.exit(0 if reds == len(BREAKS) and before == after and ok else 1)


if __name__ == "__main__":
    main()
