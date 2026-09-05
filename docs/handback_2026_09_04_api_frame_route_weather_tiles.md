# Handback — energylake-api: frame route admits `weather/tiles/`

**Date:** 2026-09-04
**Spec:** `energylake-pantry` `docs/cc_spec_2026_09_04_api_frame_route_weather_tiles.md`
**Repo:** `Austinr100/energylake-api`
**Branch:** `claude/frame-route-weather-tiles-d090402` (from `main` @ `8dea1fc`)
**Status:** shipped to branch. No PR opened, no merge, per spec §0 and the tasking.

---

## 1. §2 recon — the numbers, taken before any edit

### 1.1 `_validate_frame_key` — `main.py:8686`

Allowlist literal before the change (`main.py:8615`):

```python
_MODEL_ROOM_KEY_PREFIX = "d2/"
```

Every rejection the validator performed, all of which stay byte-identical in
behaviour:

| # | Rejection | Line (before) | Status |
|---|-----------|---------------|--------|
| 1 | empty key, `\x00`, `\\` → **400** `invalid frame key` | 8693–8694 | untouched |
| 2 | any segment in `("", ".", "..")` → **400** `invalid frame key (traversal)`. Catches `..`, `.`, leading `/` (empty first segment), trailing `/` (empty last segment), and `//` | 8698–8700 | untouched |
| 3 | `segments[0] != "d2" or not key.startswith("d2/")` → **403** `frame key outside the d2/ allowlist`. The `startswith` is also what rejects the bare prefix `"d2"` | 8702–8704 | **the one line changed** |

Ordering receipt (`main.py:8829–8831`): `_validate_frame_key` runs *before*
`_model_room_configured()` and before the boto3 client is built — which is
exactly why the Railway log shows a 403 in **2 ms**. The refusal never left the
process, confirming the spec's read that this is the validator and not the tile
bank.

### 1.2 Frame route handler and its `Cache-Control`

Handler: `main.py:8821`, `@app.get("/api/model-room/frame/{key:path}")`.

Cache header literal (`main.py:8632`, applied at `8850`):

```python
_FRAME_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
headers = {"Cache-Control": _FRAME_IMMUTABLE_CACHE}
```

Content type (`main.py:8849`) is `obj.get("ContentType") or _frame_content_type(safe_key)`,
and `_frame_content_type` (`8708`) is a pure suffix lookup against
`_FRAME_CONTENT_TYPES` (`.png → image/png`, `.json → application/json`, `.webp`,
`.jpg`, `.jpeg`). **It is suffix-based, not hardcoded to the `d2/` layout**, so
spec §3's content-type requirement came free — no generalisation was needed and
none was made.

### 1.3 `tests/test_model_room.py` — prior count **13 passed**

Cases that prove allowlist rejection:

- `test_frame_path_traversal_rejected_at_validator` (line 316) — unit-level, nine
  bad keys: `d2/synoptic/../../etc/passwd`, `d2/..`, `../secret`, `/d2/x`,
  `d2//x`, `d2/synoptic/./x`, `d2/x\y`, `""`, `d2`.
- `test_frame_non_d2_prefix_rejected_before_r2` (line 330) — end-to-end, 403.

Case that proves a rejected key never reaches the fake bucket:

- `test_frame_non_d2_prefix_rejected_before_r2`, via `assert fake.got() == []`.

Both are byte-unchanged (see §4).

### 1.4 §2.4 bucket check — **PASSES, this is an allowlist edit**

- Frame route reads `Bucket=R2_ARCHIVE_BUCKET` (`main.py:8836`).
- Point API's `WEATHER_VALUES_BUCKET` (`main.py:18179`) is
  `os.environ.get("WEATHER_VALUES_BUCKET", "") or R2_ARCHIVE_BUCKET`.

Same bucket, no second binding. The STOP condition in §2.4 did not fire; the fix
is an allowlist edit, not a bucket switch.

### 1.5 One finding outside the code — for the captain, not this lane

`.env.example:34` states: *"the Model Room's R2_* token is scoped to `d2/` and
generally CANNOT read `weather/values/`."* If the **deployed** token is genuinely
prefix-scoped to `d2/`, then after this change tile keys will pass the allowlist
and fail at R2 with `AccessDenied`, which `_r2_error_status` surfaces as **502**
— a different symptom from the old 2 ms 403, and a token-scope action on the
Railway/R2 side that no validator edit can grant. This is called out in the code
comment at `main.py:8597` and in the README. Worth checking before the §6
verification screenshot.

---

## 2. The change — before / after of the allowlist

**Before** (`main.py:8702–8704`):

```python
    # Single allowed prefix; the key must live UNDER it (never the bare prefix).
    if segments[0] != "d2" or not key.startswith(_MODEL_ROOM_KEY_PREFIX):
        raise HTTPException(
            status_code=403, detail="frame key outside the d2/ allowlist")
```

**After** (`main.py:8714–8721`):

