"""d091689 receipts: the plans, the pinned statements and the banked bodies.

    python3 -B docs/receipts/press-api-d091689/receipts.py

Starts a throwaway Postgres (tests/load_press_d091689.cluster), loads the
2,000-row constructed bank (the two pantry vectors among them), and writes,
next to this file:

    pinned_sql.json        press.STATEMENTS, as the routes run them
    plans.md               EXPLAIN (ANALYZE, BUFFERS) of every statement, a summary
                           table, and timings (EXPLAIN's execution time, and the
                           client's execute + fetch, which detoasts the body)
    body_front.json        GET /api/press/front
    body_edition_article.json
                           GET /api/press/edition?kind=article&date=2026-10-10&slug=...
    body_editions_daily_n14.json
                           GET /api/press/editions?kind=daily
    bytes.psv              each body's bytes, raw and gzip -6

The bodies are compact JSON (ensure_ascii=False) without the cache block, at
load_press_d091689.NOW. Read-only against nothing but the throwaway cluster.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import statistics
import sys
import time
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import load_press_d091689 as lp  # noqa: E402
import press as pr  # noqa: E402

SLUG = "gas-carried-california-october-2026"
D = date(2026, 10, 10)
CASES = [
    ("FRONT_DAILY_SQL", {}, "/front: the newest daily in force, its first revision, the newest daily held"),
    ("FRONT_HEADERS_SQL", {}, "/front: newest weekly, newest monthly, five newest articles"),
    ("HEADERS_SQL", {"kind": "daily", "n": 14}, "/editions?kind=daily (default n)"),
    ("HEADERS_SQL", {"kind": "daily", "n": 60}, "/editions?kind=daily&n=60 (the cap)"),
    ("HEADERS_SQL", {"kind": "article", "n": 60}, "/editions?kind=article&n=60"),
    ("EDITION_SQL", {"kind": "daily", "date": D, "slug": ""}, "/edition?kind=daily&date=2026-10-10"),
    ("EDITION_SQL", {"kind": "article", "date": D, "slug": SLUG}, "/edition?kind=article&...&slug="),
    ("EDITION_SQL", {"kind": "daily", "date": date(2022, 10, 11), "slug": ""},
     "/edition, an identity with a withdrawn revision 1"),
    ("EDITION_AT_SQL", {"kind": "daily", "date": D, "slug": "", "revision": 0},
     "/edition?...&revision=0"),
]
BODIES = [("body_front.json", "/api/press/front"),
          ("body_edition_article.json", f"/api/press/edition?kind=article&date=2026-10-10&slug={SLUG}"),
          ("body_editions_daily_n14.json", "/api/press/editions?kind=daily")]


def explain(conn, sql, params, fmt):
    q = f"EXPLAIN (ANALYZE, BUFFERS{', FORMAT JSON' if fmt else ''}) " + sql
    rows = conn.execute(q, params).fetchall()
    return rows[0]["QUERY PLAN"][0] if fmt else "\n".join(r["QUERY PLAN"] for r in rows)


def walk(n, out):
    out.append(n)
    for c in n.get("Plans", []):
        walk(c, out)
    return out


def summary(conn, sql, params):
    for _ in range(3):                                   # warm
        explain(conn, sql, params, True)
    execs, fetches = [], []
    for _ in range(25):
        execs.append(explain(conn, sql, params, True)["Execution Time"])
        t0 = time.perf_counter()
        conn.execute(sql, params).fetchall()
        fetches.append((time.perf_counter() - t0) * 1000)
    j = explain(conn, sql, params, True)
    nodes = walk(j["Plan"], [])
    scans = [n for n in nodes if n.get("Relation Name") == "press_editions"]
    return {
        "scans": sorted({f"{n['Node Type']} on {n['Index Name']}" for n in scans}),
        "rows_read": sum(n["Actual Rows"] * n["Actual Loops"] + n.get("Rows Removed by Filter", 0)
                         for n in scans),
        "rows_out": j["Plan"]["Actual Rows"],
        "buffers": j["Plan"].get("Shared Hit Blocks", 0) + j["Plan"].get("Shared Read Blocks", 0),
        "exec_ms": statistics.median(execs),
        "fetch_ms": statistics.median(fetches),
    }


def bank_bodies(conn):
    import main
    from fastapi.testclient import TestClient
    main._utcnow = lambda: lp.NOW
    main._pool = lp.PgPool(conn)
    c = TestClient(main.app)
    sizes = []
    for fname, path in BODIES:
        for cache in main._PRESS_CACHES:
            cache.clear()
        r = c.get(path)
        assert r.status_code == 200, (path, r.text)
        body = {k: v for k, v in r.json().items() if k != "cache"}
        text = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        (HERE / fname).write_text(text)
        b = r.content
        sizes.append((fname, path, len(b), len(gzip.compress(b, 6))))
    (HERE / "bytes.psv").write_text(
        "file|path|raw_bytes_as_served|gzip6_bytes\n"
        + "".join(f"{f}|{p}|{raw}|{gz}\n" for f, p, raw, gz in sizes))


def main_():
    (HERE / "pinned_sql.json").write_text(json.dumps(pr.STATEMENTS, indent=1) + "\n")
    with lp.cluster() as conn:
        lp.constructed(conn)
        version = conn.execute("SELECT version() AS v").fetchone()["v"]
        size = conn.execute("""
            SELECT pg_size_pretty(pg_relation_size('press_editions')) AS heap,
                   pg_size_pretty(pg_total_relation_size('press_editions')) AS total,
                   pg_size_pretty(pg_relation_size('press_editions_identity')) AS idx""").fetchone()
        census = conn.execute("""
            SELECT kind, count(*) AS n_rows, count(DISTINCT (edition_date, slug)) AS identities,
                   count(withdrawn_at) AS withdrawn, min(edition_date) AS first,
                   max(edition_date) AS last
              FROM press_editions GROUP BY kind ORDER BY kind""").fetchall()
        rows, raws = [], []
        for name, params, what in CASES:
            s = summary(conn, pr.STATEMENTS[name], params)
            rows.append((name, params, what, s))
            raws.append((name, params, what, explain(conn, pr.STATEMENTS[name], params, False)))
        bank_bodies(conn)

    def p(params):
        return ", ".join(f"{k}={v}" for k, v in params.items()) or "-"
    out = [
        "# Plans — d091689, the press API",
        "",
        "**Where:** a throwaway local Postgres (`tests/load_press_d091689.cluster`), "
        f"`{version.split(',')[0]}`, holding `press_editions` exactly as spec d091689 §7 states it "
        "(`tests/fixtures/press_d091689/ddl_press_editions.sql`: the primary key and "
        "`press_editions_identity` are its only indexes) and **2,000 constructed rows** "
        "(`load_press_d091689.constructed`), every body a full el.edition.v1 document "
        "(the pantry vectors, re-dated), so the heap and TOAST are the real size.",
        "",
        "**The Neon plans are still owed.** `press_editions` does not exist on Neon yet "
        "(pantry lane d091688 is building it); these are not Neon's plans. When the "
        "migration lands, the same statements (`pinned_sql.json`) are to be planned there "
        "with `EXPLAIN (ANALYZE, BUFFERS)` and this file's table filled for Neon.",
        "",
        f"Table: heap {size['heap']}, total with TOAST and indexes {size['total']}, "
        f"`press_editions_identity` {size['idx']}.",
        "",
        "| kind | rows | identities | withdrawn | first | last |",
        "|---|---:|---:|---:|---|---|",
        *[f"| {r['kind']} | {r['n_rows']} | {r['identities']} | {r['withdrawn']} | {r['first']} | "
          f"{r['last']} |" for r in census],
        "",
        "## Summary",
        "",
        "Every read of `press_editions` is an index scan on `press_editions_identity`; no plan "
        "has a sequential scan or a sort. `rows read` counts rows the scans returned plus "
        "rows their filter removed: the newest walks stop at their LIMIT. Times are medians "
        "of 25 warm runs: `exec` is EXPLAIN's execution time (the body is not detoasted), "
        "`fetch` the client's execute + fetch of the real statement (it is).",
        "",
        "| statement | parameters | route | scans | rows read | rows out | buffers | exec ms | fetch ms |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
        *[f"| `{n}` | {p(prm)} | {w} | {'; '.join(s['scans'])} | {s['rows_read']} | {s['rows_out']} | "
          f"{s['buffers']} | {s['exec_ms']:.3f} | {s['fetch_ms']:.2f} |" for n, prm, w, s in rows],
        "",
        "## Raw plans",
        "",
    ]
    for name, params, what, text in raws:
        out += [f"### `{name}` ({p(params)}): {what}", "", "```", text, "```", ""]
    (HERE / "plans.md").write_text("\n".join(out))
    print((HERE / "plans.md").read_text().split("## Raw plans")[0])


if __name__ == "__main__":
    main_()
