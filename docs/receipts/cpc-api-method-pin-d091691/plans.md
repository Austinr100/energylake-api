# d091691 plan receipts

Neon production (`fancy-block-96153928`), read-only, 2026-10-10 ~17:35–17:50Z,
one sitting: each statement run cold, then warm; the warm run is banked.
`explains.sql` holds each statement: **B** = main's (d091679's pin), **A** = this
lane's pin, the params as literals. `plans_raw.txt` is every plan line verbatim,
cut from the connector's own results by `extract_plans.py` (matched character
for character). `make_explains.py` writes `explains.sql` from the two pins.
The bank at the sitting: v1 only (no v2 row yet), 428,264 curve rows (610temp
183,600, 814temp 244,664), 720 verdict rows.

| read | d091679 | B (main, today) | A (pinned, today) | A / d091679 | `iss` / newest scan, A |
|---|---:|---:|---:|---:|---|
| 01 /curves 610 KSAN n=1 | 1.253 ms | 0.837 ms | 0.839 ms | 0.67× | Index Only Scan Backward `uniq_coc_row`, 1 entry |
| 02 /curves 814 KSAN n=1 | 0.922 ms | 0.843 ms | 0.857 ms | 0.93× | same, 1 entry |
| 03 vintages 814 pnw n=14 | 8.112 ms | 7.692 ms | 9.259 ms | 1.14× | same, 1,769 entries (B: `idx_coc_issued`, 1,769) |
| 04 vintages KDEN n=14 | 0.829 ms | 0.610 ms | 0.850 ms | 1.03× | same, 1,327 entries, 0 heap fetches |
| 05 /places | 3.555 ms | 3.724 ms | 3.674 ms | 1.03× | seq scan of the 51-page verdict table, ×3, as before |
| 06 /places newest | 0.325 ms | 0.182 ms | 0.181 ms | 0.56× | Index Only Scan Backward `uniq_coc_row`, 1 entry per product |

**What the pin changed in the plans.**
- The issuance scans move from `idx_coc_issued (product, issued_date DESC)` to
  `uniq_coc_row (product, issued_date, …, method_version)` read backward:
  `method_version` is in that key, so it is an Index Cond and the scan stays
  index-only (0 or 1 heap fetches, as B). No new index is needed today.
- Every other `cpc_outlook_curves` probe is the same `uniq_coc_row` lookup with
  `method_version` now an equality in the key, not a join filter.
- The verdict reads (`nv` lateral, `hashes`, `inforce`, the cells) were seq
  scans of the 720-row table in d091679 and B; they still are, with one more
  filter. No statement scans a table it did not scan before. STOP-P not met.

**Named risk (for the flip lane).** With the pin on v1 and v2 written daily,
the v1 issuance scans walk past every v2 entry newer than v1's last issuance
(~200 entries a product a day, index-only) until the flip. And if v1's rows
are ever deleted while the pin still names v1, the scan reads every entry of
the product to find none: the CLAUDE.md trap. Flip before retiring v1.
