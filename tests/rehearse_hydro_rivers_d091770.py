"""R — the mutation rehearsal for lane d091770's river routes.

Each break below is a plausible, tempting change to hydro_rivers.py. The rehearsal
applies it to a COPY of the tree, runs the suite against the copy, and records which
cells went red. A break that nothing catches is a hole in the suite, not a pass.

    python3 -I rehearse_hydro_rivers_d091770.py          # from tests/

The four the brief names, plus one for the publish rule the live bank forced:

  R1  DISTINCT ON for the newest record   (CLAUDE.md d091551 / D-08-02-L)
  R2  OR-ed series in the window read     (D-08-02-L)
  R3  a series served without its receipt (D-09-25-232 §2, D-08-02-R)
  R4  the gate drawable at n < 20         (nwm_reach_gate MIN_DAYS)
  R5  NWM velocity read into a body       (D-09-25-237)
  R6  'newest derived' instead of 'newest PUBLISHED' object (D-09-25-247 §3)

The tree hash is printed before and after: the rehearsal must leave no trace.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TARGET = "hydro_rivers.py"

BREAKS = [
    ("R1 DISTINCT ON for the newest record", TARGET,
     """    WITH keys AS (
        SELECT DISTINCT kind, source_code, source_key FROM geo_records
    )
    SELECT r.record_id, r.kind, r.source_code, r.source_key, r.name, r.lat, r.lon,
           r.position_basis, r.attrs, r.record_sha256, r.source_sha256, r.fetched_at
    FROM keys k
    CROSS JOIN LATERAL (
        SELECT g.* FROM geo_records g
        WHERE g.kind = k.kind AND g.source_code = k.source_code
          AND g.source_key = k.source_key
        ORDER BY g.fetched_at DESC, g.record_id DESC
        LIMIT 1
    ) AS r""",
     """    SELECT DISTINCT ON (kind, source_code, source_key)
           r.record_id, r.kind, r.source_code, r.source_key, r.name, r.lat, r.lon,
           r.position_basis, r.attrs, r.record_sha256, r.source_sha256, r.fetched_at
    FROM geo_records r
    ORDER BY kind, source_code, source_key, r.fetched_at DESC, r.record_id DESC"""),

    ("R2 OR-ed series in the window read", TARGET,
     "WHERE t.dataset = d.dataset AND t.series = d.series\n"
     "          AND t.ts >= %s AND t.ts < %s",
     "WHERE t.dataset = d.dataset AND t.series = ANY(ARRAY[d.series])\n"
     "          AND t.ts >= %s AND t.ts < %s"),

    ("R3 a series served without its receipt", TARGET,
     """            if rset is None or not rset["tags"]:""",
     """            if False:"""),

    ("R4 the gate drawable at n < 20", TARGET,
     "MIN_DAYS = 20       # units.MIN_DAYS",
     "MIN_DAYS = 0        # units.MIN_DAYS"),

    ("R5 NWM velocity read into a body", TARGET,
     '''        "velocity": {"absence": "never_served",
                     "why": "NWM velocity is never served (D-09-25-237)"},
    }''',
     '''        "velocity": {"dataset": "nwm_reach_analysis_hourly",
                     "series": "<fid>/velocity"},
    }'''),

    ("R6 newest derived instead of newest PUBLISHED", TARGET,
     """    LEFT JOIN LATERAL (
        SELECT g.*, d.display_key, d.sha256 AS published_sha256,
               d.bytes AS published_bytes, d.content_type, d.published_at
        FROM geo_layer_objects g
        JOIN geo_display_objects d ON d.object_id = g.object_id
        WHERE g.layer_code = c.layer_code AND g.role = 'derived'""",
     """    LEFT JOIN LATERAL (
        SELECT g.*, d.display_key, d.sha256 AS published_sha256,
               d.bytes AS published_bytes, d.content_type, d.published_at
        FROM geo_layer_objects g
        LEFT JOIN geo_display_objects d ON d.object_id = g.object_id
        WHERE g.layer_code = c.layer_code AND g.role = 'derived'"""),
]


def tree_hash(root: pathlib.Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts or "__pycache__" in p.parts or not p.is_file():
            continue
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def run_suite(root: pathlib.Path) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_hydro_rivers_d091770.py",
         "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, timeout=900)
    return r.returncode, r.stdout + r.stderr


def failed_cells(out: str) -> list[str]:
    return sorted({ln.split("::")[1].split()[0]
                   for ln in out.splitlines() if ln.startswith("FAILED ")}
                  | {"<collection error>"
                     for ln in out.splitlines() if ln.startswith("ERROR ")})


def main() -> int:
    before = tree_hash(REPO)
    print(f"tree {before[:16]}… before\n")
    rc, out = run_suite(REPO)
    assert rc == 0, f"the suite is not green before the rehearsal:\n{out[-3000:]}"
    print(f"baseline: green — {out.strip().splitlines()[-1]}\n")

    caught, missed = 0, []
    for name, rel, old, new in BREAKS:
        work = pathlib.Path(tempfile.mkdtemp(prefix="rehearse_rivers_"))
        tree = work / "tree"
        shutil.copytree(REPO, tree, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", "node_modules"))
        path = tree / rel
        src = path.read_text()
        if src.count(old) != 1:
            print(f"  {name}: ANCHOR NOT UNIQUE ({src.count(old)} hits) — rehearsal "
                  f"cannot apply it")
            missed.append(name)
            shutil.rmtree(work, ignore_errors=True)
            continue
        path.write_text(src.replace(old, new))
        rc, out = run_suite(tree)
        cells = failed_cells(out)
        if rc == 0:
            print(f"  {name}: NOT CAUGHT — the suite stayed green")
            missed.append(name)
        else:
            caught += 1
            print(f"  {name}: caught by {', '.join(cells) or '(nonzero exit)'}")
        shutil.rmtree(work, ignore_errors=True)

    after = tree_hash(REPO)
    print(f"\ntree {after[:16]}… after")
    assert after == before, "the rehearsal changed the tree"
    print(f"{caught} of {len(BREAKS)} breaks caught by name; tree unchanged")
    if missed:
        print("MISSED: " + "; ".join(missed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
