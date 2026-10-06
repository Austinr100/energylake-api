"""d091623 reds (R5): each break below is applied to a throwaway copy of the
tree, the tests that guard it must FAIL there, and the unbroken tree must PASS.

    python docs/receipts/net-demand-reason-d091623/rehearse.py > docs/receipts/net-demand-reason-d091623/reds.txt
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]

BREAKS = [
    ("R1", "the old first branch restored: no figure reads registry_only before the line",
     "load_outlook.py",
     '    if cid is None:\n        return None, "registry_only"\n',
     '    if cal is None or cid is None:\n        return None, "registry_only"\n',
     "test_R1 or test_R2"),
    ("R3", "clause (b) dropped: the newest unscored day and its parts",
     "load_outlook.py",
     '        out.append(f"the newest unscored day, {u[\'day\']}, stopped on "\n'
     '                   + " and ".join(_stop_words(s) for s in u["stopped_by"]))\n',
     "",
     "test_R3"),
    ("R3", "clause (c) dropped: the most frequent stop",
     "load_outlook.py",
     '        out.append(f"most often {part}: {ABSENT.get(why, why)}, "\n'
     '                   f"on {k} of {len(unscored)} unscored days")\n',
     "",
     "test_R3"),
    ("R3", "a reason on a scored cell",
     "load_outlook.py",
     '    if ours["status"] != "scored":\n        ours["reason"]',
     '    if True:\n        ours["reason"]',
     "test_R3"),
    ("R3", "the stop's PT hour, lead and hours absent dropped",
     "load_outlook.py",
     '    return out + (f" ({\', \'.join(bits)})" if bits else "")\n',
     '    return out\n',
     "test_R3"),
    ("R3", "ties go by reason code before part order",
     "load_outlook.py",
     "        (part, why), k = min(tally.items(), key=lambda kv: (\n"
     "            -kv[1], STOP_PART_ORDER.index(kv[0][0])",
     "        (part, why), k = min(tally.items(), key=lambda kv: (\n"
     "            -kv[1], kv[0][1], STOP_PART_ORDER.index(kv[0][0])",
     "test_R3"),
]

TESTS = ["tests/test_net_demand_reason_d091623.py", "tests/test_load_outlook.py",
         "tests/test_outlook_fit_d091608.py"]


def run(tree, k):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS]
    if k:
        args += ["-k", k]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    print("# d091623 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091623_red_"))
        try:
            shutil.copytree(ROOT, tmp / "t", ignore=shutil.ignore_patterns(".git", "__pycache__"))
            path = tmp / "t" / f
            src = path.read_text()
            if src.count(old) != 1:
                print(f"{tag}  BREAK NOT APPLIED ({what}): needle found {src.count(old)} times")
                ok = False
                continue
            path.write_text(src.replace(old, new))
            rc, tail = run(tmp / "t", k)
            red = rc != 0
            ok &= red
            print(f"{tag:<4} {'RED ' if red else 'NOT RED'}  {what}\n     -k '{k}': {tail}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    rc, tail = run(ROOT, "")
    ok &= rc == 0
    print(f"\nclean tree: {'GREEN' if rc == 0 else 'RED'}  {tail}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
