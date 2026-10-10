# cpc_method_pin_d091691

`ddl_286_298.sql`: the CPC curve relations as Neon holds them since pantry
migration 298 (applied 2026-10-10 13:40:22Z), verbatim from energylake-pantry
`f2145f80709e12fbcc182ee4269bb2d1ee49bac0`:

- `migrations/286_cpc_outlook_curves_and_verdicts.sql` lines 100-337 (the two
  tables, their indexes, the view);
- `migrations/298_cpc_curves_short_base.sql` lines 135-313 (the helpers, the new
  columns and CHECKs, the view restated).

sha-256 `0740859fde8c0271dd5c3a317fcb69e3213172f07b79eae75bc61565167f61c4`.
The view it creates deparses to Neon's (`md5(pg_get_viewdef)` =
`66966f907581836370275d209d564a67` on both; `test_P0_the_view_is_neons`).

The rows are not here: `tests/load_bank_d091691.py` rebuilds the v1 bank from
d091679's sha-checked fixtures (`../cpc_outlooks_d091679`) and constructs the
v2 rows beside it, deterministically.
