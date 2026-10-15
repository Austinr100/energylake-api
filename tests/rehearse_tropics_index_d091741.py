"""d091741 mutation rehearsal: one break per rule, each must turn the suite red.

    python3 -B tests/rehearse_tropics_index_d091741.py > docs/receipts/tropics-api-index-d091741/reds.txt

Each break is a text substitution applied IN PLACE; the guarding tests run
(pytest -B, so no stale bytecode outlives a restore); the file is written back
from its original bytes. The tree is hashed before and after, and the clean
suite is run first and last. The brief's three (R): `default` bound (G1), the
in-force rule dropped (I2), the memo removed (G1 probes twice); then one break
per other rule the routes hold. Not a test module.
"""

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST = "tests/test_tropics_index_d091741.py"
SUITES = [TEST]
RECEIPT = "docs/receipts/tropics-api-index-d091741/reds.txt"

BREAKS = [
    # the brief's three
    ("R default", "the layer's template binds GIBS's `default` time", "tropics_index.py",
     '            "template": tile_url(layer, frame, "{z}", "{y}", "{x}"),',
     '            "template": tile_url(layer, "default", "{z}", "{y}", "{x}"),', "G1"),
    ("R default", "the probe asks the `default` tile instead of the slot", "tropics_index.py",
     "            res = await fetch(tile_url(layer, s))",
     '            res = await fetch(tile_url(layer, "default"))', "G1"),
    ("R in force", "the ledger read takes every banked copy, not the copy in force", "tropics_index.py",
     "      FROM tropical_file_vintage_in_force AS v\n      LEFT JOIN LATERAL (",
     "      FROM (SELECT t.*, COALESCE((t.meta -> 'correction' ->> 'revision')::int, 0) AS revision,\n"
     "                   (t.meta ? 'correction') AS corrected\n"
     "              FROM tropical_file_vintage t WHERE t.status = 'banked') AS v\n"
     "      LEFT JOIN LATERAL (", "I2"),
    ("R memo", "/ground builds on every call (the memo removed)", "main.py",
     "        payload, state, entry = await _tropics_ground_cache.serve(\n"
     "            layer, lambda: _tropics_ground_build(layer))",
     "        payload = await _tropics_ground_build(layer)\n"
     "        state, entry = \"miss\", _DDCacheEntry(payload, _utcnow(), time.monotonic(), 0.0)",
     "G1_the_memo"),
    # /ground
    ("ground", "GIBS's layer-time-actual is ignored (a snapped 200 counts)", "tropics_index.py",
     "        if actual is not None and actual != slot:\n            return \"absent\"",
     "        if False:\n            return \"absent\"", "G1"),
    ("ground", "recent_present lists an errored slot", "tropics_index.py",
     "        if v == \"present\":\n            present.append(s)",
     "        if v in (\"present\", \"error\"):\n            present.append(s)", "G1"),
    ("ground", "the window gives up after 12 slots, not 18", "tropics_index.py",
     "WINDOW_SLOTS = 18", "WINDOW_SLOTS = 12", "G1"),
    ("ground", "ep is drawn from GOES-East", "tropics_index.py",
     'GROUND_LAYERS_BY_BASIN = {"al": "GOES-East_ABI_GeoColor", "ep": "GOES-West_ABI_GeoColor",',
     'GROUND_LAYERS_BY_BASIN = {"al": "GOES-East_ABI_GeoColor", "ep": "GOES-East_ABI_GeoColor",', "G1"),
    # /products
    ("products", "surge is looked for in every basin (basin_has_none dropped)", "tropics_index.py",
     '    if name == "surge_ww" and basin not in SURGE_BASINS:',
     '    if False:', "I1"),
    ("products", "not_issued without NHC's word (an earlier link counts as this advisory's)",
     "tropics_index.py",
     "    if kind == \"ref\" and ref != advisory:",
     "    if kind == \"ref\" and ref == advisory:", "I1 or T1"),
    ("products", "the WSP cycle is the advisory's own time, not its synoptic time", "tropics_index.py",
     "        cycle = wsp_cycle(adv[\"init_ts\"]) if adv else None",
     "        cycle = wsp_cycle(adv[\"position_valid_ts\"]) if adv else None", "I1"),
    ("products", "the default advisory is the newest official forecast, not NHC's newest",
     "main.py",
     "        await cur.execute(_ti.ENTRY_NEWEST_SQL, {\"storm_id\": storm_id})\n        entry = await cur.fetchone()",
     "        entry = None", "I1 or T1"),
    ("I2", "the original's url is dropped from a corrected object", "tropics_index.py",
     "    return {\"url\": display_url(r[\"original_r2_key\"]), \"sha256\": r[\"original_sha256\"],",
     "    return {\"url\": None, \"sha256\": r[\"original_sha256\"],", "I2"),
    # /text
    ("text", "the two times are served as one", "tropics_index.py",
     '            "transmitted_utc": meta.get("transmitted_utc"), "forecaster": meta.get("forecaster"),',
     '            "transmitted_utc": meta.get("issued_utc"), "forecaster": meta.get("forecaster"),', "T1"),
    # /members
    ("members", "a banked parquet with no manifest is served as not_banked", "tropics_index.py",
     "                absent.append({\"cycle\": c, \"ensemble\": e, \"reason\": \"not_published\",",
     "                absent.append({\"cycle\": c, \"ensemble\": e, \"reason\": \"not_banked\",", "M1"),
    ("members", "too_few_members is the API's own floor (the recon's 5), not the manifest's", "tropics_index.py",
     "                if n is not None and floor is not None and n < floor:",
     "                if n is not None and n < 5:", "M1"),
    ("members", "/tracks's not_served no longer points at /members", "tropics.py",
     '        "no member column. This API reads no R2 per request. The members\' published "\n'
     '        "manifests (display bucket) are indexed by GET /api/weather/tropics/members"\n'
     '        "?storm_id=&cycle= (d091741)."),',
     '        "no member column. This API reads no R2 per request."),', "M1 or V1"),
    # plans
    ("X1", "the ledger read names no product (a scan of the storm's every file)", "tropics_index.py",
     "     WHERE v.source = ANY (%(sources)s::text[]) AND v.product = ANY (%(products)s::text[])\n"
     "       AND v.storm_id = %(storm_id)s AND v.vintage_key = %(vintage_key)s",
     "     WHERE v.source = ANY (%(sources)s::text[]) AND v.product LIKE ANY (%(products)s::text[])\n"
     "       AND v.vintage_key = %(vintage_key)s", "X1 or I1"),
    ("ground", "one httpx client kept across event loops (found live, 14:20Z)", "main.py",
     "        async with httpx.AsyncClient(timeout=GIBS_TIMEOUT_S, follow_redirects=False,\n"
     "                                     headers={\"User-Agent\": GIBS_USER_AGENT}) as client:",
     "        global _tropics_gibs_kept\n"
     "        _tropics_gibs_kept = globals().get(\"_tropics_gibs_kept\") or httpx.AsyncClient(\n"
     "            timeout=GIBS_TIMEOUT_S, follow_redirects=False, headers={\"User-Agent\": GIBS_USER_AGENT})\n"
     "        client = _tropics_gibs_kept\n"
     "        if True:", "service_path"),
    # every build sets the statement timeout first
    ("C", "the index builds set no statement timeout", "main.py",
     "                await cur.execute(f\"SET LOCAL statement_timeout = '{TROPICS_STATEMENT_TIMEOUT}'\")\n"
     "                storm = await _tropics_storm_or_404(cur, storm_id)\n"
     "                a, adv, entry = await _tropics_advisory(cur, storm_id, advisory)",
     "                storm = await _tropics_storm_or_404(cur, storm_id)\n"
     "                a, adv, entry = await _tropics_advisory(cur, storm_id, advisory)", "test_C"),
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