```python
    # The key must live UNDER one of the allowed prefixes (never the bare
    # prefix — each literal carries its trailing slash, so "d2" and
    # "weather/tiles" fail this startswith). One gate, one code path: the
    # traversal/dot/absolute rejections above already applied to every prefix.
    if not any(key.startswith(p) for p in _FRAME_KEY_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail="frame key outside the d2/ + weather/tiles/ allowlist")
```

with the constants (`main.py:8625–8627`):

```python
_MODEL_ROOM_KEY_PREFIX = "d2/"
_MODEL_ROOM_TILES_PREFIX = "weather/tiles/"
_FRAME_KEY_PREFIXES = (_MODEL_ROOM_KEY_PREFIX, _MODEL_ROOM_TILES_PREFIX)
```

Notes on the shape, against spec §3:

- **`d2/` stays** — it is the first element of the tuple, and
  `_MODEL_ROOM_KEY_PREFIX` keeps its name and value.
- **`weather/values/` is NOT admitted** — asserted by a new test, not just by
  omission.
- **One code path, not two.** The prefix gate is a single `any(...)` over the
  tuple, layered on top of the *unmodified* traversal/dot/absolute/NUL checks,
  which run first and therefore already apply to the new prefix.
- The dropped `segments[0] != "d2"` clause was **fully redundant**, not a
  weakening: the segment loop above guarantees no empty segments, so
  `key.startswith("d2/")` already implies `segments[0] == "d2"`. Removing it is
  what lets a multi-segment prefix (`weather/tiles/`) go through the same gate.
  Test 4 below pins that the traversal guards still bite on the new prefix.
- **Cache header unchanged** — `_FRAME_IMMUTABLE_CACHE` is untouched and applies
  to both prefixes. Correct for tiles: keys embed model/run/cycle/param/fhr and
  are never rewritten in place; the 30-day lifecycle deletes keys, it does not
  mutate them.
- **Content type: no change required.** The existing logic was already
  suffix-based (§1.2), so `.../manifest.json` serves `application/json` and tile
  PNGs serve `image/png` with no prefix-specific branch. Both are pinned by
  tests.

---

## 3. Tests — `pytest tests/test_model_room.py -q`

Prior count: **13 passed**. Expected: 13 + 4 = 17.

```
.................                                                        [100%]
17 passed in 0.42s
```

The four added cases (all appended; nothing existing edited):

1. `test_frame_serves_tile_manifest_json` — the exact key shape from the
   2026-09-04 403 log line
   (`weather/tiles/gfs/na3/20260903/06Z/mslp/f006/manifest.json`) returns **200**,
   `application/json`, `public, max-age=31536000, immutable`, and the fake bucket
   records the get.
2. `test_frame_serves_tile_png` — a tile PNG under `weather/tiles/...` returns
   **200** / `image/png`, stored with **no** `ContentType`, so it proves the
   suffix map (not a `d2/`-shaped branch) decides the type.
3. `test_frame_weather_values_prefix_rejected_before_r2` —
   `weather/values/gfs/...` is **403** and `fake.got() == []`: never reaches the
   bucket.
4. `test_frame_tiles_traversal_rejected_at_validator` — rejects
   `weather/tiles/../d2/renders/x.png`, `weather/tiles//x.png`,
   `weather/tiles/./x.png`, `/weather/tiles/x.png`, `weather/tiles/`,
   `weather/tiles`, `weather/tiles/x\y`, `weather`, `weather/other/x.png`; and
   confirms both clean tile keys pass through unchanged. Note the first case
   escapes into `d2/`, which *is* allowlisted, and is still refused — the
   traversal guard fires before the prefix gate.

Whole suite, for safety: `pytest tests/ -q` → **1674 passed**.

---

## 4. Files changed — nothing else touched

```
 README.md                | 30 +++++++++++++++-----
 main.py                  | 59 +++++++++++++++++++++++++++-----------
 tests/test_model_room.py | 73 ++++++++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 138 insertions(+), 24 deletions(-)
```

Exactly the three files spec §5 allows. `git diff tests/test_model_room.py`
contains **zero deletions** — every pre-existing test is byte-unchanged; the new
block and two header-docstring lines are pure additions.

No pantry changes, no dashboard changes, no R2 changes, no deploy-config changes.
`README.md` §Endpoints now names both prefixes, states why `weather/values/` is
excluded, and carries the token-scope caveat from §1.5.

---

## 5. After merge (architect lane, per spec §6)

Railway redeploys on merge. Verify with the same log line that found the defect:

```
GET /api/model-room/frame/weather/tiles/gfs/na3/20260903/06Z/mslp/f006/manifest.json → 200
```

with a **real R2 round-trip latency** (~1.3–1.5 s like the `d2/renders` PNGs),
not a 2 ms answer. If it comes back **502** instead of 200, that is the token
scope from §1.5, not this change. Then the Atlas screenshot the dress v2.2
handback never got to take.
