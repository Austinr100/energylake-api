"""d091673 mutation rehearsal: one break per rule, each must turn the suite red.

    python3 -B tests/rehearse_tropics_d091673.py > docs/receipts/tropics-api-d091673/reds.txt

Each break is a one-line text substitution applied IN PLACE; the guarding
tests run (pytest -B, so no stale bytecode outlives a restore); the file is
written back from its original bytes. The tree is hashed before and after,
and the clean tree is run first and last. Not a test module.
"""

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST = "tests/test_tropics_d091673.py"
RECEIPT = "docs/receipts/tropics-api-d091673/reds.txt"

BREAKS = [
    # rule 1: serve what the bank states; one label map
    ("1 verbatim", "an unknown source is mapped to a known label", "tropics.py",
     "    m = SOURCES.get(source)\n", "    m = SOURCES.get(source) or SOURCES[\"atcf:AVNO\"]\n", "T7"),
    ("1 verbatim", "a second place words a label", "tropics.py",
     '            series.append({"source": src, "label": m["label"],',
     '            series.append({"source": src, "label": src.upper(),', "T7 or V_"),
    ("1 verbatim", "positions are smoothed (rounded to whole degrees)", "tropics.py",
     '    out = {"tau_h": r["tau"], "valid_ts": _ts(r["valid_ts"]), "lat_deg": _f(r["lat"]),',
     '    out = {"tau_h": r["tau"], "valid_ts": _ts(r["valid_ts"]), "lat_deg": round(_f(r["lat"])),',
     "T5 or T4"),
    ("1 verbatim", "the API corrects valid_ts by NHC's 3 h", "tropics.py",
     '    out = {"tau_h": r["tau"], "valid_ts": _ts(r["valid_ts"]),',
     '    out = {"tau_h": r["tau"], "valid_ts": _ts(r["valid_ts"] + __import__("datetime").timedelta(hours=3)),',
     "T5 or R_served"),
    # rule 2: identity is the bank's storm_id
    ("2 identity", "storms sharing a name are merged", "tropics.py",
     '    storms = [storm_summary(s, by_storm.get(s["storm_id"], [])) for s in storm_rows]',
     '    storms = list({(current_name(s["names"]) or {}).get("name") or s["storm_id"]: '
     'storm_summary(s, by_storm.get(s["storm_id"], [])) for s in storm_rows}.values())',
     "T4"),
    # rule 3: members are never served
    ("3 members", "the member filter is dropped", "tropics.py",
     '    for r in rows:\n        if MEMBER_SOURCE.match(r["source"]):',
     '    for r in rows:\n        if False:', "T5"),
    # rule 4: an absence is stated
    ("4 absence", "no storms is an empty list with no absence", "tropics.py",
     "    if not storms:\n        hb =", "    if False:\n        hb =", "T2"),
    ("4 absence", "an unknown storm is a 200", "main.py",
     "    if not rows:\n        raise HTTPException(status_code=404,\n                            detail=f\"no storm with",
     "    if not rows:\n        return {'storm_id': storm_id, 'names': [], 'basin': '', 'number': 0, 'season': 0, 'status': '', 'aliases': [], 'first_seen': None, 'last_seen': None, 'last_lat': None, 'last_lon': None}\n        raise HTTPException(status_code=404,\n                            detail=f\"no storm with",
     "T8"),
    ("4 absence", "a missing threshold is not named", "tropics.py",
     "                    for kt in THRESHOLDS_KT if kt not in held]})",
     "                    for kt in () if kt not in held]})", "T6"),
    # rule 5: the player's cycles in one body
    ("5 one body", "/tracks serves only the newest cycle", "tropics.py",
     "        SELECT init_ts FROM cyc WHERE init_ts IS NOT NULL LIMIT %(n)s",
     "        SELECT init_ts FROM cyc WHERE init_ts IS NOT NULL LIMIT 1 + 0 * %(n)s", "T5"),
    ("5 one body", "the cycles are served newest first", "tropics.py",
     "    inits = sorted(i for i, rs in by_init.items() if any(r[\"source\"] not in NOT_A_SERIES for r in rs))",
     "    inits = sorted((i for i, rs in by_init.items() if any(r[\"source\"] not in NOT_A_SERIES for r in rs)), reverse=True)",
     "T5"),
    # rule 6: ECMWF CC BY 4.0
    ("6 attribution", "the ECMWF notice is dropped", "tropics.py",
     "    if kind == ECMWF:\n        return ECMWF_NOTICE.format(year=year)",
     "    if kind == ECMWF:\n        return None", "R_every_ecmwf"),
    # rule 7: repo rules
    ("7 repo", "no statement timeout on /tracks", "main.py",
     "                await cur.execute(f\"SET LOCAL statement_timeout = '{TROPICS_STATEMENT_TIMEOUT}'\")\n                storm = await _tropics_storm_or_404(cur, storm_id)\n                await cur.execute(_tr.TRACKS_SQL",
     "                storm = await _tropics_storm_or_404(cur, storm_id)\n                await cur.execute(_tr.TRACKS_SQL",
     "C_each"),
    ("7 repo", "the tracks memo key drops n", "main.py",
     "    return await _solar_serve(_tropics_tracks_cache, (sid, n_),",
     "    return await _solar_serve(_tropics_tracks_cache, (sid, 4),", "C_memo"),
    ("7 repo", "n above 8 is trimmed instead of refused", "tropics.py",
     "    if not 1 <= n <= TRACKS_N_MAX:\n        raise ValueError(f\"n must be 1-{TRACKS_N_MAX} model cycles, got {n}\")\n    return n",
     "    return max(1, min(n, TRACKS_N_MAX))", "T5"),
    ("7 repo", "the census's per-cycle read loses its index", "tropics.py",
     "     CROSS JOIN LATERAL (\n            SELECT source, count(*) AS n_points, max(tau) AS max_tau\n              FROM tropical_track_points_in_force\n             WHERE storm_id = %(storm_id)s AND init_ts = c.init_ts",
     "     CROSS JOIN LATERAL (\n            SELECT source, count(*) AS n_points, max(tau) AS max_tau\n              FROM tropical_track_points_in_force\n             WHERE storm_id = %(storm_id)s AND init_ts + interval '0 s' = c.init_ts",
     "T10"),
    ("7 repo", "a stale body is served past the ttl", "main.py",
     "_tropics_tracks_cache = _DDCache(\"weather/tropics/tracks\", TROPICS_MEMO_TTL, max_entries=48,\n",
     "_tropics_tracks_cache = _DDCache(\"weather/tropics/tracks\", TROPICS_MEMO_TTL, max_entries=48, allow_stale=True,\n",
     "C_memo"),
    # rules of shape
    ("shape", "advisories are ordered by the string", "tropics.py",
     '    out.sort(key=lambda a: (a["init_ts"], a["position_valid_ts"]))',
     '    out.sort(key=lambda a: a["advisory"])', "T4"),
    ("shape", "an east-positive lon is served as stored", "tropics.py",
     "    if lon > 180.0:\n        return round(lon - 360.0, 6)", "    if False:\n        return round(lon - 360.0, 6)", "T9"),
    ("shape", "below_1pct is served as 0", "tropics.py",
     '                "value_pct": None if r["below_1pct"] else _f(r["value"]),',
     '                "value_pct": _f(r["value"]),', "T6"),
    ("shape", "the poll grade is >= instead of the view's >", "tropics.py",
     "    elif now - hb > stale_after:", "    elif now - hb >= stale_after:", "T3"),
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
    cmd = [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", TEST]
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
        print(f"{i:2d}. rule {rule:13s} {what:55s} -> {'GREEN (missed)' if green else 'RED'}  [{line}]")
    ok, line = run(None)
    after = tree_hash()
    print(f"clean tree again: {'GREEN' if ok else 'RED'} ({line})")
    print(f"{reds} of {len(BREAKS)} breaks red; tree {'restored byte for byte' if before == after else 'CHANGED'} "
          f"(sha256 {before[:16]} -> {after[:16]})")
    sys.exit(0 if reds == len(BREAKS) and before == after and ok else 1)


if __name__ == "__main__":
    main()
