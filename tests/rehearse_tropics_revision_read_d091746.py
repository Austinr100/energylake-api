"""d091746 mutation rehearsal: one break per rule, each must turn the suite red.

    python3 -B tests/rehearse_tropics_revision_read_d091746.py > docs/receipts/tropics-api-revision-read-d091746/reds.txt

Each break is a text substitution applied IN PLACE; the guarding tests run
(pytest -B, so no stale bytecode outlives a restore); the file is written back
from its original bytes. The tree is hashed before and after, and the clean
suites are run first and last. The brief's three (R) come first:
`advisory_or_cycle` -> `vintage_key` in DISPLAY_FILES_SQL, `revision DESC`
dropped, the fixture's r1 rows deleted. Break 3 is a DIAGNOSTIC, expected GREEN:
the brief's second break made where neither 316's own post-check nor the
ddl_316.sql pin can see it, to show what I2 alone can tell on the banked rows
(every r1 there is also the newest publish by vintage_id). Not a test module.
"""

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITES = ["tests/test_tropics_index_d091741.py", "tests/test_tropics_revision_read_d091746.py"]
RECEIPT = "docs/receipts/tropics-api-revision-read-d091746/reds.txt"

NO_REVISION_ORDER = (
    "    conn.execute(\"\"\"CREATE OR REPLACE VIEW tropical_display_in_force AS\n"
    "        SELECT DISTINCT ON (v.product, v.storm_id, substring(v.vintage_key FROM "
    "'^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})'))\n"
    "               v.vintage_id, v.dataset, v.product, v.storm_id,\n"
    "               substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})') AS advisory_or_cycle,\n"
    "               COALESCE(substring(v.vintage_key FROM 'r([1-9][0-9]*)$')::int, 0) AS revision,\n"
    "               v.vintage_key, v.fetch_ts AS published_ts, v.issued_ts, v.r2_key, v.byte_size, "
    "v.sha256, v.meta\n"
    "          FROM tropical_file_vintage v\n"
    "         WHERE v.source = 'display' AND v.status = 'banked'\n"
    "         ORDER BY v.product, v.storm_id, substring(v.vintage_key FROM "
    "'^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})'),\n"
    "                  v.fetch_ts DESC, v.vintage_id DESC\"\"\")\n")

