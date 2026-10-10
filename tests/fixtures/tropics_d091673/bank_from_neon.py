"""How bank/*.jsonl.gz were made from the Neon connector's saved results (d091673).

The connector cannot return the 11.5 MB bank in one message, so each table was
read as `SELECT c, count(*) n, min(id) lo, max(id) hi, string_agg(row_to_json(t)::text,
E'\\n' ORDER BY id) body ... WHERE <cut>` in chunks (manifest.json). The
connector saved each result as a JSON array; this script pulls the `body`
cells out in order, joins them with '\\n', checks the sha-256 Neon computed over
the same text at the cut, and only then writes the gzip. (The committed files
were written by `gzip -n -9` from the same text; Python's header differs in its
OS byte. The decompressed text, which the suite checks, is identical.)

    python3 -I bank_from_neon.py OUT_DIR KIND SHA256 SAVED_RESULT.json [...]

Not run by the suite; the suite re-checks every sha on load.
"""
import gzip
import hashlib
import json
import sys


def main(out_dir, kind, sha, *saved):
    lines = []
    for path in saved:
        for row in json.load(open(path)):
            body = row["body"].split("\n")
            assert len(body) == int(row["n"]), (path, row["c"])
            lines += body
    text = "\n".join(lines)
    got = hashlib.sha256(text.encode()).hexdigest()
    if got != sha:
        raise SystemExit(f"{kind}: sha256 {got} is not Neon's {sha}; nothing written")
    with open(f"{out_dir}/{kind}.jsonl.gz", "wb") as fh:
        fh.write(gzip.compress(text.encode(), compresslevel=9, mtime=0))
    print(kind, len(lines), got)


if __name__ == "__main__":
    main(*sys.argv[1:])
