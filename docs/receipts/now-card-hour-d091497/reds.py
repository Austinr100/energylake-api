"""
d091497 §A — the reds. For each: ONE edit, the WHOLE suite, then the file is
restored and its sha-256 checked against the original. Output → reds.txt.

    python docs/receipts/now-card-hour-d091497/reds.py
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).with_name("reds.txt")

REDS = [
    ("R1", "`s_now = series[0]`", "N1", "model_arm.py",
     """        now_row = _hour_row(series[h_now], label)""",
     """        now_row = _hour_row(series[0], label)"""),
    ("R2", "`now.source` hard-coded to f000", "N1 N2", "model_arm.py",
     """    now["source"] = now_source""",
     '    now["source"] = f"model · {label} f000"'),
    ("R3", "`age_min` counted from `s_now.valid`", "N3", "model_arm.py",
     """    now["age_min"] = int((generated_at - run_dt).total_seconds() // 60)""",
     """    now["age_min"] = int((generated_at - lf.parse_iso(now["valid"])).total_seconds() // 60)"""),
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    lines = [f"d091497 reds — applied to {head}, whole suite each, sha-256 restore", ""]
    for rid, what, must, fname, old, new in REDS:
        path = ROOT / fname
        orig, before = path.read_bytes(), sha(path)
        src = orig.decode()
        assert src.count(old) == 1, f"{rid}: the edit does not apply exactly once"
        path.write_text(src.replace(old, new))
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                "-rf"], cwd=ROOT, capture_output=True, text=True, timeout=900)
        finally:
            path.write_bytes(orig)
        assert sha(path) == before, f"{rid}: restore failed"
        failed = sorted(set(re.findall(r"^FAILED (.+?)(?: - |$)", r.stdout, re.M)))
        summary = next((l.strip("= ") for l in reversed(r.stdout.splitlines())
                        if re.search(r"\d+ (passed|failed)", l)), "(no summary)")
        red = {m: any(f"test_{m}_" in f for f in failed) for m in must.split()}
        lines.append(f"{rid} — {what} ({fname}); must go red: {must}")
        lines.append(f"  {summary}")
        for m, ok in red.items():
            lines.append(f"  must-go-red {m}: {'RED' if ok else 'NOT RED'}")
        for f in failed:
            lines.append(f"    {f}")
        lines.append(f"  restored: {fname} sha-256 {before}")
        lines.append("")
    OUT.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
