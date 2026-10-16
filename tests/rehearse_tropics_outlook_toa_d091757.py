"""d091757 mutation rehearsal: one break per rule, each must turn its named tests red.

    python3 -B tests/rehearse_tropics_outlook_toa_d091757.py > docs/receipts/tropics-api-outlook-toa-d091757/reds.txt

Each break is one or more text substitutions applied IN PLACE; the named tests run
(pytest -B, so no stale bytecode outlives a restore); every file is written back from its
original bytes. The tree is hashed before and after, and the clean suites run first and
last. The brief's two come first: the stamp key mistaken for an hour (four ways) and r<n>
mistaken for an issuance (three ways). Two breaks are DIAGNOSTICS, expected GREEN: 8 is
break 7 with its text pin set aside, to show that no banked vector can see it (no TWO row
is corrected); 12 drops TOA from the top-level revision, which no banked vector can see
either (every corrected TOA identity's cone is corrected too). Breaks 9, 10 and 14 are the
label table (D-09-25-242 §1(d)): a key the row does not carry, a timezone not the row's,
labels.by_source dropped. Not a test module.
"""

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = "tests/test_tropics_outlook_toa_d091757.py"
ALL = [SUITE, "tests/test_tropics_index_d091741.py", "tests/test_tropics_revision_read_d091746.py"]
RECEIPT = "docs/receipts/tropics-api-outlook-toa-d091757/reds.txt"

HOUR_CHECK = '    if re.match(r"^[0-9]{10}$", raw):'
RN_CHECK = '    if re.match(r"^[0-9]{12}r[0-9]+$", raw):'
STAMP_RE = 'STAMP = re.compile(r"^20[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])([01][0-9]|2[0-3])[0-5][0-9]$")'
STRPTIME = '    datetime.strptime(raw, "%Y%m%d%H%M")        # 202602311200 is refused, not rolled'
VIEW_MATCH = "       AND v.advisory_or_cycle = %(advisory_or_cycle)s"
LABEL_BLOCK = '"labels": {k: meta[k] for k in TOA_LABEL_KEYS if k in meta}}'

