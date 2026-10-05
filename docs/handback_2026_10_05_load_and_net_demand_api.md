# Handback d091611: the load outlook and CAISO net demand (API)

**Lane:** d091611, `energylake-api`. **Rulings:** D-09-25-139 (the load outlook), D-09-25-140 (CAISO net demand).
**Branch:** `claude/load-outlook-net-demand-7w94wg`. Branch only: no PR, no merge, no deploy, nothing written to Neon (Neon read through the connector, SELECT and EXPLAIN only).
**Compare:** https://github.com/Austinr100/energylake-api/compare/main...claude/load-outlook-net-demand-7w94wg
**Measured:** Neon production, 2026-10-05 03:19–03:45Z (Pacific 2026-10-04 evening). Score window: the 28 Pacific days 2026-09-06 → 10-03.

## The short of it

1. **The outlook works as ruled.** On 10-04 PT, each of today .. D+7 is CAISO's freshest product: DAM (today, D+1), 2DA (D+2), 7DA (D+3 → D+7, issued 4, 3, 2, 1 and 0 days ago). The system scores **DAM MAPE 2.40% (MAE 693 MW), 2DA 2.49%, 7DA 7.00% (MAE 2,168 MW)**.
2. **11 of the 20 BAs with an EIA respondent pass the usability rule** (BANC, BPAT, EPE, IPCO, NWMT, PACW, PGE, PNM, PSEI, SCL, TPWR). LADWP fails as expected. **PSEI passes**: its EIA `D` is clean (r 0.990, scale 0.999). What recon d091565 found unusable at PSEI was its **DF** (the BA's own forecast), and that holds here too: MAPE 36%, bias −887 MW. 15 of the 36 areas have no actual we bank (5 TACs, 7 sub-areas, AVRN, BHBA, GWA), so they read "not scored: <reason>".
3. **Net demand is drawn for 52 of the 192 outlook hours** (10-05 00Z → 10-07 11Z), with two 4-hour morning gaps. It stops at lead 67 of the 18Z solar (beyond the fitted leads) and lead 67 of the 00Z wind (registry only).
4. **The day-ahead backtest of ours scores 0 days, and cannot score any until about 10-19.** The CISO implied-generation history is the backfill: one issuance a day, registry only, never a calibrated figure, and wind's backfill has no 12Z at all. See "What this brief got wrong" §1.
5. **CAISO's DA net demand reference is biased by +1,833 MW against the truth, and the bias comes from scope, not skill.** The three hubs' DAM solar and wind cover about 1,450 MW less than fuel-mix solar and wind (about 3,200 MW less at midday). That repeats D-09-25-114 clause 6's scope finding. The route serves the reference with `reference_scope_note`, and the captain should rule on it (§2 below).
6. **The D-09-25-138 stale cap:** d091608 is not on main, so `max_stale_s` is added to `_DDCache` here (opt-in, `None` keeps the old policy). The three load memos are 300 s fresh and at most 900 s stale.

---

## Step 1

### 1.1 Per area × product, against the area's actual (trailing 28 Pacific days)

A day counts only when every one of its hours has both the forecast and the actual. MAPE, MAE, bias and r are computed over the hours of the counted days. Peak MAPE and the peak-hour hit are per day (on a tie, the earliest hour). Each BA is scored against EIA-930 `D` **whatever its usability verdict** (Step 1 asks for every BA), and the `usable` column shows whether the route will serve the score. The numbers come from one SQL statement (LATERAL per series). The route's own Python reproduces CA ISO-TAC and BPAT to the digit (`test_L2_production_*`).

¹ EIA-930 DF for CISO **is** CAISO's DAM rounded to the MW: 661 of 672 hours are within 0.5 MW, and the largest difference is 0.5 MW. It is not drawn (`df_absent_reason`).

| area | actual | usable | product | days | hours | MAPE % | MAE MW | bias MW | r | peak MAPE % | peak-hour hit % |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CA ISO-TAC | native | yes | DAM | 28 | 672 | 2.40 | 693 | 244 | 0.9868 | 2.50 | 78.6 |
| CA ISO-TAC | native | yes | 2DA | 28 | 672 | 2.49 | 734 | -128 | 0.9825 | 2.56 | 85.7 |
| CA ISO-TAC | native | yes | 7DA | 28 | 672 | 7.00 | 2168 | -536 | 0.7931 | 9.08 | 64.3 |
| CA ISO-TAC | native | yes | EIA DF (CISO) ¹ | 28 | 672 | 2.40 | 693 | 244 | 0.9868 | 2.50 | 78.6 |
| AVA | EIA D AVA | **no** | DAM | 28 | 672 | 15.88 | 195 | -195 | 0.9805 | 14.63 | 42.9 |
| AVA | EIA D AVA | **no** | 2DA | 28 | 672 | 15.96 | 196 | -196 | 0.9790 | 14.66 | 42.9 |
| AVA | EIA D AVA | **no** | 7DA | 28 | 672 | 15.90 | 195 | -195 | 0.9598 | 14.09 | 42.9 |
| AVA | EIA D AVA | **no** | EIA DF | 28 | 672 | 1.31 | 16 | -1 | 0.9888 | 2.03 | 50.0 |
| AZPS | EIA D AZPS | **no** | DAM | 28 | 672 | 14.65 | 650 | -397 | 0.7677 | 6.66 | 35.7 |
| AZPS | EIA D AZPS | **no** | 2DA | 28 | 672 | 14.95 | 663 | -368 | 0.7512 | 6.33 | 35.7 |
| AZPS | EIA D AZPS | **no** | 7DA | 28 | 672 | 15.96 | 711 | -184 | 0.6411 | 6.59 | 32.1 |
| AZPS | EIA D AZPS | **no** | EIA DF | 27 | 648 | 14.82 | 672 | -406 | 0.7462 | 6.92 | 3.7 |
| BANC | EIA D BANC | yes | DAM | 28 | 672 | 7.33 | 158 | -155 | 0.9486 | 9.49 | 60.7 |
| BANC | EIA D BANC | yes | 2DA | 28 | 672 | 7.35 | 160 | -154 | 0.9441 | 9.72 | 71.4 |
| BANC | EIA D BANC | yes | 7DA | 28 | 672 | 9.76 | 232 | -207 | 0.8442 | 13.92 | 57.1 |
| BANC | EIA D BANC | yes | EIA DF | 28 | 672 | 2.30 | 56 | 7 | 0.9478 | 7.28 | 60.7 |
| BPAT | EIA D BPAT | yes | DAM | 28 | 672 | 3.58 | 234 | -210 | 0.9295 | 4.26 | 50.0 |
| BPAT | EIA D BPAT | yes | 2DA | 28 | 672 | 3.83 | 249 | -224 | 0.9281 | 4.37 | 53.6 |
| BPAT | EIA D BPAT | yes | 7DA | 28 | 672 | 4.78 | 312 | -295 | 0.9187 | 5.23 | 25.0 |
| BPAT | EIA D BPAT | yes | EIA DF | 27 | 648 | 1.33 | 81 | 28 | 0.9505 | 1.32 | 59.3 |
| EPE | EIA D EPE | yes | DAM | 28 | 672 | 4.52 | 58 | -24 | 0.9774 | 5.07 | 39.3 |
| EPE | EIA D EPE | yes | 2DA | 28 | 672 | 4.50 | 58 | -23 | 0.9786 | 5.52 | 35.7 |
| EPE | EIA D EPE | yes | 7DA | 28 | 672 | 8.43 | 108 | -27 | 0.9190 | 12.49 | 46.4 |
| EPE | EIA D EPE | yes | EIA DF | 28 | 672 | 3.85 | 50 | -13 | 0.9780 | 4.70 | 42.9 |
| IPCO | EIA D IPCO | yes | DAM | 28 | 672 | 2.85 | 58 | 18 | 0.9650 | 2.91 | 53.6 |
| IPCO | EIA D IPCO | yes | 2DA | 28 | 672 | 3.15 | 65 | 24 | 0.9550 | 3.72 | 53.6 |
| IPCO | EIA D IPCO | yes | 7DA | 28 | 672 | 5.22 | 108 | 20 | 0.8555 | 7.42 | 28.6 |
| IPCO | EIA D IPCO | yes | EIA DF | 27 | 648 | 6.32 | 133 | -6 | 0.7736 | 7.86 | 33.3 |
| LADWP | EIA D LDWP | **no** | DAM | 28 | 672 | 60.99 | 312 | -63 | 0.7970 | 6.39 | 50.0 |
| LADWP | EIA D LDWP | **no** | 2DA | 28 | 672 | 61.92 | 354 | -120 | 0.7862 | 8.12 | 46.4 |
| LADWP | EIA D LDWP | **no** | 7DA | 28 | 672 | 62.87 | 544 | -230 | 0.5907 | 15.10 | 60.7 |
| LADWP | EIA D LDWP | **no** | EIA DF | 28 | 672 | 62.23 | 348 | -111 | 0.7774 | 8.33 | 57.1 |
| NEVP | EIA D NEVP | **no** | DAM | 28 | 672 | 4.87 | 273 | -152 | 0.9303 | 2.81 | 53.6 |
| NEVP | EIA D NEVP | **no** | 2DA | 28 | 672 | 4.96 | 278 | -135 | 0.9290 | 3.11 | 57.1 |
| NEVP | EIA D NEVP | **no** | 7DA | 28 | 672 | 7.85 | 446 | -67 | 0.8324 | 8.82 | 57.1 |
| NEVP | EIA D NEVP | **no** | EIA DF | 28 | 672 | 7.94 | 452 | -438 | 0.9217 | 5.88 | 42.9 |
| NWMT | EIA D NWMT | yes | DAM | 28 | 672 | 2.41 | 29 | -1 | 0.9289 | 2.06 | 14.3 |
| NWMT | EIA D NWMT | yes | 2DA | 28 | 672 | 2.53 | 31 | -3 | 0.9205 | 2.14 | 14.3 |
| NWMT | EIA D NWMT | yes | 7DA | 28 | 672 | 2.95 | 36 | -6 | 0.8995 | 2.62 | 32.1 |
| NWMT | EIA D NWMT | yes | EIA DF | 28 | 672 | 1.97 | 24 | -2 | 0.9469 | 1.93 | 28.6 |
| PACE | EIA D PACE | **no** | DAM | 28 | 672 | 7.27 | 431 | 84 | 0.7914 | 3.61 | 17.9 |
| PACE | EIA D PACE | **no** | 2DA | 28 | 672 | 7.26 | 433 | 51 | 0.7861 | 3.89 | 25.0 |
| PACE | EIA D PACE | **no** | 7DA | 28 | 672 | 8.30 | 500 | 6 | 0.6829 | 7.25 | 17.9 |
| PACE | EIA D PACE | **no** | EIA DF | 27 | 648 | 7.96 | 458 | 278 | 0.7979 | 3.04 | 18.5 |
| PACW | EIA D PACW | yes | DAM | 28 | 672 | 1.73 | 38 | 3 | 0.9858 | 1.98 | 57.1 |
| PACW | EIA D PACW | yes | 2DA | 28 | 672 | 1.78 | 39 | -18 | 0.9859 | 2.27 | 60.7 |
| PACW | EIA D PACW | yes | 7DA | 28 | 672 | 3.56 | 80 | -45 | 0.9318 | 5.13 | 39.3 |
| PACW | EIA D PACW | yes | EIA DF | 28 | 672 | 2.42 | 52 | 39 | 0.9779 | 2.35 | 46.4 |
| PGE | EIA D PGE | yes | DAM | 28 | 672 | 1.92 | 49 | 20 | 0.9729 | 1.86 | 57.1 |
| PGE | EIA D PGE | yes | 2DA | 28 | 672 | 1.88 | 48 | 11 | 0.9722 | 2.20 | 50.0 |
| PGE | EIA D PGE | yes | 7DA | 28 | 672 | 3.09 | 81 | 0 | 0.9061 | 5.10 | 71.4 |
| PGE | EIA D PGE | yes | EIA DF | 28 | 672 | 1.69 | 43 | 26 | 0.9784 | 1.88 | 78.6 |
| PNM | EIA D PNM | yes | DAM | 28 | 672 | 5.27 | 92 | -67 | 0.9622 | 4.96 | 50.0 |
| PNM | EIA D PNM | yes | 2DA | 28 | 672 | 5.57 | 97 | -69 | 0.9564 | 5.24 | 46.4 |
| PNM | EIA D PNM | yes | 7DA | 28 | 672 | 5.51 | 99 | -58 | 0.9312 | 5.83 | 39.3 |
| PNM | EIA D PNM | yes | EIA DF | 28 | 672 | 5.78 | 101 | -92 | 0.9770 | 5.48 | 50.0 |
| PSEI | EIA D PSEI | yes | DAM | 28 | 672 | 1.42 | 35 | 11 | 0.9897 | 1.60 | 71.4 |
| PSEI | EIA D PSEI | yes | 2DA | 28 | 672 | 1.36 | 34 | 7 | 0.9901 | 1.46 | 67.9 |
| PSEI | EIA D PSEI | yes | 7DA | 28 | 672 | 2.53 | 62 | 25 | 0.9637 | 3.11 | 78.6 |
| PSEI | EIA D PSEI | yes | EIA DF | 28 | 672 | 36.23 | 888 | -887 | 0.4406 | 31.64 | 17.9 |
| SCL | EIA D SCL | yes | DAM | 28 | 672 | 2.87 | 27 | -14 | 0.9659 | 2.82 | 64.3 |
| SCL | EIA D SCL | yes | 2DA | 28 | 672 | 2.83 | 27 | -14 | 0.9670 | 2.81 | 64.3 |
| SCL | EIA D SCL | yes | 7DA | 28 | 672 | 3.39 | 32 | -10 | 0.9468 | 3.60 | 60.7 |
| SCL | EIA D SCL | yes | EIA DF | 27 | 648 | 2.68 | 25 | -12 | 0.9702 | 2.71 | 59.3 |
| SRP | EIA D SRP | **no** | DAM | 28 | 672 | 17.07 | 633 | 561 | 0.8738 | 10.61 | 35.7 |
| SRP | EIA D SRP | **no** | 2DA | 28 | 672 | 17.44 | 647 | 569 | 0.8655 | 10.96 | 35.7 |
| SRP | EIA D SRP | **no** | 7DA | 28 | 672 | 22.69 | 840 | 713 | 0.7515 | 17.13 | 32.1 |
| SRP | EIA D SRP | **no** | EIA DF | 28 | 672 | 17.52 | 649 | 600 | 0.8858 | 10.55 | 32.1 |
| TEPC | EIA D TEPC | **no** | DAM | 28 | 672 | 20.55 | 377 | -374 | 0.9467 | 15.85 | 39.3 |
| TEPC | EIA D TEPC | **no** | 2DA | 28 | 672 | 20.49 | 376 | -373 | 0.9412 | 15.37 | 35.7 |
| TEPC | EIA D TEPC | **no** | 7DA | 28 | 672 | 17.70 | 331 | -322 | 0.8566 | 12.20 | 14.3 |
| TEPC | EIA D TEPC | **no** | EIA DF | 28 | 672 | 3.44 | 67 | 1 | 0.9620 | 3.37 | 46.4 |
| TIDC | EIA D TIDC | **no** | DAM | 28 | 662 | 2.76 | 11 | -5 | 0.9885 | 4.72 | 60.7 |
| TIDC | EIA D TIDC | **no** | 2DA | 28 | 662 | 2.98 | 12 | -5 | 0.9849 | 5.01 | 64.3 |
| TIDC | EIA D TIDC | **no** | 7DA | 28 | 662 | 6.29 | 26 | -8 | 0.9002 | 9.78 | 71.4 |
| TIDC | EIA D TIDC | **no** | EIA DF | 28 | 662 | 3.70 | 14 | 4 | 0.9756 | 5.66 | 67.9 |
| TPWR | EIA D TPWR | yes | DAM | 28 | 672 | 3.36 | 14 | -12 | 0.9727 | 2.87 | 17.9 |
| TPWR | EIA D TPWR | yes | 2DA | 28 | 672 | 3.29 | 14 | -12 | 0.9720 | 2.92 | 14.3 |
| TPWR | EIA D TPWR | yes | 7DA | 28 | 672 | 3.68 | 16 | -8 | 0.9504 | 3.19 | 14.3 |
| TPWR | EIA D TPWR | yes | EIA DF | 28 | 672 | 1.55 | 7 | 2 | 0.9897 | 1.44 | 57.1 |
| WALC | EIA D WALC | **no** | DAM | 28 | 672 | 19.73 | 209 | -180 | 0.6858 | 20.30 | 3.6 |
| WALC | EIA D WALC | **no** | 2DA | 28 | 672 | 19.55 | 207 | -179 | 0.6958 | 19.54 | 0.0 |
| WALC | EIA D WALC | **no** | 7DA | 28 | 672 | 17.70 | 190 | -168 | 0.7315 | 17.35 | 0.0 |
| WALC | EIA D WALC | **no** | EIA DF | 28 | 672 | 14.86 | 153 | -8 | 0.5597 | 10.45 | 25.0 |


**No actual, so not scored (15 areas).** PGE-TAC, SCE-TAC, SDGE-TAC, VEA-TAC, MWD-TAC: CAISO publishes no actual load by TAC area that we bank. BANCMID, BANCRDNG, BANCRSVL, BANCSMUD, BANCWASN, WALCAEPCO, WALCDSW are sub-areas, and EIA reports only the parent BA. AVRN, BHBA, GWA: no EIA-930 respondent is banked under these codes.

What the table says:
- **The 7DA is materially worse than the DAM for the system:** MAE 2,168 against 693 MW, and r 0.79. A day three to six days out on the page should carry its score, and the scores card shows the per-product score beside the line.
- **AVA, TEPC and WALC are footprint mismatches, not bad forecasts.** CAISO's forecast sits 16–21% below EIA `D` while the BA's own DF tracks `D` at MAPE 1.3% (AVA) and 3.4% (TEPC). CAISO forecasts a different footprint from the one EIA reports. The scale test is what catches it.
- **LADWP's MAPE of 61% with an MAE of only 312 MW** comes from 21 hours of `D` below half its median (minimum 124 MW). The data are near-zero in shape, as recon said, but not under 1% of the median, so the brief's example threshold would have missed them.
- **NEVP and PACE line up best one hour later** (r at +1 h: 0.949 vs 0.930; 0.865 vs 0.791). That matches recon's "2026 +1" for the `D`-vs-DF clock.
- **DF against D:** PSEI DF is unusable (MAPE 36%, r 0.44). IPCO DF (6.3%) and NEVP DF (7.9%, bias −438) drift, as recon found. BPAT, PGE, PACW and NWMT DF beat CAISO's DAM for their own BA.

### 1.2 The usability rule for EIA-930 `D` as an actual

The rule is computed over the same 28 Pacific days as the scores, in one statement for every BA (`USABILITY_SQL`), so `/areas` and `/outlook` apply the same rule (`load_outlook.USABILITY_RULE`):

| test | rule | why |
| --- | --- | --- |
| coverage | hours of `D` present / hours in the window ≥ 0.95 | a score on a third of the hours is not the BA's |
| dropouts | hours of `D` below **½ the area's median** / hours ≤ 0.005 (3 of 672) | catches LDWP's 124 MW hours; the brief's "1% of median" finds none in this window |
| agreement | Pearson r of `D` against CAISO's DAM for the area ≥ 0.90 | `D` that does not move with the load it is meant to measure |
| clock | r at shift 0 is the best of −1, 0, +1 h | the CISO 2022–25 defect, and NEVP/PACE now |
| scale | median DAM / median `D` in [0.90, 1.10] | CAISO and EIA must measure the same footprint |

LDWP and PSEI were expected to fail. **LDWP fails (dropouts, agreement). PSEI passes:** its `D` agrees with CAISO's DAM better than any other BA's (r 0.990, scale 0.999). Recon's PSEI verdict was about its DF. Seven more fail, and the table names each failed test. In short, AVA and TEPC fail on footprint, NEVP on the clock, and AZPS, PACE, SRP, TIDC and WALC on agreement, together with dropouts, scale or the clock.


| BA | EIA | hours | dropouts (< ½ median) | r vs DAM, shift −1 / 0 / +1 h | median D MW | median DAM MW | scale | verdict | fails |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |
| AVA | AVA | 672 | 0 | 0.931 / **0.981** / 0.931 | 1280 | 1077 | 0.842 | **fail** | scale |
| AZPS | AZPS | 672 | 6 | 0.760 / **0.768** / 0.743 | 5688 | 5107 | 0.898 | **fail** | dropouts, agreement, scale |
| BANC | BANC | 672 | 0 | 0.917 / **0.949** / 0.921 | 1990 | 1854 | 0.932 | pass | — |
| BPAT | BPAT | 672 | 1 | 0.882 / **0.930** / 0.873 | 6660 | 6495 | 0.975 | pass | — |
| EPE | EPE | 672 | 0 | 0.950 / **0.977** / 0.958 | 1170 | 1138 | 0.973 | pass | — |
| IPCO | IPCO | 672 | 0 | 0.933 / **0.965** / 0.923 | 2020 | 2047 | 1.014 | pass | — |
| LADWP | LDWP | 672 | 21 | 0.781 / **0.797** / 0.780 | 3248 | 3142 | 0.967 | **fail** | dropouts, agreement |
| NEVP | NEVP | 672 | 0 | 0.863 / **0.930** / 0.949 | 5500 | 5184 | 0.943 | **fail** | clock |
| NWMT | NWMT | 672 | 0 | 0.896 / **0.929** / 0.851 | 1226 | 1234 | 1.006 | pass | — |
| PACE | PACE | 672 | 0 | 0.675 / **0.791** / 0.865 | 5838 | 5944 | 1.018 | **fail** | agreement, clock |
| PACW | PACW | 672 | 0 | 0.928 / **0.986** / 0.942 | 2194 | 2207 | 1.006 | pass | — |
| PGE | PGE | 672 | 0 | 0.918 / **0.973** / 0.921 | 2538 | 2567 | 1.011 | pass | — |
| PNM | PNM | 672 | 0 | 0.928 / **0.962** / 0.948 | 1694 | 1611 | 0.951 | pass | — |
| PSEI | PSEI | 672 | 0 | 0.927 / **0.990** / 0.935 | 2544 | 2542 | 0.999 | pass | — |
| SCL | SCL | 672 | 0 | 0.918 / **0.966** / 0.915 | 974 | 957 | 0.982 | pass | — |
| SRP | SRP | 672 | 17 | 0.851 / **0.874** / 0.857 | 4508 | 4960 | 1.100 | **fail** | dropouts, agreement, scale |
| TEPC | TEPC | 672 | 0 | 0.935 / **0.947** / 0.907 | 1830 | 1433 | 0.783 | **fail** | scale |
| TIDC | TIDC | 672 | 10 | 0.807 / **0.831** / 0.811 | 348 | 349 | 1.004 | **fail** | dropouts, agreement |
| TPWR | TPWR | 672 | 0 | 0.878 / **0.973** / 0.970 | 446 | 434 | 0.973 | pass | — |
| WALC | WALC | 672 | 0 | 0.684 / **0.686** / 0.655 | 1013 | 824 | 0.814 | **fail** | agreement, scale |

### 1.3 The CAISO name → EIA-930 respondent map, in full

The respondent names are EIA's own (row `meta.respondent_name`, read 2026-10-05). The map is `load_outlook.AREAS`, served by `GET /api/load/areas`.

| CAISO area | kind | EIA respondent | EIA's name |
| --- | --- | --- | --- |
| CA ISO-TAC | system | CISO | California Independent System Operator (scored against CAISO native, not `D`; D-09-25-112 cl. 3) |
| PGE-TAC, SCE-TAC, SDGE-TAC, VEA-TAC, MWD-TAC | tac | — | inside CISO; EIA has no TAC-level series |
| AVA | weim_ba | AVA | Avista Corporation |
| AVRN | weim_ba | — | Avangrid Renewables; not banked |
| AZPS | weim_ba | AZPS | Arizona Public Service Company |
| BANC | weim_ba | BANC | Balancing Authority of Northern California |
| BANCMID, BANCRDNG, BANCRSVL, BANCSMUD, BANCWASN | sub_area (of BANC) | — | EIA reports BANC whole |
| BHBA | weim_ba | — | not banked; the code's owner is not confirmed here, so its display name is the code |
| BPAT | weim_ba | BPAT | Bonneville Power Administration |
| EPE | weim_ba | EPE | El Paso Electric Company |
| GWA | weim_ba | — | not banked; display name is the code |
| IPCO | weim_ba | IPCO | Idaho Power Company |
| **LADWP** | weim_ba | **LDWP** | Los Angeles Department of Water and Power |
| NEVP | weim_ba | NEVP | Nevada Power Company |
| NWMT | weim_ba | NWMT | NorthWestern Corporation |
| PACE | weim_ba | PACE | PacifiCorp East |
| PACW | weim_ba | PACW | PacifiCorp West |
| **PGE** | weim_ba | **PGE** | **Portland General Electric Company** (not PG&E, which is PGE-TAC) |
| PNM | weim_ba | PNM | Public Service Company of New Mexico |
| PSEI | weim_ba | PSEI | Puget Sound Energy, Inc. |
| SCL | weim_ba | SCL | Seattle City Light |
| SRP | weim_ba | SRP | Salt River Project Agricultural Improvement and Power District |
| TEPC | weim_ba | TEPC | Tucson Electric Power |
| TIDC | weim_ba | TIDC | Turlock Irrigation District |
| TPWR | weim_ba | TPWR | City of Tacoma, Department of Public Utilities, Light Division |
| WALC | weim_ba | WALC | Western Area Power Administration - Desert Southwest Region |
| WALCAEPCO, WALCDSW | sub_area (of WALC) | — | EIA reports WALC whole |

Banked EIA respondents with **no** CAISO area: CHPD, DOPD, GCPD, IID, PSCO.

### 1.4 The net demand backtest (window 2026-09-06 → 10-03)

**Ours: 0 days scored, 28 not scored.** The route serves each day's stops, part by part (`unscored_days`):

| stopped by | days |
| --- | --- |
| wind: no 12Z issuance of D−1 banked | 28 of 28 (the wind backfill is 06Z every day, 09-01 → 10-02; the first live 12Z is 10-04 12Z) |
| solar: no 12Z issuance of D−1 banked | 8 (09-06, 09-08, 09-09, 09-11, 09-16, 09-22, 10-01, 10-03; on those days the backfill chose the 06Z) |
| solar: the 12Z of D−1 carries no calibrated figure (registry only) | 20 |
| load (DAM), truth | 0: both are complete every day |

**CAISO's DA net demand against the truth: 24 days scored** (09-10 → 10-03; the hub DAM series start 09-10), 576 hours. MAE 2,017 MW, bias **+1,833 MW**, WAPE 11.9%. By Pacific hour:

| hour PT | 00 | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| MAE MW | 793 | 955 | 903 | 829 | 837 | 908 | 759 | 848 | 2,343 | 3,283 | 3,607 | 3,679 | 3,748 | 3,694 | 3,551 | 3,443 | 3,799 | 3,140 | 1,482 | 1,292 | 1,238 | 1,171 | 1,066 | 1,029 |
| bias MW | +438 | +597 | +536 | +537 | +534 | +606 | +463 | +249 | +1,947 | +3,107 | +3,607 | +3,679 | +3,748 | +3,694 | +3,465 | +3,263 | +3,651 | +3,140 | +1,470 | +1,288 | +1,196 | +1,032 | +877 | +876 |
| hub DAM − fuel mix (solar + wind), MW | −432 | −573 | −542 | −516 | −456 | −487 | −355 | −119 | −1,880 | −2,963 | −3,219 | −3,208 | −3,235 | −3,150 | −2,832 | −2,569 | −2,853 | −2,395 | −719 | −424 | −459 | −448 | −469 | −514 |

The last row explains the bias. The hub forecasts leave out about 3.2 GW of the fuel mix's midday solar and wind, plus about 450 MW of overnight wind. Hourly MAPE is undefined at midday because truth falls to 1.2 GW at 11 PT and below zero on some hours (MAPE −1,746% at 11 PT), so the route serves **WAPE**. `PAIRING_RULE` states this.

### 1.5 Plans

Every statement the three routes run was run under `EXPLAIN (ANALYZE, BUFFERS)` on production with the bank's parameters (`docs/receipts/load-net-demand-api-d091611/plans.txt`, rendered by `render.py`). Every read of `timeseries_values` is an Index Scan on `idx_tsv_series_ts` with **dataset and series both in the Index Cond**, one LATERAL per series. Nothing is a Seq Scan.

| statement | access | rows | buffers | time |
| --- | --- | ---: | ---: | ---: |
| `FCST_SQL` (CA ISO-TAC, 3 products, 36 days) | 3 × Index Scan `idx_tsv_series_ts` | 2,328 | 2,782 | 3.3 ms |
| `SERIES_SQL` EIA DF BPAT | Index Scan `idx_tsv_series_ts` | 672 | 1,173 | 1.1 ms |
| `SERIES_SQL` native actual | Index Scan `idx_tsv_series_ts` | 692 | 382 | 0.7 ms |
| `SERIES_SQL` EIA D BPAT | Index Scan `idx_tsv_series_ts` | 690 | 1,148 | 1.1 ms |
| `USABILITY_SQL`, all 20 BAs (`/areas`) | 40 × Index Scan `idx_tsv_series_ts`; hash join on (area, ts) | 20 | 31,521 | **111 ms** |
| `HUB_DAM_SQL` | 6 × Index Scan `idx_tsv_series_ts` | 3,744 | 1,872 | 3.0 ms |
| `TRUTH_SQL` | 3 × Index Scan `idx_tsv_series_ts` | 2,076 | 1,301 | 2.3 ms |
| `GEN_NEWEST_SQL` | Index Only Scan Backward pkey, LIMIT 1 | 1 | 5 | 0.06 ms |
| `GEN_ROWS_SQL` newest issuance | Index Scan pkey | 181 | 10 | 0.17 ms |
| `GEN_ROWS_SQL` backtest, 28 inits | Index Scan pkey, `init_ts = ANY` | 1,320 | 85 | 0.85 ms |
| `LINES_SQL` | Index Scan `implied_gen_calibration_pkey` | 8 | 4 | 0.05 ms |

**One rewrite came from reading the plans.** The first `USABILITY_SQL` correlated `D` with the DAM at three clock shifts through a range join (`f.ts BETWEEN d.ts − 1h AND d.ts + 1h`). The planner hashed on area alone and filtered 9.0 M pairs, taking **934 ms**. An equality join with the shift as an expression did worse, at **6.8 s** and 27 M pairs, because the CTE estimates are 11 rows against a true 13,480. The shipped form materialises the shifted hour as a column (`ds AS MATERIALIZED`), so the hash key is (area, ts): **111 ms**, with identical results to 10 decimal places. `test_P1_the_correlation_join_hashes_on_area_and_ts` holds that form. A BA's `/outlook` runs the same statement for one area (1/20 of the rows).

---

## The routes

All three are memoised in `_DDCache` (300 s fresh, served stale for at most 900 s behind one refresh, single-flight, 15 s build backstop), read under `SET LOCAL statement_timeout = '5s'`, and carry the house `cache` block, `X-Cache` and `Age`. A bad param is a 400; the DB being unavailable is a 503.

### `GET /api/load/outlook?area=<CAISO area>`

Fields: `label`, `attributions`, `area {code, name, kind, group, parent, eia, eia_name}`, `today`, `tz`, `max_days_ahead`, `hours[] {target_ts, mw, product, issued_at, lead_days, days_ahead, df_mw}`, `days[] {day, product, product_name, issued_at, issue_date, lead_days, days_ahead, issued_days_ago, hours_in_day, hours_covered, complete, energy_mwh, peak_mw, peak_ts, peak_he, age_sentence, product_changes}`, `actuals[]` (today's elapsed hours, only when the actual is usable), `df` / `df_absent_reason`, `scores {DAM, 2DA, 7DA[, EIA_DF]}` (`status` scored | not_yet_scored | not_scored, with `text` and `reason`), `actual_basis {source, dataset, series, usable, rule, verdict, reason|note}`, `issue_sentence`, `newest_issues`, `absence`.

- **The product of a day** is the shortest horizon that covers every hour of the day, else the one covering the most hours. It is chosen from what is banked, never from the clock (§3 of what the brief got wrong).
- **Days 8 and later are not drawn.** The forecast read stops at the end of D+7 (L4).

Excerpt (`sample_outlook_ca_iso_tac.json`, D+3):

```json
{"day": "2026-10-07", "product": "7DA", "product_name": "CAISO seven-day-ahead (7DA)",
 "issued_at": "2026-09-30T16:10:00+00:00", "issue_date": "2026-09-30", "lead_days": 7,
 "days_ahead": 3, "issued_days_ago": 4, "hours_in_day": 24, "hours_covered": 24, "complete": true,
 "energy_mwh": 805243.72, "peak_mw": 42975.91, "peak_ts": "2026-10-08T00:00:00+00:00", "peak_he": 18,
 "age_sentence": "CAISO seven-day-ahead (7DA) for Wed Oct 7, issued Wed Sep 30 at 09:10 PT, 4 days ago. CAISO does not re-issue it.",
 "product_changes": true}
```

A failing BA (`sample_outlook_ladwp.json`):

```json
"DAM": {"status": "not_scored",
        "text": "not scored: EIA-930 D for LDWP fails the usability rule: more than 0.5% of its hours fall below half its median; its correlation with CAISO's DAM forecast is under 0.90",
        "reason": "...", "window_start": "2026-09-06", "window_end": "2026-10-03"}
```

### `GET /api/load/areas`

Fields: `count` (36), `window_start`, `window_end`, `usability_rule`, `groups {caiso, tac, western}`, and `areas[] {code, name, kind, group, parent, eia, eia_name, df_available, actual_source, scored, not_scored_reason, usability}`. The order is the system, the TACs, then the western areas, with each sub-area after its parent. One read covers all 20 BAs (`USABILITY_SQL`).

### `GET /api/load/net-demand?area=CISO`

Fields: `label`, `definition`, `truth_definition`, `reference_definition`, `issuances {solar, wind}`, and `hours[] {target_ts, load_mw, load_product, load_issued_at, load_absent_reason, solar_mw, solar_lead_h, solar_absent_reason, wind_mw, wind_lead_h, wind_absent_reason, net_demand_mw, absent_part, caiso_da_net_demand_mw, truth_mw}`. Then `days` (the load's), `stops {load|solar|wind: {present, last_ts, last_lead_h, stop, gaps}}`, `net_demand_stop {first_ts, last_ts, stop{…, parts}, gaps, sentence}`, `scores {ours, caiso_da}`, `unscored_days`, `pairing_rule`, `reference_scope_note`, `attributions` (CAISO; the turbine-data ODbL notice verbatim from `wind_outlook.ATTRIBUTION`), `issue_sentence`, and `absence`. Any other area returns 400 (clause 5).

- **A part shows a figure only when it is calibrated and the hour's lead lies inside the leads its line was fitted on.** Those leads are `implied_gen_calibration.fit_lead_min/max`, which the writer now stores (D-09-25-127). Solar's night hours are the exception: registry 0 is calibrated 0 by the writer's own `apply_line`, as long as the hour is within the line's furthest fitted lead. **No registry figure is served.**
- **`gaps`** are runs where a part drops out and comes back. Today they are solar leads 22–25 and 46–49 of the 18Z issuance: morning hours that the 06Z and 12Z backfill never fitted. **`stop`** is the run that reaches the end of the outlook.

Excerpt (`sample_net_demand_ciso.json`):

```json
{"target_ts": "2026-10-06T00:00:00+00:00", "load_mw": 46806.63, "load_product": "DAM",
 "solar_mw": 7005.53, "solar_lead_h": 31, "wind_mw": 955.063, "wind_lead_h": 25,
 "net_demand_mw": 38846.037, "absent_part": null, "caiso_da_net_demand_mw": 40283.14, "truth_mw": null}
```
`net_demand_stop.sentence`: "Net demand stops at 2026-10-07T12:00:00+00:00: solar (the hour's lead is outside the leads its line was fitted on, lead 67 h)."

### Banked for d091612

`docs/receipts/load-net-demand-api-d091611/`:
- `sample_outlook_ca_iso_tac.json`: system, scored, no DF.
- `sample_outlook_bpat.json`: a usable BA with DF.
- `sample_outlook_ladwp.json`: a failing BA with DF.
- `sample_outlook_pge_tac.json`: a TAC with no actual.
- `sample_areas.json`
- `sample_net_demand_ciso.json`

They are the routes' answers at 2026-10-05T03:30Z over production's rows (`tests/fixtures/load_outlook_d091611/production_2026_10_05_0330z.json`). `sample.py` remakes them, and `test_samples_are_the_routes_answer` holds three of them equal to the route.

---

## Tests, red then green

`tests/test_load_outlook.py`, 40 tests. The route tests run `main.py` over `tests/load_bank_d091611.py`, which answers each statement from production's banked rows by the statement's own parameters. Reds: `rehearse.py` breaks each rule in a throwaway copy of the tree, and the guarding tests must fail there (`reds.txt`):

| test | pins | break that turns it red |
| --- | --- | --- |
| L1 | each day carries its product, issue date and lead; D+3..D+6 say their age | the longest horizon wins; the hours lose `issued_at`; `issued_days_ago` dropped |
| L2 | a failing BA has no score and says why; production's verdicts | failing BAs scored anyway; the scale test dropped |
| L3 | DF only where EIA has the BA (and not the system's copy of the DAM) | DF for every area with an EIA code |
| L4 | no hour or day beyond D+7 | days to D+9 |
| N1 | net demand is null wherever a part is registry or null, naming the part; the gate | a registry figure stands in; the fitted-lead gate ignored |
| N2 | D's DAM load is paired with D−1 12Z's calibrated parts (06Z and 18Z decoys) | 06Z paired |
| N3 | DAM load − the six hub series, AVA_:Solar excluded, nothing with a hub missing | drawn with a hub missing |
| N4 | 13 days → not yet scored; 14 → scored (load and net demand) | `MIN_SCORED_DAYS = 13` |
| P1 | no Seq Scan in plans.txt; dataset+series in every Index Cond and every statement; the (area, ts) hash | the shift inlined; a read without its series |
| D-09-25-138 | past `max_stale_s` an entry is a miss | the cap ignored |
| D-09-25-75 | `SET LOCAL statement_timeout` runs first | the timeout dropped |

```
16 breaks, 16 RED; clean tree: GREEN 40 passed in 1.22s
full suite: 2329 passed in 46.62s
```

---

## What this brief got wrong

1. **"Scored at day-ahead from history" has no history to score yet.** The CISO implied-generation bank before 10-03 (solar) and 10-04 (wind) is the backfill. It holds one issuance a day, leads 1–66, with `calibrated_mw` null on every row. Wind's backfill is 06Z every day. Solar's is the writer's `dam_comparable` pick, which in this window gives a 12Z of D−1 for 20 days and a 06Z for 8. Under the pairing rule, ours therefore scores **0 of 28 days**. Live 12Z issuances started 10-03 (solar) and 10-04 (wind), so the first day both parts can be paired is D = 10-05. Fourteen scorable days run to 10-18, and the score can appear about **10-19**, provided every 12Z issuance lands and keeps its calibration through leads 19–43. The alternative is the line in force applied to the backfill's registry, but nothing in the bank carries it. I did not compute it, because it would be our derivation of a figure the writer never published. The captain can ask for it as a diagnostic.
2. **Clause 3's reference crosses scopes.** "DAM load − DAM hub solar − DAM hub wind" against "native − fuel-mix solar − fuel-mix wind" sets hub forecasts beside BA-wide totals. D-09-25-114 clause 6 forbids exactly this pairing for solar. The hubs leave out ~1.45 GW on average (~3.2 GW at midday), so CAISO's reference reads +1,833 MW biased, and most of that is scope. The route serves it as ruled, with `reference_scope_note`. A ruling is needed on one of three options: (a) keep it with the note; (b) score the reference against a hub-scoped truth (native − hub actual solar and wind, `caiso_renewables_hourly`); (c) drop the reference to DAM load alone.
3. **"D+1 DAM, D+2 2DA, D+3..D+7 7DA" is true only between CAISO's publication and midnight.** All three products publish at 09:10 PT, and the bank ingests them around 11:15 PT. Between midnight and the ingest, the newest DAM covers *today*, D+1 is the 2DA and D+2 the 7DA. The route picks each day's freshest banked product, so it is right at every hour of the day.
4. **"Solar is calibrated through lead 120" is not what the gate allows.** The writer now stores `fit_lead_min/max` (d091607's columns are populated). Solar's lines were fitted on the 06Z and 12Z backfill: h01_06 → 1–6, h07_24 → 7–21, h25_48 → 26–45, h49_120 → **50–66**. So gated solar stops at lead 66 today, like wind. The 18Z (and 00Z) cycles also have morning holes at leads 22–25 and 46–49. The writer still stores `calibrated_mw` at leads 67–120 for solar; the route does not serve it.
5. **The BANC sub-areas are not CAISO TACs.** BANC is a WEIM balancing authority with its own EIA respondent, and BANCMID/RDNG/RSVL/SMUD/WASN are its sub-areas, as WALCAEPCO and WALCDSW are WALC's. The brief's three kinds (system / TAC / WEIM BA) cannot hold the 36, so `/areas` adds `sub_area` with `parent`. The 36 also include AVRN, BHBA and GWA, which have no banked EIA respondent.
6. **PSEI was the wrong expected failure.** Its `D` passes every test. Recon's PSEI verdict was about the BA's DF, which is still unusable (MAPE 36%), and the page will show that as the DF's score. LDWP fails, but not because `D` is near zero relative to the median: its minimum is 124 MW, and 0 hours fall under 1% of the median. It fails on 21 hours under half the median and r 0.80.
7. **The usability rule is partly circular, as specified.** Judging `D` by its correlation with CAISO's DAM removes BAs where CAISO's forecast is poor as well as BAs where `D` is poor. AZPS, SRP and WALC fail "agreement" (r 0.69–0.87), and their own DF tracks `D` no better (r 0.56–0.89). That is the brief's example; the alternative is to judge `D` against the BA's own DF.
8. **EIA's CISO DF is CAISO's DAM.** "The BA's own forecast as a second line" would draw the DAM twice for the system, so it is not drawn and `df_absent_reason` says why.
9. **"Since 2026-07-23"** holds for the 7DA only. The DAM and 2DA begin on 2026-07-25.
10. **MAPE for net demand** is undefined at midday, where truth nears zero or goes negative, so the route serves WAPE and says why.
11. **The EIA `D` CISO clock.** The brief and recon place the one-hour label defect in 2022-06 → 2025-11. In this window, EIA `D` CISO still matches native best one hour shifted (r 0.877 against 0.848 unshifted) and sits **+2,485 MW** above native. It is unused here (the system is scored against native), but the clock is not settled.
12. **d091608 is not on main.** `max_stale_s` was added to `_DDCache` here. It is opt-in, and every existing memo is unchanged.

## Files

- `load_outlook.py` (new): the areas, the rule, the SQL and the shaping for all three routes.
- `main.py`:
  - the routes and memos (a new section after the wind outlook, before the season block, whose source sweep requires it to be last);
  - `_DDCache.max_stale_s`;
  - three lines in the route index.
- `tests/test_load_outlook.py`, `tests/load_bank_d091611.py`, `tests/fixtures/load_outlook_d091611/production_2026_10_05_0330z.json` (1.2 MB).
- `docs/receipts/load-net-demand-api-d091611/`:
  - `render.py`: the exact statements;
  - `plans.txt`;
  - `sample.py` and the six `sample_*.json`;
  - `rehearse.py` and `reds.txt`.
