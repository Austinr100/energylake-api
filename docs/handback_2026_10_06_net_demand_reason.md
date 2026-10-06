# Handback d091623 (energylake-api): net demand says why it is not yet scored, and says it truly

**Lane:** d091623. **Rulings:** none new. D-09-25-140 (net demand names the part that stops it),
D-09-25-127 (a calibrated figure only where its line was fitted on the hour's lead).
**Branch:** `claude/lucid-sagan-k8mbzb`. No PR, no merge, no deploy, no dispatch.
**Compare:** https://github.com/Austinr100/energylake-api/compare/main...claude/lucid-sagan-k8mbzb
**Read-only.** Every production access was a `SELECT` through the Neon connector. Nothing was
written to Neon.
**Receipts:** `docs/receipts/net-demand-reason-d091623/`.

## Verdict

- **The gate reads the line before it reads the figure.** `load_outlook.gate` now works in this order:
  - no `calibration_id` → `registry_only` (unchanged);
  - an id whose line is not banked → `no_line`;
  - a banked line and no figure → `beyond_fitted_leads` when the lead is outside the line's fitted
    leads, and `registry_only` when it is inside. It uses `solar_outlook._fitted`, the outlook
    routes' one test.
  - The figure-present branches, the night branch and the lo/hi refusal are byte-for-byte main's.
- **`scores.ours.reason` is served whenever ours is not scored.** It is one sentence built only from
  the backtest's own fields (`load_outlook.not_scored_reason`). `text` is still
  `"not yet scored"`, a scored cell has no `reason` key, and `caiso_da` is unchanged.
- **No new read.** There is no SQL or memo change. `main.py` changes only in the route docstring.
- **A second production replay** was banked at NOW = 2026-10-06T12:40Z, after the gated writer.
  The 10-05 replay is kept as it was.
- **On that replay, the body says what the brief measured, in the right words:**
  - 2026-10-05 is stopped by solar `beyond_fitted_leads`, 4 h from 02:00 PT, lead 22;
  - the stop sentence and the gap say "outside the leads its line was fitted on";
  - the reason reads exactly the brief's pinned content.
- **Re-measured the brief's table over all CISO issuances at 12:4x Z:**
  - a line id and no figure, lead outside the line's fitted leads: solar 737, wind 108;
  - the same with the lead inside: 0 and 0;
  - a figure outside its line's fitted leads: 0 and 0;
  - no line id: solar 3,816, wind 3,396.

  Identical to §0.2.

## 1. Before and after, the live-shaped body

Both are `GET /api/load/net-demand?area=CISO` over
`tests/fixtures/load_outlook_d091623/production_2026_10_06_1240z.json`. "Before" is main's
`load_outlook.py` and `main.py` run over the same replay, and it reproduces the brief's §0.1/§0.2
live body word for word.

### `scores.ours`

Before:

```json
{"status": "not_yet_scored", "text": "not yet scored", "window_start": "2026-09-08",
 "window_end": "2026-10-05", "n_days": 0, "n_hours": 0, "min_days": 14}
```

After:

```json
{"status": "not_yet_scored", "text": "not yet scored", "window_start": "2026-09-08",
 "window_end": "2026-10-05", "n_days": 0, "n_hours": 0, "min_days": 14,
 "reason": "0 of 14 days scored; the newest unscored day, 2026-10-05, stopped on solar: the hour's lead is outside the leads its line was fitted on (4 h from 02:00 PT, lead 22 h); most often wind: no 12Z issuance of D-1 is banked, on 27 of 28 unscored days"}
```

The page prints it as *"not yet scored: 0 of 14 days scored; the newest unscored day, …"*.

### The newest unscored day

Before:

```json
{"day": "2026-10-05", "stopped_by": [{"part": "solar", "reason": "registry_only",
  "init_ts": "2026-10-04T12:00:00+00:00", "first_ts": "2026-10-05T09:00:00+00:00",
  "lead_h": 22, "hours_absent": 4,
  "detail": "the hour carries no calibrated figure (registry only)"}]}
```

After:

```json
{"day": "2026-10-05", "stopped_by": [{"part": "solar", "reason": "beyond_fitted_leads",
  "init_ts": "2026-10-04T12:00:00+00:00", "first_ts": "2026-10-05T09:00:00+00:00",
  "lead_h": 22, "hours_absent": 4,
  "detail": "the hour's lead is outside the leads its line was fitted on"}]}
```

The rows carry line 407 (`h07_24`, fitted 7–21) at leads 22–24 and line 408 (`h25_48`, fitted
26–45) at lead 25. 2026-10-04 moves the same way: solar 4 h from 02:00 PT, lead 22, issuance
10-03 12Z (lines 387/388, the same leads). Its wind stop, `no_12z_issuance`, is unchanged.

Tally over `unscored_days` (28 days), (part, reason) → days:

| part | reason | before | after |
| --- | --- | ---: | ---: |
| wind | `no_12z_issuance` | 27 | 27 |
| solar | `registry_only` | 21 | 19 |
| solar | `no_12z_issuance` | 7 | 7 |
| solar | `beyond_fitted_leads` | — | 2 |

The 19 that stay `registry_only` are the backfill's rows, which carry no line id. That is the
true word for them.

### `net_demand_stop.sentence`

Before:
*Net demand stops at 2026-10-09T00:00:00+00:00: solar (the hour carries no calibrated figure
(registry only), lead 67 h); wind (the hour carries no calibrated figure (registry only), lead 67 h).*

After:
*Net demand stops at 2026-10-09T00:00:00+00:00: solar (the hour's lead is outside the leads its
line was fitted on, lead 67 h); wind (the hour's lead is outside the leads its line was fitted on,
lead 67 h).*

The lines involved are solar `h49_120` line 720 (fitted 50–66) and wind `h49_120` line 736
(fitted 49–66). `stops.solar.stop` and `stops.wind.stop` read `beyond_fitted_leads` at lead 67 too.

### The gap

Before: `{"first_absent_ts": "2026-10-08T03:00:00+00:00", "last_absent_ts": "2026-10-08T06:00:00+00:00",
"hours": 4, "parts": [{"part": "solar", "reason": "registry_only", …}]}`

After: the same span, `"parts": [{"part": "solar", "reason": "beyond_fitted_leads", "detail": "the
hour's lead is outside the leads its line was fitted on"}]`.

These are leads 46–49 of the 10-06 06Z issuance: 46–48 on line 755 (fitted 25–45) and 49 on
line 720 (fitted 50–66). `stops.solar.gaps` gives `(first lead 46, 4 h, beyond_fitted_leads)`.

## 2. The reason, clause by clause

`not_scored_reason(score, unscored_days)` joins three clauses with "; ":

- **(a)** `{n_days} of {min_days} days scored`.
- **(b)** `the newest unscored day, {day}, stopped on ` followed by each stop of that day in served
  order, joined by " and ". Each stop is `{part}: {detail}`, then, where the stop serves them,
  `({hours_absent} h from {first_ts in PT, HH:MM} PT, lead {lead_h} h)`.
  - Load, truth and `no_12z_issuance` stops serve none of these, so they carry no parenthesis.
  - `no_issuance_hour` serves no lead, so it gets none.
- **(c)** `most often {part}: {detail}, on {k} of {len(unscored_days)} unscored days`.
  - A day counts once per (part, reason).
  - Ties go by part order (load, solar, wind, truth), then by reason code.

Every number in it is a served field. The only exceptions are `k`, which is a count over the
served `unscored_days`, and the "12" of the detail's own "12Z". R3 checks this by regex. The
sentence never begins with "not yet scored".

On a 13-day world (R3) it reads: *"13 of 14 days scored; the newest unscored day, 2026-10-03,
stopped on wind: no 12Z issuance of D-1 is banked; most often wind: no 12Z issuance of D-1 is
banked, on 1 of 1 unscored days"*.

## 3. Files

| file | what |
| --- | --- |
| `load_outlook.py` | `gate()` reads the line before the figure; `not_scored_reason()`, `_stop_words()`, `STOP_PART_ORDER`; `backtest()` sets `ours.reason` when not scored |
| `main.py` | `/api/load/net-demand` docstring names `scores.ours.reason` (no code change) |
| `tests/test_net_demand_reason_d091623.py` | R1, R2, R3, the 10-06 samples (23 tests) |
| `tests/load_bank_d091623.py` | the 10-06 replay: NOW, its fixture path, d091611's loader and pool |
| `tests/load_bank_d091611.py` | `load()` takes the fixture path (default unchanged) |
| `tests/fixtures/load_outlook_d091623/production_2026_10_06_1240z.json` | the replay, 1.2 MB, d091611's form |
| `docs/receipts/net-demand-reason-d091623/render.py` | d091611's statements at the new NOW, `--rows` for the compact form |
| `docs/receipts/net-demand-reason-d091623/bank.py` | writes the fixture from the connector's answers; refuses any statement whose run text is not render.py's |
| `docs/receipts/net-demand-reason-d091623/sample.py`, `sample_*.json` | the six bodies at the new NOW |
| `docs/receipts/net-demand-reason-d091623/rehearse.py`, `reds.txt` | R5 |
| `docs/receipts/load-net-demand-api-d091611/sample_net_demand_ciso.json` | re-banked: it gains `scores.ours.reason`, nothing else (the other five bodies re-bank identical) |
| `docs/receipts/load-net-demand-api-d091611/rehearse.py`, `reds.txt` | two stale needles fixed (§5.3); all 15 breaks red again |

The 10-05 replay (`production_2026_10_05_0330z.json`) is untouched. The dashboard's fixtures are
not touched.

## 4. Tests, red then green

**Red on main.** I ran the new module on a copy of the tree with main's `load_outlook.py` and
`main.py`: 16 failed, 7 passed. The 7 that pass on main are expected to:

- the bank's own facts (R1);
- `test_N1_gate`'s assertions and the figure-without-id case (R2);
- the three R2 rows main already answers right (no id; and no figure with the lead inside, lit
  and dark);
- a scored cell has no reason (R3).

**Green on the branch.** The three modules give 93 passed: the new module, `test_load_outlook.py`
and `test_outlook_fit_d091608.py`. Across the full suite, 2411 passed, 2 skipped and 1 failed. The
failure is `test_chart_brief.py::test_chart_brief_maps_contract` (`'pending' == 'overdue'`). It
fails identically on main and is outside this lane.

**R4.** `test_load_outlook.py` (40) and `test_outlook_fit_d091608.py` (30) pass on the 10-05
replay. No assertion changed. The only change behind them is the re-banked d091611
`sample_net_demand_ciso.json`, which gains `reason`. `test_samples_are_the_routes_answer` compares
against that file. On the 10-05 replay the gate change moves nothing: that replay predates the
writer's gate, and no row in it carries a line without a figure.

**R5** (`docs/receipts/net-demand-reason-d091623/reds.txt`; each break is applied to a
throwaway copy):

| break | guarded by | result |
| --- | --- | --- |
| the old first branch restored (`cal is None or cid is None → registry_only`) | `test_R1 or test_R2` | 9 failed |
| clause (b) dropped | `test_R3` | 5 failed |
| clause (c) dropped | `test_R3` | 5 failed |
| a reason on a scored cell | `test_R3` | 1 failed |
| the stop's PT hour, lead and hours absent dropped | `test_R3` | 3 failed |
| ties by reason code before part order | `test_R3` | 2 failed |
| clean tree | all three modules | 93 passed |

## 5. What this brief got wrong

1. **R4's "pass as on main" needed a fixture change, not an assertion change.**
   `test_samples_are_the_routes_answer` holds d091611's banked bodies equal to the route. Any
   served `reason` therefore changes `sample_net_demand_ciso.json`, so I re-banked it with d091611's
   own `sample.py`. Only that body differs, by the one key. The brief's "keep the 10-05 replay"
   holds: the replay is untouched.
2. **"The words are yours" could not be wholly mine for clause (b).** The brief pins "each part
   with the route's own `detail`". Some details already end in a parenthesis ("the hour carries no
   calibrated figure (registry only)"). Adding the stop's numbers then gives two parentheses in a
   row: "… (registry only) (2 h from 03:00 PT, lead 14 h)". It reads clumsily, but I kept it rather
   than rewrite a served detail. Today's live sentence has no such pair, since its details are
   `beyond_fitted_leads` and `no_12z_issuance`.
3. **d091611's rehearsal had gone stale, and the suite did not notice.**
   - Its N1 break's needle is the gate line this lane replaces. I moved the needle to the new line.
   - Its D-09-25-138 needle had already stopped matching on main. d091608 rewrapped the
     `_DDCache` stale test as `age < self.ttl + self.max_stale_s` over two lines, so that break was
     silently "not applied".
   - I fixed both needles, and `reds.txt` shows all 15 breaks red again.
4. **§0.3, wind's day-ahead leads.** The brief says the lines fitted on 2026-10-05 19:12Z cover
   wind 25–48. No such line is on any banked row. The 10-06 06Z wind issuance carries `h25_48` line
   683, fitted 2026-10-04 15:09Z (`fit_end` 10-03), which covers 25–48. The 10-05 fit's wind lines
   on the bank are 733 (1–6), 734 (7–24) and 736 (49–66). So the conclusion about coverage holds,
   but the line it rests on is a day older than the brief says.
5. **§0.1 counts the unscored days by (part, reason), and so does clause (c).** The brief does not
   say whether a day with two stops on one part could count twice. The backtest emits at most one
   stop per part per day, so it cannot. I count each (part, reason) once per day so that
   "k of len(unscored_days)" stays a count of days.

## 6. How the replay was banked

- **NOW** is 2026-10-06T12:40Z. Both parts' newest issuance was 2026-10-06 06Z, and nothing newer
  landed while I banked: GEN_NEWEST was re-read after the rows.
- **Statements.** Each one is d091611's statement with NOW moved (`render.py`). It is wrapped as
  `json_agg(json_build_array(<columns>))::text`, so Postgres returns the compact rows.
- **Running them.** Each statement was run through `run_sql` as exactly the text `render.py`
  prints. `bank.py` reads the session's answers, matches each one to its statement by text, and
  writes the fixture in d091611's key layout. It refuses any statement it cannot match.
- **Size.** 18 statements plus the lines read for the 16 line ids the rows carry. 2,256
  forecast rows per area, 1,364 solar and 43 wind backtest rows, 192 newest-issuance rows per part.
