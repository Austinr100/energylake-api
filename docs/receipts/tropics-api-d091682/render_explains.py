"""Render every Tropics statement twice, d091673's (the bare tables, from git at
the lane's base) and d091682's draft (the rows in force: that tropics.py with
draft_rows_in_force.patch applied: held back by STOP-V at the first firing, applied
as tropics.py on the re-fire after pantry 297),
with the literal parameters their Neon plans were taken with, by psycopg's own
client-side binding, into explains_before.sql and explains_after.sql.

    python3 docs/receipts/tropics-api-d091682/render_explains.py
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent
BASE = "c40c231"                       # main with d091673 merged: this lane's base
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from psycopg import ClientCursor  # noqa: E402

import load_bank_d091673 as lb  # noqa: E402


def module_at(ref: str, patch: pathlib.Path | None = None):
    """tropics.py at a git ref, optionally with a patch applied, as a module."""
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:tropics.py"], check=True,
                         capture_output=True, text=True).stdout
    d = pathlib.Path(tempfile.mkdtemp())
    name = "tropics_draft" if patch else "tropics_before"
    (d / "tropics.py").write_text(src)
    if patch:
        subprocess.run(["patch", "-s", "-o", str(d / f"{name}.py"), str(d / "tropics.py"),
                        str(patch)], check=True)
    else:
        (d / "tropics.py").rename(d / f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, d / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def draft():
    return module_at(BASE, HERE / "draft_rows_in_force.patch")


def statements(tr):
    sid, storms = "ep182026", ["al092026", "ep182026", "ep202026"]
    return [("POLL_SQL", tr.POLL_SQL, None),
            ("STORMS_SQL", tr.STORMS_SQL, {"scope": "active", "recent_days": 7}),
            ("OFFICIAL_NEWEST_SQL", tr.OFFICIAL_NEWEST_SQL, {"storm_ids": storms}),
            ("STORM_SQL", tr.STORM_SQL, {"storm_id": sid}),
            ("OBSERVED_SQL", tr.OBSERVED_SQL, {"storm_id": sid}),
            ("CYCLES_SQL", tr.CYCLES_SQL, {"storm_id": sid}),
            ("TRACKS_SQL n=8", tr.TRACKS_SQL, {"storm_id": sid, "n": 8}),
            ("TRACKS_SQL n=4", tr.TRACKS_SQL, {"storm_id": sid, "n": 4}),
            ("ODDS_SQL", tr.ODDS_SQL, {"storm_id": "al092026"})]


if __name__ == "__main__":
    with lb.cluster(load_bank=False) as conn:
        cur = ClientCursor(conn)
        for name, tr in (("before", module_at(BASE)), ("after", draft())):
            out = [f"-- d091682: the Tropics statements {name} the move to the rows in force "
                   f"({'git ' + BASE + ':tropics.py' if name == 'before' else 'the draft patch'}),",
                   "-- bound by psycopg's ClientCursor.mogrify with the parameters their Neon "
                   "plans were taken with.", ""]
            for label, sql, params in statements(tr):
                out += [f"-- {label} {params or ''}".rstrip(), cur.mogrify(sql, params).strip() + ";", ""]
            (HERE / f"explains_{name}.sql").write_text("\n".join(out))
