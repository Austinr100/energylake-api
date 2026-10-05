"""Step 1 over the re-banked production rows (newest issuance of both techs).

For every calibrated area: the hours the gate withholds (lit / dark), the
calibration_gaps the route would serve, the seam, and, for wind, the served
body hour by hour against TODAY's (main's wind_outlook over main's derived
lines), naming every difference. Today's bodies are built by main's modules
in a subprocess and banked as tests/fixtures/wind_outlook_d091590/
main_body_2026_10_05_00z.json (A3 compares against it).

    python docs/receipts/outlook-fit-d091608/step1.py > docs/receipts/outlook-fit-d091608/step1.txt
"""
import json, pathlib, subprocess, sys, tempfile, textwrap

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
WIND_MAIN_BODY = ROOT / "tests" / "fixtures" / "wind_outlook_d091590" / "main_body_2026_10_05_00z.json"
KIND = {"NP15": "hub", "ZP26": "hub", "SP15": "hub", "HUBSUM": "hub_sum", "CISO": "ba"}


def _load(tech):
    import importlib.util
    path = ROOT / "docs" / "receipts" / ("solar-outlook-api-d091568" if tech == "solar"
                                         else "wind-outlook-api-d091590") / "sample.py"
    spec = importlib.util.spec_from_file_location(f"{tech}_sample", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.load()


def main_bodies():
    """Wind's served bodies as main builds them, over the same rows with the
    lines main's CALIBRATION_SQL derives."""
    d = pathlib.Path(tempfile.mkdtemp())
    for f in ("solar_outlook.py", "wind_outlook.py"):
        (d / f).write_text(subprocess.run(["git", "-C", str(ROOT), "show", f"main:{f}"],
                                          capture_output=True, text=True, check=True).stdout)
    code = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(d)!r})
        import wind_outlook as wo
        assert "derived by this route" in wo.FIT_LEADS_BASIS
        sys.path.insert(1, {str(ROOT / 'docs' / 'receipts' / 'wind-outlook-api-d091590')!r})
        sys.path.insert(1, {str(ROOT)!r})
        import importlib.util
        spec = importlib.util.spec_from_file_location("s", {str(ROOT / 'docs/receipts/wind-outlook-api-d091590/sample.py')!r})
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        b = m.load()
        out = {{}}
        for area, kind in {KIND!r}.items():
            a = b["areas"][area]
            out[area] = wo.build_outlook(area_kind=kind, area=area, model="hrrr_gfs",
                issuance=a["issuance"], hours=a["hours"], score_rows=a["scores"],
                lines=a["lines_derived"], actual_rows=a["actuals"], site_rows=b["sites"])
        print(json.dumps(out, sort_keys=True))
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=d)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append((f"{path}.{k}", "<absent>", b[k]))
            elif k not in b:
                out.append((f"{path}.{k}", a[k], "<absent>"))
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [(path, a, b)]


def withheld(body):
    lit = lambda h: h["registry_mw"] is not None and h["registry_mw"] > 0
    beyond = [h for h in body["hours"] if h["calibrated_absent_reason"] == "beyond_fitted_leads"]
    return {"beyond_lit": sum(lit(h) for h in beyond), "beyond_dark": sum(not lit(h) for h in beyond),
            "beyond_leads_lit": [h["lead_h"] for h in beyond if lit(h)],
            "served_calibrated": sum(h["calibrated_mw"] is not None for h in body["hours"]),
            "no_line": sum(h["calibrated_absent_reason"] == "no_line" for h in body["hours"])}


if __name__ == "__main__":
    import solar_outlook as so
    import wind_outlook as wo
    sb, wb = _load("solar"), _load("wind")
    print("# Step 1 — the gate over the newest issuance of each tech (re-banked rows)\n")
    new = {}
    for tech, b, m in (("solar", sb, so), ("wind", wb, wo)):
        for area, kind in KIND.items():
            a = b["areas"][area]
            kw = dict(area_kind=kind, area=area, model=m.DEFAULT_MODEL, issuance=a["issuance"],
                      hours=a["hours"], score_rows=a["scores"], lines=a["lines"], actual_rows=a["actuals"])
            body = (m.build_outlook(**kw, fleet_rows=a["fleet"]) if tech == "solar"
                    else m.build_outlook(**kw, site_rows=b["sites"]))
            new[(tech, area)] = body
            stored = sum(h["calibrated_mw"] is not None for h in a["hours"])
            print(f"## {tech} {area}  init {body['issuance']['init_ts']}")
            print(f"stored calibrated hours: {stored}; withheld: {json.dumps(withheld(body))}")
            print(f"seams.calibration: {json.dumps(body['seams']['calibration'])}")
            print(f"calibration_gaps ({len(body['calibration_gaps'])}):")
            for g in body["calibration_gaps"]:
                print(f"  {json.dumps(g)}")
            print()
    today = main_bodies()
    WIND_MAIN_BODY.write_text(json.dumps(today, sort_keys=True, separators=(",", ":")))
    print("# Wind: served body (this branch) against main's, same rows, every difference\n")
    for area in KIND:
        n = json.loads(json.dumps(new[("wind", area)]))
        ds = diff(today[area], n)
        hour_ds = [d for d in ds if d[0].startswith(".hours[")]
        print(f"## wind {area}: {len(ds)} differences, {len(hour_ds)} of them in hours[]")
        for p, x, y in ds:
            print(f"  {p}: {json.dumps(x)[:120]} -> {json.dumps(y)[:120]}")
        print()