# (rule, what, file, old, new, -k, expected: "red" | "green")
BREAKS = [
    # the brief's three
    ("R a", "DISPLAY_FILES_SQL asks vintage_key, not advisory_or_cycle", "tropics_index.py",
     "       AND v.advisory_or_cycle = %(advisory_or_cycle)s",
     "       AND v.vintage_key = %(advisory_or_cycle)s", "I2", "red"),
    ("R b", "the view's revision DESC dropped (316 as loaded, ddl_316.sql)",
     "tests/fixtures/tropics_d091741/ddl_316.sql",
     "          COALESCE(substring(v.vintage_key FROM 'r([1-9][0-9]*)$')::int, 0) DESC,\n"
     "          v.fetch_ts DESC, v.vintage_id DESC;",
     "          v.fetch_ts DESC, v.vintage_id DESC;", "I2", "red"),
    ("R b diag", "the same, past 316's checks and the pin (a view re-made after 316)",
     "tests/load_bank_d091741.py",
     "    conn.execute(ddl_316())                          # pantry 316, verbatim: its checks run here\n",
     "    conn.execute(ddl_316())                          # pantry 316, verbatim: its checks run here\n"
     + NO_REVISION_ORDER, "I2", "green"),
    ("R c", "the fixture's r1 rows deleted (054r1, 016r1)", "tests/load_bank_d091741.py",
     "    for kind in LOAD_ORDER[1:] + BANKED_D091746:\n        _insert_bank(conn, kind)\n",
     "    for kind in LOAD_ORDER[1:] + BANKED_D091746:\n        _insert_bank(conn, kind)\n"
     "    conn.execute(\"DELETE FROM tropical_file_vintage WHERE source = 'display' \"\n"
     "                 \"AND vintage_key IN ('054r1', '016r1')\")\n", "I2", "red"),
    # the original
    ("I2", "the original's probe never runs (original_not_held for every correction)", "tropics_index.py",
     "             WHERE v.revision > 0\n               AND a.source = 'display'",
     "             WHERE false\n               AND a.source = 'display'", "I2", "red"),
    ("I2", "the original is stated only where the bytes differ", "tropics_index.py",
     "    if not r.get(\"corrected\"):\n        return None",
     "    if not r.get(\"corrected\") or r.get(\"original_sha256\") == r.get(\"sha256\"):\n        return None",
     "I2", "red"),
    ("I2", "/text's top-level revision is not the max over its products", "tropics_index.py",
     "                \"revision\": max((o[\"revision\"] for o in served), default=0),",
     "                \"revision\": 0,", "I2", "red"),
    # /text
    ("T2", "/text reads a `sections` key again", "tropics_index.py",
     "            \"sections\": [{\"kind\": k} for k in (meta.get(\"section_kinds\") or [])],",
     "            \"sections\": [{\"title\": s.get(\"title\"), \"kind\": s.get(\"kind\")}\n"
     "                         for s in (meta.get(\"sections\") or [])],", "T2 or T1", "red"),
    ("T2", "/text serves no title", "tropics_index.py",
     "            \"title\": meta.get(\"title\"), \"n_forecast_rows\": meta.get(\"n_forecast_rows\"),",
     "            \"title\": None, \"n_forecast_rows\": meta.get(\"n_forecast_rows\"),", "T2 or T1", "red"),
    # WSP
    ("I3", "the WSP display read asks the storm, not storm_id ''", "main.py",
     "                                          _ti.display_files_params(\"\", cycle, _ti.WSP_PRODUCTS))",
     "                                          _ti.display_files_params(storm_id, cycle, _ti.WSP_PRODUCTS))",
     "I3", "red"),
    # V1
    ("V1", "FILES_SQL admits display rows through 294's view again", "tropics_index.py",
     "     WHERE v.source = 'nhc' AND v.product = ANY (%(products)s::text[])\n"
     "       AND v.storm_id = %(storm_id)s AND v.vintage_key = %(vintage_key)s",
     "     WHERE v.source IN ('nhc', 'display') AND v.product = ANY (%(products)s::text[])\n"
     "       AND v.storm_id = %(storm_id)s AND v.vintage_key = %(vintage_key)s", "V1", "red"),
    ("V1", "/members reads the manifests through 294's view again", "tropics_index.py",
     "     WHERE v.source = 'parsed' AND v.product = ANY (%(products)s::text[])",
     "     WHERE v.source IN ('parsed', 'display') AND v.product = ANY (%(products)s::text[])",
     "V1", "red"),
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
    cmd = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", *SUITES]
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
    as_expected = 0
    for i, (rule, what, rel, old, new, k, expect) in enumerate(BREAKS, 1):
        path = ROOT / rel
        orig = path.read_bytes()
        text = orig.decode()
        assert text.count(old) == 1, (what, text.count(old))
        try:
            path.write_text(text.replace(old, new))
            green, line = run(k)
        finally:
            path.write_bytes(orig)
        got = "green" if green else "red"
        as_expected += got == expect
        verdict = ("RED" if not green else "GREEN (missed)") if expect == "red" else \
            ("GREEN (diagnostic, as expected)" if green else "RED (diagnostic: unexpected)")
        print(f"{i:2d}. {rule:9s} {what:76s} -> {verdict}  [{line}]")
    ok, line = run(None)
    after = tree_hash()
    reds = sum(e == "red" for *_, e in BREAKS)
    print(f"clean tree again: {'GREEN' if ok else 'RED'} ({line})")
    print(f"{as_expected} of {len(BREAKS)} as expected ({reds} breaks red, 1 diagnostic green); tree "
          f"{'restored byte for byte' if before == after else 'CHANGED'} (sha256 {before[:16]} -> {after[:16]})")
    sys.exit(0 if as_expected == len(BREAKS) and before == after and ok else 1)


if __name__ == "__main__":
    main()
