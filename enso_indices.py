"""
The ENSO monthly indices, read-only, from the bank — `GET /api/enso/indices` (d091493).

Two series in `timeseries_values`, written by the pantry's CPC ingest:

    oni    dataset cpc_oni_monthly   series oni
    roni   dataset cpc_roni_monthly  series roni

`ts` is the three-month season's CENTRE month at 00:00 UTC (JJA → YYYY-07-01),
so each row is served as that month, "YYYY-MM". 919 rows each today, 1950-01 →
2026-07.

This module holds the read, `build_payload` (pure) and the content ETag. The
route itself (parse, memo, headers) lives in `main.py`, beside
/api/enso/catalog.

NUMERICS. `value` is `numeric`, which psycopg hands back as `Decimal`. Unlike
enso_catalog.py (which casts in SQL and refuses a Decimal), this module converts
explicitly: `_value` turns Decimal/int/float into a finite float and refuses
anything else by name. The route answers with a plain JSONResponse, so a
Decimal that ever slipped past `build_payload` would raise at serialisation
instead of being quietly coerced by jsonable_encoder.

NULLS. A null `value` is dropped from `values` and counted in `n_null`; `n` is
the rows read, so `len(values) == n - n_null`; `first` / `last` are the first
and last months actually served (a trailing null does not become `last`).

ETAG. `W/"<first 16 hex of sha256>"` over the canonical JSON of the served
values (index -> [[month, value], ...]) — content-addressed, never a clock. A
re-ingest that rewrites identical values keeps the tag (and `last_ingested` is
deliberately not in the hash).
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

# index -> (dataset, series); order here is the order of the body's keys.
INDICES: dict[str, tuple[str, str]] = {
    "oni": ("cpc_oni_monthly", "oni"),
    "roni": ("cpc_roni_monthly", "roni"),
}
DEFAULT_INDICES = tuple(INDICES)

SERIES_SQL = """
    SELECT ts, value, ingested_ts
    FROM timeseries_values WHERE dataset = %(d)s AND series = %(s)s
    ORDER BY ts
"""


def parse_indices(raw: str | None) -> tuple[str, ...]:
    """`?index=` → the requested indices in canonical order, deduplicated.
    Absent → both. Anything not in INDICES (including an empty token) →
    ValueError naming the allowed set."""
    if raw is None:
        return DEFAULT_INDICES
    asked = [t.strip() for t in raw.split(",")]
    bad = [t for t in asked if t not in INDICES]
    if bad:
        raise ValueError(f"unknown index {', '.join(repr(t) for t in bad)}; "
                         f"allowed: {', '.join(INDICES)}")
    return tuple(i for i in INDICES if i in asked)


def _value(index: str, v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (Decimal, int, float)):
        raise TypeError(f"{index} value is {type(v).__name__}, expected numeric")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"{index} value {v!r} is not finite")
    return f


def _utc(name: str, ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware (timestamptz)")
    return ts.astimezone(timezone.utc)


def _month(index: str, ts: datetime) -> str:
    t = _utc("ts", ts)
    if (t.day, t.hour, t.minute, t.second, t.microsecond) != (1, 0, 0, 0, 0):
        raise ValueError(f"{index} ts {t.isoformat()} is not a month start (UTC)")
    return f"{t.year:04d}-{t.month:02d}"


def _iso_z(ts: datetime) -> str:
    return _utc("ingested_ts", ts).isoformat().replace("+00:00", "Z")


def build_series(index: str, rows: Iterable[Mapping[str, Any]]) -> dict:
    """One index's block. Pure: no DB, no clock. Raises on zero rows — the
    route turns that into a 503 before calling here."""
    dataset, series = INDICES[index]
    rows = list(rows)
    if not rows:
        raise ValueError(f"no rows for {index}")
    values: list[list] = []
    n_null = 0
    for r in rows:
        month = _month(index, r["ts"])
        if r["value"] is None:
            n_null += 1
            continue
        values.append([month, _value(index, r["value"])])
    values.sort(key=lambda p: p[0])
    months = [m for m, _ in values]
    if len(set(months)) != len(months):
        raise ValueError(f"{index} has more than one row for a month")
    ingested = [r["ingested_ts"] for r in rows if r.get("ingested_ts") is not None]
    return {
        "dataset": dataset,
        "series": series,
        "n": len(rows),
        "n_null": n_null,
        "first": months[0] if months else None,
        "last": months[-1] if months else None,
        "last_ingested": _iso_z(max(ingested)) if ingested else None,
        "values": values,
    }


def build_payload(rows_by_index: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict:
    """The route's body: `{"indices": {index: block}}`, keys in INDICES order."""
    return {"indices": {i: build_series(i, rows_by_index[i])
                        for i in INDICES if i in rows_by_index}}


def etag(payload: Mapping[str, Any]) -> str:
    canon = json.dumps({i: b["values"] for i, b in payload["indices"].items()},
                       separators=(",", ":"), sort_keys=True, allow_nan=False)
    return f'W/"{hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]}"'


def etag_matches(if_none_match: str | None, tag: str) -> bool:
    """Weak comparison (RFC 9110 §13.1.2) against a possibly comma-listed header."""
    if not if_none_match:
        return False
    want = tag.removeprefix("W/")
    for cand in if_none_match.split(","):
        cand = cand.strip()
        if cand == "*" or cand.removeprefix("W/") == want:
            return True
    return False
