"""
d091491 §4 — the reds. For each: ONE edit, the WHOLE suite, then the file is
restored and its sha-256 checked against the original. Output → reds.txt.

    python docs/receipts/local-forecast-fast-today-d091491/reds.py
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).with_name("reds.txt")

REDS = [
    ("R1", "the NWS legs awaited one after another again", "T2", "nws_arm.py",
     """        legs = await asyncio.gather(forecast_leg(), obs_leg(), alerts_leg(),
                                    return_exceptions=True)""",
     """        legs = [await forecast_leg(), await obs_leg(), await alerts_leg()]"""),
    ("R2", "the ladders read in a dict comprehension again", "T4", "model_arm.py",
     """        got = await asyncio.gather(*(read_ladder(store, run_dt, p, lat, lon)
                                     for p in PARAMS), return_exceptions=True)""",
     """        got = list({p: await read_ladder(store, run_dt, p, lat, lon)
                    for p in PARAMS}.values())"""),
    ("R3", "the route reads the model arm twice on fallback", "T5", "main.py",
     """                run_dt, ladders = await model_task""",
     """                run_dt, ladders = await _model_arm.read(
                    _get_weather_store(), _local_gfs_run_candidates, flat, flon, timings)"""),
    ("R4", "refresh awaited on the request path", "T6", "model_arm.py",
     """                _start_refresh(store, candidates, clock)""",
     """                await _refresh(store, candidates, clock)"""),
    ("R5", "`hi` taken over every hour of the window", "T7", "model_arm.py",
     """    day = [h for h in hours if lf.solar_elevation(lat, lon, h["valid"]) > 0]""",
     """    day = list(hours)"""),
    ("R6", "the `condition` rule dropped from `_check_reasons`", "T8", "local_forecast.py",
     """    if kind in ("now", "hourly", "daily") and row.get("condition") == UNKNOWN \\""",
     """    if False and row.get("condition") == UNKNOWN \\"""),
    ("R7", "a duration written into `receipts.notes`", "T1", "main.py",
     """    with timings.mark("build"):
        payload = _lf.build_payload(**parts)""",
     """    parts["receipts"]["notes"].append(f"total {timings.durations()['total']:.1f} ms")
    with timings.mark("build"):
        payload = _lf.build_payload(**parts)"""),
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    lines = [f"d091491 reds — applied to {head}, whole suite each, sha-256 restore", ""]
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
        failed = sorted(set(re.findall(r"^FAILED (\S+?)(?: - |$)", r.stdout, re.M)))
        summary = next((l.strip("= ") for l in reversed(r.stdout.splitlines())
                        if re.search(r"\d+ (passed|failed)", l)), "(no summary)")
        tag = "d091491" if any(f"_{must}f_" in f for f in failed) else "MISSED"
        lines.append(f"{rid} — {what} ({fname}); must go red: {must}")
        lines.append(f"  {summary}")
        lines.append(f"  must-go-red {must}f: {'RED' if tag != 'MISSED' else 'NOT RED'}")
        for f in failed:
            lines.append(f"    {f}")
        lines.append(f"  restored: {fname} sha-256 {before}")
        lines.append("")
    OUT.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
