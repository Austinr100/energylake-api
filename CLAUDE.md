# energylake-api

Rules this repo's routes are held to.

- **D-09-25-75:** a polled route's reads each name one `(dataset, series)`, answer from a single-flight memo, and carry a statement timeout.
- **Trap:** `max(ingested_ts)` over a dataset with no `series` walks the whole dataset when the window is empty. The window is empty every morning.
- **D-09-25-76:** `condition_text` is the source's words verbatim, or null. Nothing is derived from it, and the page decides what to print.
- **Newest row per dataset (d091551):** use one `LATERAL (… WHERE dataset = … AND series = … ORDER BY ts DESC LIMIT 1)` per `(dataset, series)`, as in `_REGIME_DRIVERS_SQL`, not `DISTINCT ON (dataset)`. The `DISTINCT ON` form reads every row to keep one.
