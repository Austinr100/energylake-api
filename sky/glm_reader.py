"""The GLM reader — one background loop per satellite.

List the current hour's prefix every 20 s, fetch only the keys not seen
before, parse them, push the flashes into a `FlashWindow`, prune, forget.
Nothing is written anywhere but memory (ruling S-5).

────────────────────────────────────────────────────────────────────────────
NO NEW DEPENDENCY. The transport is `urllib.request` from the standard
library, not boto3 and not httpx. The NODD buckets are anonymous public
reads over plain HTTPS, so a signed client buys nothing, and the service
this reader is destined for (`energylake-api` on Railway) then needs exactly
ONE line added to its `requirements.txt` — `netCDF4` — instead of three.

────────────────────────────────────────────────────────────────────────────
THE HOUR BOUNDARY, AND THE GUARD THAT ACTUALLY TRIPS. The prefix is
`.../{YYYY}/{DDD}/{HH}/`. At 15:00:03 the current hour's prefix holds one
file and the other forty-four the 15-minute window needs are under the
14:xx prefix, which the reader would never list again. So: whenever the
current minute is under `WINDOW_MAX_MINUTES + 1`, the previous hour is
listed too.

Per house law D-08-02-J — *state the production input that trips the guard
and check that it actually occurs.* This one trips for sixteen minutes of
every hour, on every tick, forever; it is not a rare path. The cost is one
extra listing (67,768 B of XML, 508 ms measured) on 48 of the 180 ticks in
an hour, and zero extra object fetches, because every key under the old
prefix is already in `_seen`.

────────────────────────────────────────────────────────────────────────────
A FILE THAT FAILS TO PARSE IS LOGGED WITH ITS KEY AND NEVER RETRIED. Not
"retried with backoff" — never. A truncated or malformed object stays
malformed, and the spec's own words are "never retried in a tight loop".
The key goes into `_failed` with its reason, and `_failed` is pruned on the
same clock as the window so the ledger cannot grow without bound either.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, NamedTuple

from sky.glm import (
    FILE_SPAN_SECONDS,
    GLM_SATELLITES,
    WINDOW_MAX_MINUTES,
    FlashWindow,
    GLMParseError,
    hour_prefix,
    parse_glm_key,
    read_flashes,
)

log = logging.getLogger("sky.glm")

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

#: The wire's own cadence. One tick per file.
TICK_SECONDS = 20.0

#: A listing or object GET that has not answered in this long is abandoned;
#: the next tick will pick the key up again because it never entered `_seen`.
HTTP_TIMEOUT_SECONDS = 15.0

USER_AGENT = "energylake-sky-glm/1.0 (+https://energylake.io)"


class ListedObject(NamedTuple):
    key: str
    size: int


@dataclass
class TickStats:
    """What one pass cost. This is the price table's raw material."""
    at: datetime
    prefixes: tuple[str, ...] = ()
    listed: int = 0
    list_seconds: float = 0.0
    fetched: int = 0
    bytes_fetched: int = 0
    fetch_seconds: float = 0.0
    parse_seconds: float = 0.0
    flashes_added: int = 0
    flashes_dropped: int = 0
    skipped_stale: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    resident_flashes: int = 0
    resident_files: int = 0

    @property
    def parse_ms_per_file(self) -> float | None:
        if not self.fetched:
            return None
        return 1000.0 * self.parse_seconds / self.fetched


# ───────────────────────────────────────────────────────────────────────────
# Transport
# ───────────────────────────────────────────────────────────────────────────

class NoddTransport:
    """Anonymous HTTPS reads against a NODD mirror bucket.

    Injectable so every test in `tests/test_sky_glm.py` runs with no network
    at all: the suite hands the reader a transport backed by the vendored
    fixture and a synthetic listing.
    """

    def __init__(self, base: str = "https://{bucket}.s3.amazonaws.com/"):
        self._base = base

    def _url(self, bucket: str, path: str = "") -> str:
        return self._base.format(bucket=bucket) + path

    def list_prefix(self, bucket: str, prefix: str) -> list[ListedObject]:
        """Every object under `prefix`, following continuation tokens.

        An hour holds 180 keys and `max-keys` is 1000, so the loop normally
        runs once — but it is a loop rather than a single GET because a
        truncated listing that is silently treated as complete would drop
        the tail of the window with no symptom but missing lightning.
        """
        out: list[ListedObject] = []
        token: str | None = None
        for _ in range(10):  # a bounded loop; 10 pages is 10,000 keys
            query = f"?list-type=2&prefix={urllib.parse.quote(prefix)}&max-keys=1000"
            if token:
                query += "&continuation-token=" + urllib.parse.quote(token)
            body = self._get(self._url(bucket) + query)
            root = ET.fromstring(body)
            for c in root.findall(f"{_S3_NS}Contents"):
                key = c.findtext(f"{_S3_NS}Key") or ""
                size = int(c.findtext(f"{_S3_NS}Size") or 0)
                if key:
                    out.append(ListedObject(key, size))
            if (root.findtext(f"{_S3_NS}IsTruncated") or "false").lower() != "true":
                return out
            token = root.findtext(f"{_S3_NS}NextContinuationToken")
            if not token:
                return out
        log.warning("sky.glm: listing of %s/%s hit the page bound", bucket, prefix)
        return out

    def get_object(self, bucket: str, key: str) -> bytes:
        return self._get(self._url(bucket, key))

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return resp.read()

