"""Build plans_raw.txt from the session transcript: each EXPLAIN statement in
explains.sql is matched, character for character, to the statement the Neon
connector ran, and that call's result lines are written verbatim."""
import json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
transcript = pathlib.Path(sys.argv[1])
text = (HERE / "explains.sql").read_text()
blocks = re.findall(r"^-- (\w+) (\w+_SQL) (\{.*?\})\n(.*?);\s*$", text, re.M | re.S)

calls, results = {}, {}
for line in transcript.read_text().splitlines():
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    msg = ev.get("message") or {}
    for part in msg.get("content") or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "tool_use" and "sql" in (part.get("input") or {}):
            calls[part["id"]] = part["input"]["sql"]
        if part.get("type") == "tool_result":
            c = part.get("content")
            if isinstance(c, list):
                c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
            results[part["tool_use_id"]] = c

out = []
for tag, name, params, stmt in blocks:
    hits = [i for i, s in calls.items() if s.strip() == stmt.strip()]
    if not hits:
        sys.exit(f"{tag}: no connector call ran this exact statement")
    rows = json.loads(results[hits[-1]])
    out.append(f"== {tag} {name} {params}")
    out.extend(r["QUERY PLAN"] for r in rows)
    out.append("")
# Superseded, kept as the evidence for CURVES_SQL's OFFSET 0 fence: the same
# read with the view's LATERAL unfenced (n = 14, 814temp, KSAN), warm.
unfenced = [i for i, s in calls.items() if s.startswith("EXPLAIN (ANALYZE, BUFFERS)")
            and "dv.method_version = w.method_version) d ON TRUE" in s]
if unfenced:
    out.append("== C00b CURVES_SQL before the OFFSET 0 fence (superseded; not the pinned SQL) "
               '{"product": "814temp", "place_kind": "station", "place": "USW00023188", '
               '"weighting": "", "n": 14}')
    out.extend(r["QUERY PLAN"] for r in json.loads(results[unfenced[0]]))
    out.append("")
(HERE / "plans_raw.txt").write_text("\n".join(out))
print(len(blocks), "plans written")