# (rule, what, [(file, old, new), ...], -k, expected: "red" | "green")
BREAKS = [
    # the stamp key mistaken for an hour
    ("hour a", "?stamp= takes an hour (YYYYMMDDHH) as an issuance",
     [("tropics_index.py", HOUR_CHECK, "    if False:"),
      ("tropics_index.py", STAMP_RE, STAMP_RE.replace("[0-5][0-9]$", "([0-5][0-9])?$")),
      ("tropics_index.py", STRPTIME, "")],
     "test_S_an_hour_is_not_a_stamp", "red"),
    ("hour b", "the text is paired to the graphics' hour (key bound cut to YYYYMMDDHH)",
     [("tropics_index.py", '"key_below": (at_or_before or STAMP_MAX) + "s"}',
       '"key_below": (at_or_before or STAMP_MAX)[:10] + "s"}')],
     "test_P_the_text_is_paired_by_its_own_stamp", "red"),
    ("hour c", "the view is read at the stamp's hour, not the stamp",
     [("tropics_index.py", VIEW_MATCH,
       "       AND left(v.advisory_or_cycle, 10) = left(%(advisory_or_cycle)s, 10)")],
     "test_S_two_outlooks_of_one_hour_are_two_issuances", "red"),
    ("hour d", "the text is paired by the top of the graphics' hour (the hour NHC writes)",
     [("main.py", "_ti.outlook_stamp_params(text_name, at))",
       "_ti.outlook_stamp_params(text_name, at and at[:10] + '00'))")],
     "test_P_the_text_is_paired_by_its_own_stamp", "red"),
    # r<n> mistaken for an issuance
    ("r<n> a", "?stamp= takes a correction's key (<stamp>r<n>) as an issuance",
     [("tropics_index.py", RN_CHECK, "    if False:"),
      ("tropics_index.py", STAMP_RE, STAMP_RE.replace("[0-5][0-9]$", "[0-5][0-9](r[0-9]+)?$")),
      ("tropics_index.py", STRPTIME, "")],
     "test_S_r_n_is_not_an_issuance", "red"),
    ("r<n> b", "the display read takes the key (054r1) for its own advisory (vintage_key, not advisory_or_cycle)",
     [("tropics_index.py", VIEW_MATCH, "       AND v.vintage_key = %(advisory_or_cycle)s")],
     "test_T_each_correction_is_served_as_its_r1", "red"),
    ("r<n> c", "the stamp read returns the key, not its 12-digit base",
     [("tropics_index.py", "    SELECT substring(a.vintage_key FROM '^[0-9]{12}') AS stamp",
       "    SELECT a.vintage_key AS stamp")],
     "test_S_r_n_is_not_an_issuance", "red"),
    ("r<n> diag", "the same, the text pin set aside: what the banked vectors alone can see",
     [("tropics_index.py", "    SELECT substring(a.vintage_key FROM '^[0-9]{12}') AS stamp",
       "    SELECT a.vintage_key AS stamp")],
     "not test_S_r_n_is_not_an_issuance and not test_C and not X1", "green"),
    # the label table
    ("T labels", "the block serves a key the row does not carry (label_rule, D-09-05-T)",
     [("tropics_index.py", LABEL_BLOCK,
       '"labels": {**{k: meta[k] for k in TOA_LABEL_KEYS if k in meta}, "label_rule": meta.get("label_rule")}}')],
     "test_T_the_label_block_serves_only_keys_the_row_carries", "red"),
    ("T labels", "a timezone filled in over the row's own (every banked row carries one)",
     [("tropics_index.py", LABEL_BLOCK,
       '"labels": {**{k: meta[k] for k in TOA_LABEL_KEYS if k in meta}, "timezone": "UTC"}}')],
     "test_T_every_banked_identity or test_T_the_054", "red"),
    ("T toa", "/products reads no TOA (the pairs dropped from the route's read)",
     [("main.py", "_ti.ADVISORY_PRODUCTS + _ti.TOA_PRODUCTS, with_wsp=True)",
       "_ti.ADVISORY_PRODUCTS, with_wsp=True)")],
     "test_T_", "red"),
    ("T toa", "TOA's top-level revision not counted",
     [("tropics_index.py", '"revision": max((o["revision"] for o in objects + tobj), default=0),',
       '"revision": max((o["revision"] for o in objects), default=0),')],
     "test_T_", "green"),
    # the basin
    ("P basin", "cp served the Atlantic text",
     [("tropics_index.py", '"cp": "two_text_ep"}', '"cp": "two_text_at"}')],
     "test_P_", "red"),
    # the label table again (D-09-25-242 §1(d))
    ("T labels", "labels.by_source dropped from the served object",
     [("tropics_index.py", LABEL_BLOCK,
       '"labels": {k: ({x: y for x, y in meta[k].items() if x != "by_source"} if k == "labels" else meta[k])\n'
       '                       for k in TOA_LABEL_KEYS if k in meta}}')],
     "test_T_every_banked_identity", "red"),
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


def run(suites, k: str | None) -> tuple[bool, str, list]:
    cmd = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rf", *suites]
    if k:
        cmd += ["-k", k]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    out = r.stdout.splitlines()
    last = [l for l in out if " passed" in l or " failed" in l or " error" in l]
    failed = sorted({l.split("::")[1].split("[")[0] for l in out if l.startswith("FAILED ")})
    return r.returncode == 0, (last[-1] if last else r.stdout[-200:]).strip(), failed


def main():
    before = tree_hash()
    ok, line, _ = run(ALL, None)
    print(f"clean tree (the three Tropics index modules): {'GREEN' if ok else 'RED'} ({line})")
    assert ok
    as_expected = 0
    for i, (rule, what, subs, k, expect) in enumerate(BREAKS, 1):
        origs = {}
        try:
            for rel, old, new in subs:
                path = ROOT / rel
                origs.setdefault(path, path.read_bytes())
                text = path.read_text()
                assert text.count(old) == 1, (what, rel, text.count(old))
                path.write_text(text.replace(old, new))
            green, line, failed = run([SUITE], k)
        finally:
            for path, orig in origs.items():
                path.write_bytes(orig)
        got = "green" if green else "red"
        as_expected += got == expect
        verdict = ("RED" if not green else "GREEN (missed)") if expect == "red" else \
            ("GREEN (diagnostic, as expected)" if green else "RED (diagnostic: unexpected)")
        print(f"{i:2d}. {rule:9s} {what}\n      -k {k!r} -> {verdict}  [{line}]")
        for f in failed:
            print(f"      red: {f}")
    ok, line, _ = run(ALL, None)
    after = tree_hash()
    diag = sum(e == "green" for *_, e in BREAKS)
    print(f"clean tree again: {'GREEN' if ok else 'RED'} ({line})")
    print(f"{as_expected} of {len(BREAKS)} as expected ({len(BREAKS) - diag} breaks red, {diag} diagnostics "
          f"green); tree {'restored byte for byte' if before == after else 'CHANGED'} "
          f"(sha256 {before[:16]} -> {after[:16]})")
    sys.exit(0 if as_expected == len(BREAKS) and before == after and ok else 1)


if __name__ == "__main__":
    main()
