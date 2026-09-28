# Handback — d091497 — the model arm's NOW is the hour it is (D-09-25-47)

**Spec:** pantry `docs/cc_spec_2026_09_28_now_card_and_enso_entry.md` §A (pantry main `be8c9e4`).
**Repo / branch:** `energylake-api`, `claude/wizardly-ritchie-rmk644`, cut from `origin/main` `85adf67` (#82). Branch only: no PR, no migration, no merge, no workflow edits.
**Ruling built:** D-09-25-47 (amends only D-09-24-09's example string).
**Suite:** 1961 passed at the base. **1968 passed** on the branch: 7 new cells (N1–N7), 4 pins amended (listed below), 0 skipped. **No STOP-K:** T9's key-set pins pass unedited, and no key was added or renamed.

| commit | what |
| --- | --- |
| `2d6cc74` | §A.1–§A.6: `model_arm.py`, N1–N7, the amended pins, README |
| (this commit) | handback, reds.py, reds.txt |

## Gate 0 — what was read before the edit

**`model_arm.build()` on main `85adf67` (model_arm.py:443–451):**

```python
    s0 = series[0]
    now_row = _hour_row(s0, label)
    now = {k: now_row[k] for k in ("t", "feels", "dewpoint", "rh", "wind", "sky",
                                   "mslp", "condition", "condition_raw")}
    now["absent"] = [a for a in now_row["absent"]
                     if not a.startswith(("pop:", "precip_amt:", "t_spread:"))]
    now["valid"] = lf.iso_z(run_dt)
    # D-09-24-09: the label, never "observed".
    now["source"] = f"model · {label} f000"
    now["age_min"] = int((generated_at - run_dt).total_seconds() // 60)
```

The hourly strip in the same function, just above:

```python
    last = fhr_range[1] if fhr_range else -1
    hourly, hourly_note = lf.trim_to_now(
        [_hour_row(s, label) for s in series if s["h"] <= last], generated_at,
        HOURLY_ROWS)
```

**`lf.trim_to_now` (local_forecast.py:585–595):**

```python
def trim_to_now(rows: list[dict], now: datetime, limit: int = HOURLY_ROWS
                ) -> tuple[list[dict], str]:
    """D-09-25-10 — hourly starts at the current hour, on both arms. Drops every
    row before the hour containing `now` (UTC, floored), keeps at most `limit`
    from there, ..."""
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    kept = [r for r in rows if parse_iso(r["valid"]) >= hour][:limit]
    return kept, f"hourly from {hour:%H}Z, {len(kept)} rows"
```

So `hourly[0]` is the series row at `h = floor((generated_at − run_dt) / 1 h)`, and `now` was `series[0]`. That is §0's Moscow disagreement: 54° against 60°.

**Every test that pinned `now` on the model arm** (tests/test_local_forecast.py on main). The default clock is `NOW = 2026-09-25 21:10Z` and `RUN = 12Z`, so after the change the default `now` is h9:

| test | pin on main | after |
| --- | --- | --- |
| `test_T5_model_now_source_is_labelled_and_observed_appears_nowhere` | `"12Z" in src and "f000" in src`; `src == "model · GFS 12Z f000"`; `now.valid == "2026-09-25T12:00:00Z"`; `age_min == 9*60+10` | **amended** (below) |
| `test_T14_model_hourly_starts_this_hour` | `now.source == "model · GFS 12Z f000"  # now stays f000` | **amended** |
| `test_T16_model_arm_reads_the_placed_cell_on_pm180` (×5) | `now.t == round(283.15 + 7.0 - 273.15, 1)` (the f000 value) | **amended** |
| `test_T24_vancouver_answers_from_the_model_arm_on_the_production_header` | `now.source == "model · GFS 12Z f000"` | **amended** |
| `test_T8f_f000_by_day_names_the_six_hour_mean` | at `RUN + 10 min`: `now.sky is None`, `"condition: from sky, which is null (not banked at f000 (dswrf is a 6 h mean))" in now.absent` | **unedited, passes.** At 10 min past the run the hour that holds `generated_at` *is* f000, so the six-hour-mean reason is still the true one |
| `test_T8f_every_unknown_on_both_arms_says_why` | at `RUN + 10 min`: `now` reason is `(night)` or `(not banked at f000 …)` | **unedited, passes** (same reason) |
| `test_T4_stop_b_a_param_missing_on_the_run_is_named` | `now.mslp is None` | unedited, passes |
| `test_T5_forced_model_arm_in_the_us_is_labelled_too`, `test_T6_points_failure_falls_through_to_the_model_arm` | `now.source.startswith("model ·")` | unedited, pass |
| `test_T9f_key_sets_are_byte_identical_to_main` | `NOW_KEYS` incl. `valid`, `source`, `age_min` | **unedited, passes** (no STOP-K) |

## Pins amended (Error #65: a pinned string is part of the contract)

Each one keeps its old text in a comment beside the assertion.

1. **T5**
   - old: `assert "12Z" in src and "f000" in src` · `assert src == "model · GFS 12Z f000"` · `assert b["now"]["valid"] == "2026-09-25T12:00:00Z"`
   - new: `assert "12Z" in src and "f006–f012 interp" in src` · `assert src == "model · GFS 12Z f006–f012 interp"` · `assert b["now"]["valid"] == "2026-09-25T21:00:00Z"`
   - `age_min == 9 * 60 + 10` is unchanged: it is the run's age (§A.4).
2. **T14**
   - old: `assert b["now"]["source"] == "model · GFS 12Z f000"          # now stays f000`
   - new: `assert b["now"]["source"] == hourly[0]["source"] == "model · GFS 12Z f000–f006 interp"`
3. **T16** (the parametrized test, 5 cases)
   - old: `assert b["now"]["t"] == round(283.15 + 7.0 - 273.15, 1), name`
   - new: `assert b["now"]["t"] == round(283.15 + 0.9 + 7.0 - 273.15, 1), name`
   - This is h9 on the fake's linear `t2m = 283.15 + 0.1·fhr`. The test's subject, the placed cell's +7 K, is untouched.
4. **T24**
   - old: `assert b["now"]["source"] == "model · GFS 12Z f000"`
   - new: `assert b["now"]["source"] == "model · GFS 12Z f006–f012 interp"`

## The change (`model_arm.py`)

1. `h_now = max(0, floor((generated_at − run_dt) / 1 h))`.
2. `now` takes its fields from `_hour_row(series[h_now], label)`, with the same `absent` filter as before.
3. `now.valid` and `now.source` are that row's own `valid` and `source`. The source is **the hourly row's string**, so the page's "Now" cell and the NOW card print the same thing.
4. `now.age_min` still counts minutes since `run_dt`. The module docstring now says that on the model arm this is the run's age, and on the NWS arm it is the observation's age. No key changed.
5. The docstring's D-09-24-09 paragraph no longer quotes `… f000`. A D-09-25-47 paragraph follows it.
6. **Past the run's end** (`h_now` > the last f-hour with a banked `t2m`):
   - `now`'s value fields (`t`, `wind.speed`, `mslp`, `sky`) are null. Each carries `run ends before now (last t2m fNNN)`.
   - `condition` is `unknown` with `condition: from sky, which is null (run ends before now (last t2m fNNN))`, so `build_payload`'s D-09-25-31 check holds.
   - `now.source` is `model · GFS HHZ (run ends fNNN)`.
   - `now.valid` is the hour that holds `generated_at`. It is the hour the card is *for*, not a value.
   - No row is invented. Also, `trim_to_now` leaves `hourly` empty in this case.

The NWS arm is not touched. N6 pins the LAX body's sha-256 to the value recorded on main before the edit (`acc9de1b…fb51`), and it matches.

## Cells

| cell | test | result |
| --- | --- | --- |
| N1 | `test_N1_now_is_the_hourly_now_row`: Moscow (55.756, 37.617), GFS 00Z, `run + 10h40m`. `now.t == hourly[0].t`, `now.source == hourly[0].source == "model · GFS 00Z f006–f012 interp"`, `now.valid == hourly[0].valid == 10:00Z` | pass |
| N2 | `run + 12h00m`: `now.source == "model · GFS 00Z f012"` (no `interp`) | pass |
| N3 | `run + 10h40m59s`: `age_min == 640` | pass |
| N4 | Moscow 13:40 MSK: `now.sky` not null, `condition` not `unknown`, no `sky:`/`condition:` absent line | pass |
| N5 | Moscow 23:40 MSK: `condition == "unknown"`, `condition: from sky, which is null (night)` in `absent` | pass |
| N6 | LAX NWS body sha-256 == main's | pass |
| N7 | `run + 250 h`: value fields null, each with `run ends before now (last t2m f240)`; `source == "model · GFS 00Z (run ends f240)"`; `hourly == []`; `build_payload` accepts it | pass |

N1–N5 and N7 call `model_arm.build` directly on `_synthetic_ladders` (T7's `read_ladder`-shaped fake) with an injected `generated_at`. N6 goes through the route on the existing `world` fixture.

## Reds

`python docs/receipts/now-card-hour-d091497/reds.py` makes one edit, runs the **whole** suite, then restores the file and checks its sha-256. Full output: `reds.txt`.

| red | edit | must go red | went red |
| --- | --- | --- | --- |
| R1 | `series[h_now]` → `series[0]` | N1 | **N1**, N2, N4, T5, T14, T16×5, T24 (11) |
| R2 | `now.source` → `f"model · {label} f000"` | N1, N2 | **N1, N2**, N7, T5, T14, T24 (6) |
| R3 | `age_min` counted from `now.valid` | N3 | **N3**, N7, T5 (3) |

## Notes for the architect

- **§B needs nothing more from here.** `NowView.night` keys on `now.valid`, which is now the current hour, as §B.4 expects. The `unknown` reason §B.2 lists comes straight from `now.absent` / `hourly[i].absent`, which already carry it.
- **Past-end `now.valid`.** The spec does not say what `now.valid` is past the run's end. I used the hour that holds `generated_at`, not null, because `NowView.night` keys on it. Change it if you rule otherwise.
- **The no-`t2m`-at-all case.** When `fhr_range` is null, `h_now` is bounded by f240, not by `last`. So a fresh run that is missing `t2m` still reads the current hour, with `t: not banked on run`, the reason it gave before. Past f240 it says `(last t2m none)`.
