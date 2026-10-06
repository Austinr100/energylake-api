"""d091623: write the replay from the Neon connector's answers.

    python docs/receipts/net-demand-reason-d091623/bank.py <session transcript .jsonl>

Each statement below was rendered by render.py (--rows) and run, as that
exact text, through the Neon connector (run_sql, read-only). This script
finds each statement's answer in the session's transcript (or in the file the
harness saved a long answer to), checks that the text run is the text
render.py renders, and writes
tests/fixtures/load_outlook_d091623/production_2026_10_06_1240z.json in
d091611's form (compact arrays in the statement's column order)."""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import render  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "load_outlook_d091623" / "production_2026_10_06_1240z.json"
NEWEST = "2026-10-06T06:00:00+00:00"          # both parts' newest issuance at NOW

# fixture key -> (statement, arg)
PLAN = {
    "usability": ("usability", ""),
    "fcst:CA ISO-TAC": ("fcst", "CA ISO-TAC"),
    "fcst:BPAT": ("fcst", "BPAT"),
    "fcst:LADWP": ("fcst", "LADWP"),
    "fcst:PGE-TAC": ("fcst", "PGE-TAC"),
    "df:BPAT": ("df", "BPAT"),
    "eia_d:BPAT": ("eia_d", "BPAT"),
    "df:LDWP": ("df", "LDWP"),
    "eia_d:LDWP": ("eia_d", "LDWP"),
    "native": ("native", ""),
    "hub": ("hub", ""),
    "truth": ("truth", ""),
    "newest:solar": ("gen_newest", "solar"),
    "newest:wind": ("gen_newest", "wind"),
    "gen:solar": ("gen_rows", f"solar@{NEWEST}"),
    "gen:wind": ("gen_rows", f"wind@{NEWEST}"),
    "bt:solar": ("bt_rows", "solar"),
    "bt:wind": ("bt_rows", "wind"),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def answers(transcript: pathlib.Path) -> dict:
    """{normalised sql: rows text} for every run_sql in the transcript."""
    uses, out = {}, {}
    for line in transcript.read_text().splitlines():
        d = json.loads(line)
        m = d.get("message")
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, list):
            continue
        for x in c:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "tool_use" and x.get("name", "").endswith("run_sql"):
                uses[x["id"]] = _norm(x["input"]["sql"])
            elif x.get("type") == "tool_result" and x.get("tool_use_id") in uses:
                body = x.get("content")
                text = body if isinstance(body, str) else "".join(
                    b.get("text", "") for b in body if isinstance(b, dict))
                saved = re.search(r"Output has been saved to (\S+?\.txt)", text)
                if saved:
                    text = pathlib.Path(saved.group(1)).read_text()
                if x.get("is_error") and not saved:
                    continue
                ans = json.loads(text)
                if ans and "rows" in ans[0]:
                    out[uses[x["tool_use_id"]]] = json.loads(ans[0]["rows"])
    return out


def main(transcript: str) -> None:
    got = answers(pathlib.Path(transcript))
    bank = {
        "_doc": ("d091623: production rows read 2026-10-06 ~12:40-13:00Z by the routes' own "
                 "statements (docs/receipts/net-demand-reason-d091623/render.py, NOW "
                 "2026-10-06T12:40Z), through the Neon connector, read-only. Rows are compact "
                 "arrays in the statement's column order."),
        "now": render.NOW.isoformat(),
        "columns": render.COLUMNS,
    }
    for key, (name, arg) in PLAN.items():
        sql = _norm(render.statement(name, arg, rows=True))
        if sql not in got:
            raise SystemExit(f"no answer banked for {key}")
        rows = got[sql]
        if name == "gen_newest":
            rows = [[r["init_ts"]] for r in rows]
        bank[key] = rows
    ids = sorted({r[6] for k in ("gen:solar", "gen:wind", "bt:solar", "bt:wind")
                  for r in bank[k] if r[6] is not None})
    sql = _norm(render.statement("lines", ",".join(map(str, ids)), rows=True))
    if sql not in got:
        print("lines to read:", ",".join(map(str, ids)))
        raise SystemExit(1)
    bank["lines"] = got[sql]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bank, separators=(",", ":")) + "\n")
    print(OUT, OUT.stat().st_size)


if __name__ == "__main__":
    main(sys.argv[1])
