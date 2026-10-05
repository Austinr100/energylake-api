"""What the solar page (dashboard @ a0609c4, solarOutlook.ts `seams` and
`bandBasis`, unchanged by d091609) prints over this branch's body, if d091609
is not live: its seams are every change of drawn figure between consecutive
hours, dark hours included; a band is drawn "calibrated" only if every hour
of it is. Replicated here line for line; run over the re-banked HUBSUM rows.

    python docs/receipts/outlook-fit-d091608/page_before_d091609.py
"""
import importlib.util, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import solar_outlook as so

spec = importlib.util.spec_from_file_location("s", ROOT / "docs/receipts/solar-outlook-api-d091568/sample.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
a = m.load()["areas"]["HUBSUM"]


def page(hours):
    basis = ["calibrated" if h["calibrated_mw"] is not None else "registry" for h in hours]
    seams = [(hours[i - 1]["lead_h"], hours[i]["lead_h"], basis[i - 1], basis[i])
             for i in range(1, len(hours)) if basis[i] != basis[i - 1]]
    bands = {}
    for h, b in zip(hours, basis):
        bands.setdefault(h["lead_band"], []).append(b)
    return seams, {k: ("calibrated" if all(x == "calibrated" for x in v) else "registry")
                   for k, v in bands.items()}


main_hours = [{**h, "calibrated_mw": h["calibrated_mw"]} for h in a["hours"]]       # main serves as stored
body = so.build_outlook(area_kind="hub_sum", area="HUBSUM", model="gfs", issuance=a["issuance"],
                        hours=a["hours"], score_rows=a["scores"], lines=a["lines"],
                        actual_rows=a["actuals"], fleet_rows=a["fleet"])
for name, hs in (("main (ungated)", main_hours), ("this branch (gated)", body["hours"])):
    seams, bands = page(hs)
    lit = [s for s in seams if True]
    print(f"{name}: {len(seams)} seams printed")
    for s in seams:
        print(f"  lead {s[0]} -> {s[1]}: {s[2]} -> {s[3]}")
    print(f"  band basis (score shown): {json.dumps(bands)}")
print(f"this branch's own seams.calibration: {json.dumps(body['seams']['calibration'])}")
print(f"this branch's calibration_gaps: {len(body['calibration_gaps'])}")