# ───────────────────────────────────────────────────────────────────────────
# The reader
# ───────────────────────────────────────────────────────────────────────────

class GLMReader:
    """One satellite's rolling window, and the loop that fills it."""

    def __init__(
        self,
        sat: str,
        transport: NoddTransport | None = None,
        retain_minutes: int = WINDOW_MAX_MINUTES,
        clock: Callable[[], datetime] | None = None,
    ):
        if sat not in GLM_SATELLITES:
            raise GLMParseError(f"unknown satellite {sat!r}")
        self.sat = sat
        self.bucket = GLM_SATELLITES[sat]
        self.transport = transport or NoddTransport()
        self.window = FlashWindow(sat, retain_minutes=retain_minutes)
        self.retain_minutes = retain_minutes
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._seen: set[str] = set()
        self._failed: dict[str, tuple[datetime, str]] = {}
        self.lock = threading.RLock()
        self.last_tick: TickStats | None = None
        self.ticks = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- one pass -----------------------------------------------------------
    def prefixes_for(self, now: datetime) -> tuple[str, ...]:
        """The prefixes a tick at `now` must list. See the hour-boundary note."""
        cur = hour_prefix(self.sat, now)
        if now.minute < self.retain_minutes + 1:
            return (hour_prefix(self.sat, now - timedelta(hours=1)), cur)
        return (cur,)

    def tick(self, now: datetime | None = None) -> TickStats:
        """List, fetch what is new, parse, add, prune. Returns the price."""
        now = now or self._clock()
        st = TickStats(at=now)
        st.prefixes = self.prefixes_for(now)

        listed: list[ListedObject] = []
        t0 = time.perf_counter()
        for prefix in st.prefixes:
            try:
                listed.extend(self.transport.list_prefix(self.bucket, prefix))
            except (urllib.error.URLError, OSError, ET.ParseError) as exc:
                # A listing that fails is a tick that read nothing. It is NOT
                # an empty window: the window keeps what it has and the next
                # tick tries again in 20 s. Loud in the log, invisible to the
                # route beyond the receipt headers going stale — which is
                # exactly what the receipt is for.
                log.warning("sky.glm[%s]: listing %s failed: %s",
                            self.sat, prefix, exc)
        st.list_seconds = time.perf_counter() - t0
        st.listed = len(listed)

        # Deduplicated by key. Two prefixes are listed for sixteen minutes of
        # every hour, and any overlap between them — or a listing that repeats
        # a key across continuation pages — would otherwise fetch and parse
        # the same object twice in ONE tick, which also double-counts it into
        # `_failed` and burns a second GET on a file already known bad.
        #
        # AND filtered against the retention BEFORE any GET. This is the cold
        # start, and without it the first tick of a fresh container fetches
        # every key in the hour prefix — up to 180 files and 45 MB measured —
        # and then `prune` throws 135 of them away microseconds later. The
        # window can only ever hold 15 minutes, so a file that is already
        # older than that is not worth its 250 kB. Steady state is unaffected
        # (every key is fresh by definition); the saving is entirely at boot
        # and after any gap in the loop, which is exactly when a container is
        # least able to afford it.
        horizon = now - timedelta(minutes=self.retain_minutes)
        candidates = {}
        for obj in listed:
            if obj.key in self._seen or obj.key in self._failed:
                continue
            try:
                if parse_glm_key(obj.key).end < horizon:
                    st.skipped_stale += 1
                    continue
            except GLMParseError:
                pass  # let the per-file handler below record it properly
            candidates[obj.key] = obj
        fresh = list(candidates.values())

        for obj in fresh:
            try:
                info = parse_glm_key(obj.key)
            except GLMParseError as exc:
                self._note_failure(obj.key, f"key: {exc}", now, st)
                continue
            if info.sat != self.sat:
                self._note_failure(obj.key, f"key names {info.sat}", now, st)
                continue

            t0 = time.perf_counter()
            try:
                blob = self.transport.get_object(self.bucket, obj.key)
            except (urllib.error.URLError, OSError) as exc:
                # Deliberately NOT recorded in `_failed`: a transport error
                # says nothing about the object, and the key stays out of
                # `_seen` so the next tick retries it once, 20 s later. That
                # is a retry on the wire's own clock, not a tight loop.
                log.warning("sky.glm[%s]: GET %s failed: %s", self.sat, obj.key, exc)
                continue
            st.fetch_seconds += time.perf_counter() - t0
            st.fetched += 1
            st.bytes_fetched += len(blob)

            t0 = time.perf_counter()
            try:
                flashes = read_flashes(blob, key=obj.key)
            except GLMParseError as exc:
                st.parse_seconds += time.perf_counter() - t0
                self._note_failure(obj.key, str(exc), now, st)
                continue
            st.parse_seconds += time.perf_counter() - t0

            with self.lock:
                self._seen.add(obj.key)
                st.flashes_added += self.window.add_file(info, flashes)

        with self.lock:
            st.flashes_dropped = self.window.prune(now)
            self._forget_stale(now)
            st.resident_flashes = len(self.window)
            st.resident_files = self.window.file_count

        self.ticks += 1
        self.last_tick = st
        return st

    def _note_failure(self, key: str, reason: str, now: datetime,
                      st: TickStats) -> None:
        """Record a bad file and never look at it again."""
        self._failed[key] = (now, reason)
        st.failures.append((key, reason))
        log.error("sky.glm[%s]: UNPARSEABLE %s -- %s (not retried)",
                  self.sat, key, reason)

    def _forget_stale(self, now: datetime) -> None:
        """Drop `_seen`/`_failed` entries older than the window.

        Without this the two sets grow by 180 keys an hour forever — the
        rolling window would forget while its bookkeeping quietly banked,
        which is ruling S-5 defeated by a side door. Both are keyed on the
        object name's own scan stamp, so the cutoff is the same clock the
        flashes use, with one file-span of slack.
        """
        cutoff = now - timedelta(minutes=self.retain_minutes,
                                 seconds=FILE_SPAN_SECONDS)
        for key in [k for k in self._seen if _key_is_stale(k, cutoff)]:
            self._seen.discard(key)
        for key in [k for k in self._failed if _key_is_stale(k, cutoff)]:
            self._failed.pop(key, None)

    # -- the loop -----------------------------------------------------------
    def run_forever(self, interval: float = TICK_SECONDS) -> None:
        """Tick on the wire's cadence until `stop()`.

        A tick that raises does not kill the loop — it is logged and the next
        one goes ahead, because the alternative is a proxy that answers 200
        with a frozen window and no way to tell. The receipt headers make a
        frozen window visible (`X-GLM-Newest` stops advancing), which is why
        surviving here is safe and silence would not be.
        """
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the loop must outlive one tick
                log.exception("sky.glm[%s]: tick raised", self.sat)
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def start(self, interval: float = TICK_SECONDS) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run_forever, args=(interval,),
            name=f"sky-glm-{self.sat}", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    # -- introspection ------------------------------------------------------
    @property
    def failed_keys(self) -> dict[str, tuple[datetime, str]]:
        return dict(self._failed)

    def health(self) -> dict:
        """What `/sky/glm/health` reports. Measured, never asserted."""
        with self.lock:
            newest = self.window.newest()
            now = self._clock()
            return {
                "sat": self.sat,
                "bucket": self.bucket,
                "ticks": self.ticks,
                "flashes": len(self.window),
                "files": self.window.file_count,
                "retain_minutes": self.retain_minutes,
                "newest": newest.isoformat().replace("+00:00", "Z") if newest else None,
                "newest_age_seconds": (
                    round((now - newest).total_seconds(), 1) if newest else None),
                "failed_keys": len(self._failed),
            }


def _key_is_stale(key: str, cutoff: datetime) -> bool:
    """True when the object's own scan end predates the cutoff.

    An unparseable key is stale by definition: it can never be placed in
    time, so keeping it in the ledger would leak one entry per occurrence.
    """
    try:
        return parse_glm_key(key).end < cutoff
    except GLMParseError:
        return True
