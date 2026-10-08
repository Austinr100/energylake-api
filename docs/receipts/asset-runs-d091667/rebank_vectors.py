"""d091667: re-bank the two dashboard vectors this lane changes, additively.

    docs/receipts/asset-page-api-d091635/asset_wind_57514.json   + plant.equipment
    docs/receipts/vintages-d091644/body_wind_ciso_n28.json       + attribution

Each route is served over its own lane's bank, through that lane's own test
pools, exactly as its vector test serves it. Before anything is written, the
old receipt must equal the new body with the new key taken out (the `cache`
block aside), so the only change a re-bank can make is the key it names. The
old receipt is main's copy (`git show main:<path>`), so the script can be run
again; the old `cache` block and key order are kept, so the diff is the key.
tests/test_asset_runs_d091667.py holds the same against main's copies.

    python docs/receipts/asset-runs-d091667/rebank_vectors.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

ASSET = ROOT / "docs" / "receipts" / "asset-page-api-d091635" / "asset_wind_57514.json"
VINTAGE = ROOT / "docs" / "receipts" / "vintages-d091644" / "body_wind_ciso_n28.json"


def served_asset_wind() -> dict:
    import test_asset_page_d091635 as t
    main._asset_cache.clear()
    t.main._pool = t.wind_pool()
    return TestClient(main.app).get(t.WIND).json()


def served_vintage_wind() -> dict:
    import test_vintages_d091644 as t
    main._wind_vintages_cache.clear()
    main._pool = t.vintage_pool(t.load_bank("wind"), t.bank_lines("wind"))
    return TestClient(main.app).get("/api/generation/wind/vintages?area_kind=ba&area=CISO").json()


def _without(body: dict, path: tuple) -> dict:
    out = json.loads(json.dumps(body))
    d = out
    for k in path[:-1]:
        d = d[k]
    d.pop(path[-1])
    out.pop("cache", None)
    return out


def main_copy(path: pathlib.Path) -> dict:
    rel = path.relative_to(ROOT).as_posix()
    return json.loads(subprocess.run(["git", "-C", str(ROOT), "show", f"main:{rel}"],
                                     check=True, capture_output=True, text=True).stdout)


def rebank(path: pathlib.Path, body: dict, key: tuple) -> None:
    old = main_copy(path)
    cache = old.pop("cache", None)
    assert old == _without(body, key), f"{path.name}: the re-bank would change more than {key}"
    d_old, d_new = old, body
    for k in key[:-1]:
        d_old, d_new = d_old[k], d_new[k]
    d_old[key[-1]] = d_new[key[-1]]
    if cache is not None:
        old["cache"] = cache
    path.write_text(json.dumps(old, indent=1, ensure_ascii=False) + "\n")
    print(f"{path.relative_to(ROOT)}: + {'.'.join(key)}")


if __name__ == "__main__":
    rebank(ASSET, served_asset_wind(), ("plant", "equipment"))
    rebank(VINTAGE, served_vintage_wind(), ("attribution",))
