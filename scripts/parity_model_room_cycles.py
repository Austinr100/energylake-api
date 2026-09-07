#!/usr/bin/env python3
"""THE BLOCKING PARITY GATE for the /api/model-room/cycles O(1) re-base.

    OLD  the R2 archive walk   (main._cycles_from_r2_walk — the pinned handler)
    NEW  the render ledger     (main._MODEL_ROOM_CYCLES_SQL over d2_render_runs)

The 2026-09-06 spec moved the endpoint off a per-request list_objects_v2 walk
(measured 14,061-15,938 ms, 499s, p95 bucket 30 s) onto one indexed Postgres
query (measured 0.3 ms). That changes the STORE that answers "which cycles are
published". It must NOT change the ANSWER. This script is the proof, and it is
the receipt the captain reads before the arbiter is deleted.

    python scripts/parity_model_room_cycles.py
    python scripts/parity_model_room_cycles.py --days 14 --models gfs,ifs,aifs

Exit code 0 iff EVERY model's OLD and NEW payloads are byte-identical, so it can
gate CI. Non-zero on any divergence, with the diff printed cycle by cycle.

READ-ONLY, BOTH SIDES. The R2 token is read-only by provisioning; the SQL is a
SELECT. This script writes nothing, to either store, ever.

WHERE TO RUN IT. A box with egress to BOTH the R2 archive endpoint and Neon,
with the production env vars loaded (NEON_DATABASE_URL and the four R2_*):

    railway run python scripts/parity_model_room_cycles.py --days 14

The default Claude Code build environment cannot run this — its egress policy
blocks the Railway service host and the R2 endpoint, and Railway returns the
R2 secret redacted to a connected OAuth app. Same constraint, same posture as
scripts/verify_outlook_graphics.py: the gate ships runnable and the environment
that CAN run it runs it, rather than the gate being quietly downgraded to a
guess.

ON A DIVERGENCE — STOP. Do not reconcile it in code. A divergence means the R2
archive and the render ledger disagree about what is PUBLISHED, and that is a
defect worth more than this lane: either the pantry banked a ledger row for a
cycle whose manifest never landed (NEW-only), or it wrote a manifest without
banking the row (OLD-only). Both are bank-integrity findings. Report the diff.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# main.py is the source of truth for BOTH sides — the arbiter and the live SQL
# are imported, never re-implemented here. A parity gate that carries its own
# copy of the thing it is checking proves nothing.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


DEFAULT_MODELS = ("gfs", "ifs", "aifs")


def old_payload(model: str, days: int) -> dict:
    """The OLD endpoint's exact response body, arbiter-side: the walk's pairs,
    sorted and enveloped precisely as the pinned handler did."""
    found = main._cycles_from_r2_walk(model, days)
    found.sort(reverse=True)
    return {"cycles": [{"model": model, "date": d, "cycle": c} for d, c in found]}


async def new_payload(model: str, days: int) -> dict:
    """The NEW endpoint's exact response body, ledger-side: the live SQL and the
    live formatting, run through the app's own pool."""
    params = {"model": model, "cutoff": main._model_room_cutoff(days)}
    rows = await main._cockpit_read(main._MODEL_ROOM_CYCLES_SQL, params)
    return {"cycles": [
        {
            "model": model,
            "date": r["run_date"].strftime("%Y%m%d"),
            "cycle": f"{int(r['cycle']):02d}",
        }
        for r in rows
    ]}


def diff(old: dict, new: dict) -> tuple[list, list]:
    """(only-in-OLD, only-in-NEW) as (date, cycle) pairs, newest first."""
    o = {(r["date"], r["cycle"]) for r in old["cycles"]}
    n = {(r["date"], r["cycle"]) for r in new["cycles"]}
    return sorted(o - n, reverse=True), sorted(n - o, reverse=True)


async def run(models: list[str], days: int) -> int:
    if not main.DATABASE_URL:
        print("FATAL: NEON_DATABASE_URL is not set — the ledger side cannot run.")
        return 2
    if not main._model_room_configured():
        print("FATAL: the R2_* vars are not set — the arbiter side cannot run.")
        return 2

    from psycopg_pool import AsyncConnectionPool
    from psycopg.rows import dict_row

    # The app's own pool shape, opened for the length of the gate. The script
    # never touches a module-level connection of its own.
    main._pool = AsyncConnectionPool(
        conninfo=main.DATABASE_URL, min_size=1, max_size=2, open=False,
        kwargs={"row_factory": dict_row}, check=main._pool_pre_ping,
    )
    await main._pool.open()
    try:
        cutoff = main._model_room_cutoff(days)
        print(f"parity gate — days={days} (cutoff {cutoff}, inclusive), "
              f"models={','.join(models)}")
        print()
        failures = 0
        for model in models:
            old = old_payload(model, days)
            new = await new_payload(model, days)
            if old == new:
                print(f"  PASS  {model:5s}  {len(old['cycles']):3d} cycles, "
                      f"byte-identical")
                continue
            failures += 1
            only_old, only_new = diff(old, new)
            print(f"  FAIL  {model:5s}  OLD {len(old['cycles'])} cycles vs "
                  f"NEW {len(new['cycles'])} cycles")
            for d, c in only_old:
                print(f"          R2 manifest present, NO ledger row : "
                      f"{model} {d} {c}Z")
            for d, c in only_new:
                print(f"          ledger row banked, NO R2 manifest  : "
                      f"{model} {d} {c}Z")
            # Ordering can diverge even when the sets agree — say so explicitly.
            if not only_old and not only_new:
                print("          sets agree; ORDER differs — compare the "
                      "payloads directly")
        print()
        if failures:
            print(f"PARITY FAILED on {failures}/{len(models)} model(s). STOP — "
                  "do not delete the arbiter, do not reconcile in code. This is "
                  "a bank-integrity finding; report the diff above.")
            return 1
        print(f"PARITY PASSED on {len(models)}/{len(models)} models. The "
              "arbiter (main._cycles_from_r2_walk + its three R2 helpers) and "
              "this script may now be deleted.")
        return 0
    finally:
        await main._pool.close()
        main._pool = None


def main_cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=14,
                    help="the window both sides read (default 14, the spec's)")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="comma-separated feed slugs (default gfs,ifs,aifs)")
    args = ap.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    return asyncio.run(run(models, args.days))


if __name__ == "__main__":
    raise SystemExit(main_cli())
