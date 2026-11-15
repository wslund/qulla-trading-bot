"""U0: append-only snapshots of *current* instrument lists (UNIVERSE_DESIGN §5, §10 U0; D-019).

Every run stores the provider's raw response bytes unchanged plus a manifest:

    data/snapshots/<source>/<YYYYMMDDTHHMMSSZ>[_n]/<file>      raw bytes, exactly as received
    data/snapshots/<source>/<YYYYMMDDTHHMMSSZ>[_n]/manifest.json

manifest: source, fetched_at (UTC, our observation time), status (complete | incomplete |
failed), per file: url (without secrets), http status, bytes, sha256, provider time if the
provider states one (HTTP Last-Modified / Date, Nasdaq's "File Creation Time" trailer),
record_count when the payload parses, pages, rate-limit headers, error text. A directory is
never overwritten: a second run in the same second gets a ``_1`` suffix.

Status semantics: ``complete`` only when every file was fully received and passed its
completeness check (Nasdaq: header + trailer line; Alpaca: JSON array). Anything else is
``incomplete`` (something stored, not trustworthy as a full list) or ``failed`` (nothing
usable stored). Consumers (U1) must read the manifest and use ``complete`` snapshots only.

The two timestamps matter for point-in-time work: ``fetched_at`` is when *we* observed the
list (knowledge time); the provider time is the list's own effective time. Both are kept.

Sources:
- Nasdaq Trader symbol directory: ``nasdaqlisted.txt`` and ``otherlisted.txt`` (public,
  pipe-delimited, trailer ``File Creation Time: MMDDYYYYHH:MM``). Current listings only.
- Alpaca ``GET /v2/assets?asset_class=us_equity`` (trading API, paper or live host). Keys
  from the environment / ``.env``; never written anywhere. Current assets only.
- Massive (formerly Polygon) ``GET /v3/reference/tickers?market=stocks&date=…&active=…``
  (U2B-FREE-1): the tickers that were active, and separately those already inactive, on an
  *effective date*; paginated via ``next_url`` (1 000 rows a page), all pages kept raw. Basic
  plan: 5 calls/min, 2 years of reference history – the fetcher sleeps between calls
  (``min_interval``, injectable). The key goes in the ``Authorization: Bearer`` header only;
  any ``apiKey`` query parameter is stripped from URLs before they are stored. These lists are
  labelled ``effective_dated_reconstructed`` (D-023, D-024 p. 2): Massive returns "the record
  as it maintains it now" for a past date, so ``effective_date`` (what the list is about),
  ``fetched_at`` (when we fetched it) and the rows' ``last_updated_utc`` (Massive's own
  update time) are three different things and are never conflated with a verified
  point-in-time snapshot.

HTTP is injected (``http_get``) so tests run offline with fixtures; the default uses urllib
with retries on 429 (Retry-After) and 5xx, and never retries other 4xx.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

SNAPSHOT_VERSION = "1"
COMPLETE, INCOMPLETE, FAILED = "complete", "incomplete", "failed"

NASDAQ_FILES = {
    "nasdaqlisted.txt": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "otherlisted.txt": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
ALPACA_HOSTS = {"paper": "https://paper-api.alpaca.markets", "live": "https://api.alpaca.markets"}
MASSIVE_HOST = "https://api.massive.com"
MASSIVE_SOURCE = "massive_tickers"
MASSIVE_LABEL = "effective_dated_reconstructed"
MASSIVE_MIN_INTERVAL = 12.0  # seconds between calls: Basic plan allows 5 calls per minute
MASSIVE_MAX_PAGES = 200  # safety stop per list (~200 000 rows); never silently truncates
TRAILER_RE = re.compile(r"^File Creation Time:\s*(\d{10}:\d{2})")

HttpResponse = tuple[int, dict[str, str], bytes]
HttpGet = Callable[[str, dict[str, str]], HttpResponse]


class SnapshotError(RuntimeError):
    pass


def default_http_get(
    url: str, headers: dict[str, str], *, max_retries: int = 5, sleep=time.sleep, timeout: int = 60
) -> HttpResponse:
    """GET with retries on 429/5xx; other 4xx and network errors raise SnapshotError.
    Error messages never include request headers (credentials)."""
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed hosts
                return resp.status, {k: v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                if attempt == max_retries:
                    raise SnapshotError(f"HTTP {e.code} after {attempt} retries: {url}") from e
                sleep(float(e.headers.get("Retry-After") or 2 ** (attempt + 1)))
                continue
            raise SnapshotError(f"HTTP {e.code}: {url}") from e
        except urllib.error.URLError as e:
            raise SnapshotError(f"network error: {e.reason}: {url}") from e
    raise SnapshotError("unreachable")


@dataclass
class FileRecord:
    name: str
    url: str
    http_status: int | None = None
    bytes: int = 0
    sha256: str | None = None
    provider_time: str | None = None  # provider's own time for the list, if stated
    http_last_modified: str | None = None
    http_date: str | None = None
    record_count: int | None = None
    complete: bool = False
    error: str | None = None


@dataclass
class Manifest:
    source: str
    fetched_at: str  # UTC ISO, our observation (knowledge) time
    status: str
    files: list[FileRecord] = field(default_factory=list)
    rate_limit: dict[str, str] = field(default_factory=dict)
    pages: int = 0
    notes: str = ""
    snapshot_version: str = SNAPSHOT_VERSION
    extra: dict = field(default_factory=dict)  # source-specific facts (U2B-FREE-1: Massive)

    @property
    def sha256_combined(self) -> str:
        h = hashlib.sha256()
        for f in sorted(self.files, key=lambda x: x.name):
            h.update((f.sha256 or "").encode())
        return h.hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sha256_combined"] = self.sha256_combined
        return d


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "snapshots"

    def new_dir(self, source: str, fetched_at: datetime) -> Path:
        base = self.root / source
        base.mkdir(parents=True, exist_ok=True)
        stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
        d = base / stamp
        n = 0
        while d.exists():  # append-only: never reuse a directory
            n += 1
            d = base / f"{stamp}_{n}"
        d.mkdir()
        return d

    def write(self, d: Path, manifest: Manifest, payloads: dict[str, bytes]) -> Path:
        for name, data in payloads.items():
            p = d / name
            if p.exists():
                raise SnapshotError(f"refusing to overwrite {p}")
            p.write_bytes(data)
        (d / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return d

    def list(self, source: str) -> list[Path]:
        base = self.root / source
        return sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []

    def read_manifest(self, d: Path) -> dict:
        return json.loads((d / "manifest.json").read_text(encoding="utf-8"))

    def latest_complete(self, source: str) -> Path | None:
        for d in reversed(self.list(source)):
            if self.read_manifest(d).get("status") == COMPLETE:
                return d
        return None


# --- completeness checks (pure) ------------------------------------------------------------------


def check_nasdaq_file(data: bytes) -> tuple[bool, int | None, str | None, str | None]:
    """(complete, record_count, provider_time, error) for a Nasdaq Trader symbol file: a
    header line, pipe-delimited rows and a 'File Creation Time' trailer are required."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, None, None, f"not utf-8: {e}"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False, None, None, "too short"
    if "|" not in lines[0] or "Symbol" not in lines[0]:
        return False, None, None, "header missing"
    m = TRAILER_RE.match(lines[-1])
    if not m:
        return False, len(lines) - 1, None, "trailer 'File Creation Time' missing (truncated?)"
    raw = m.group(1)  # MMDDYYYYHH:MM
    try:  # naive: the file states no timezone (Nasdaq documents US Eastern; not asserted here)
        provider_time = datetime.strptime(raw, "%m%d%Y%H:%M").isoformat(timespec="minutes")
    except ValueError:
        provider_time = raw
    rows = lines[1:-1]
    bad = [ln for ln in rows if ln.count("|") != lines[0].count("|")]
    if bad:
        return False, len(rows), provider_time, f"{len(bad)} rows with wrong field count"
    return True, len(rows), provider_time, None


def check_alpaca_assets(data: bytes) -> tuple[bool, int | None, str | None]:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return False, None, f"not a JSON document: {type(e).__name__}"
    if not isinstance(obj, list):
        return False, None, "not a JSON array"
    if not all(isinstance(x, dict) and "symbol" in x for x in obj):
        return False, len(obj), "elements without 'symbol'"
    return True, len(obj), None


# --- fetchers --------------------------------------------------------------------------------


def snapshot_nasdaq(
    store: SnapshotStore, *, http_get: HttpGet = default_http_get, now: datetime | None = None
) -> Path:
    fetched_at = now or datetime.now(UTC)
    man = Manifest("nasdaq_trader", fetched_at.isoformat(), FAILED)
    payloads: dict[str, bytes] = {}
    for name, url in NASDAQ_FILES.items():
        rec = FileRecord(name, url)
        try:
            status, headers, data = http_get(url, {"Accept": "text/plain"})
            rec.http_status = status
            rec.bytes = len(data)
            rec.sha256 = hashlib.sha256(data).hexdigest()
            rec.http_last_modified = _hdr(headers, "Last-Modified")
            rec.http_date = _hdr(headers, "Date")
            ok, n, ptime, err = check_nasdaq_file(data)
            rec.complete, rec.record_count, rec.provider_time, rec.error = ok, n, ptime, err
            payloads[name] = data  # keep even partial bytes; status says what they are
        except SnapshotError as e:
            rec.error = str(e)
        man.files.append(rec)
    man.pages = len([f for f in man.files if f.http_status])
    man.notes = "provider_time = 'File Creation Time' trailer, naive (no timezone in file)"
    man.status = _status(man.files, payloads)
    d = store.new_dir(man.source, fetched_at)
    return store.write(d, man, payloads)


def snapshot_alpaca_assets(
    store: SnapshotStore,
    *,
    host: str = "paper",
    http_get: HttpGet = default_http_get,
    now: datetime | None = None,
    api_key: str | None = None,
    secret_key: str | None = None,
) -> Path:
    key = api_key or os.environ.get("ALPACA_API_KEY", "")
    secret = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
    fetched_at = now or datetime.now(UTC)
    source = f"alpaca_assets_{host}"
    url = f"{ALPACA_HOSTS[host]}/v2/assets?asset_class=us_equity"
    man = Manifest(source, fetched_at.isoformat(), FAILED)
    rec = FileRecord("assets.json", url)
    payloads: dict[str, bytes] = {}
    if not key or not secret:
        rec.error = "ALPACA_API_KEY / ALPACA_SECRET_KEY not set"
    else:
        try:
            status, headers, data = http_get(
                url,
                {
                    "APCA-API-KEY-ID": key,
                    "APCA-API-SECRET-KEY": secret,
                    "Accept": "application/json",
                },
            )
            rec.http_status = status
            rec.bytes = len(data)
            rec.sha256 = hashlib.sha256(data).hexdigest()
            rec.http_date = _hdr(headers, "Date")
            rec.http_last_modified = _hdr(headers, "Last-Modified")
            man.rate_limit = {
                k: v for k, v in headers.items() if k.lower().startswith("x-ratelimit")
            }
            ok, n, err = check_alpaca_assets(data)
            rec.complete, rec.record_count, rec.error = ok, n, err
            payloads[rec.name] = data
        except SnapshotError as e:
            rec.error = str(e)
    man.files.append(rec)
    man.pages = 1 if rec.http_status else 0
    man.notes = "Alpaca /v2/assets returns the full list in one response (no pagination)"
    man.status = _status(man.files, payloads)
    d = store.new_dir(source, fetched_at)
    return store.write(d, man, payloads)


# --- Massive reference tickers (U2B-FREE-1) --------------------------------------------------


def scrub_url(url: str) -> str:
    """Remove any apiKey query parameter so a URL can be stored or shown. Keeps the rest."""
    parts = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() != "apikey"]  # fmt: skip
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def check_massive_page(data: bytes) -> tuple[bool, int | None, str | None, str | None]:
    """(complete, record_count, next_url, error) for one /v3/reference/tickers page. A page
    is complete when it is a JSON object with status OK and a results list."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return False, None, None, f"not a JSON document: {type(e).__name__}"
    if not isinstance(obj, dict):
        return False, None, None, "not a JSON object"
    if obj.get("status") != "OK":
        return False, None, None, f"status {obj.get('status')!r}: {obj.get('error') or ''}".strip()
    results = obj.get("results")
    if not isinstance(results, list):
        return False, None, None, "results missing"
    nxt = obj.get("next_url")
    return True, len(results), (str(nxt) if nxt else None), None


def massive_tickers_url(effective_date: date, active: bool, limit: int = 1000) -> str:
    q = urllib.parse.urlencode(
        {
            "market": "stocks",
            "date": effective_date.isoformat(),
            "active": "true" if active else "false",
            "limit": limit,
            "order": "asc",
            "sort": "ticker",
        }
    )
    return f"{MASSIVE_HOST}/v3/reference/tickers?{q}"


class _Pacer:
    """Keeps successive calls at least ``min_interval`` seconds apart (Basic: 5/min)."""

    def __init__(self, min_interval: float, sleep, clock) -> None:
        self.min_interval, self.sleep, self.clock = min_interval, sleep, clock
        self.last: float | None = None
        self.slept = 0.0

    def wait(self) -> None:
        now = self.clock()
        if self.last is not None:
            gap = self.min_interval - (now - self.last)
            if gap > 0:
                self.sleep(gap)
                self.slept += gap
                now = now + gap
        self.last = now


def snapshot_massive_tickers(
    store: SnapshotStore,
    *,
    effective_date: date,
    http_get: HttpGet = default_http_get,
    now: datetime | None = None,
    api_key: str | None = None,
    sleep=time.sleep,
    clock=time.monotonic,
    min_interval: float = MASSIVE_MIN_INTERVAL,
    limit: int = 1000,
) -> Path:
    """Archive Massive's stock ticker lists for ``effective_date``: every page of
    ``active=true`` (``active_pNNNN.json``) and of ``active=false`` (``inactive_pNNNN.json``),
    raw. ``active=false`` means *inactive on that date*, not "delisted after it". Status is
    complete only when both lists reached a page without ``next_url`` and every page parsed;
    anything else is incomplete (pages kept) or failed. The key is sent in the Authorization
    header only and never stored; should a response body echo it (e.g. inside ``next_url``)
    the stored bytes have it replaced by ``REDACTED`` and the page is listed in
    ``extra.redacted_files`` – the only edit ever made to raw bytes."""
    key = api_key or os.environ.get("MASSIVE_API_KEY", "")
    fetched_at = now or datetime.now(UTC)
    man = Manifest(MASSIVE_SOURCE, fetched_at.isoformat(), FAILED)
    man.extra = {
        "label": MASSIVE_LABEL,
        "effective_date": effective_date.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "min_interval_s": min_interval,
        "lists": {},
        "active_count": 0,
        "inactive_count": 0,
        "last_updated_utc_min": None,
        "last_updated_utc_max": None,
        "redacted_files": [],  # pages where the API key was replaced by REDACTED before storing
    }
    man.notes = (
        "effective-dated list reconstructed by Massive for the requested date; not a verified "
        "point-in-time snapshot. active=false = inactive on effective_date. Rows keep Massive's "
        "own fields (ticker, cik, composite_figi, share_class_figi, type, primary_exchange, "
        "delisted_utc, last_updated_utc) unchanged; FIGI/CIK may be missing or shared."
    )
    payloads: dict[str, bytes] = {}
    if not key:
        man.files.append(
            FileRecord(
                "active_p0001.json", scrub_url(massive_tickers_url(effective_date, True, limit))
            )
        )
        man.files[-1].error = "MASSIVE_API_KEY not set"
        man.status = FAILED
        return store.write(store.new_dir(MASSIVE_SOURCE, fetched_at), man, payloads)
    pacer = _Pacer(min_interval, sleep, clock)
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    lu: list[str] = []
    for name, active in (("active", True), ("inactive", False)):
        url: str | None = massive_tickers_url(effective_date, active, limit)
        pages, rows, finished, err = 0, 0, False, None
        while url is not None and pages < MASSIVE_MAX_PAGES:
            pages += 1
            rec = FileRecord(f"{name}_p{pages:04d}.json", scrub_url(url))
            pacer.wait()
            try:
                status, hdrs, data = http_get(scrub_url(url), headers)
            except SnapshotError as e:
                rec.error = err = str(e)
                man.files.append(rec)
                break
            if key.encode() in data:  # a next_url echoing the key: never store it
                data = data.replace(key.encode(), b"REDACTED")
                man.extra["redacted_files"].append(rec.name)
            rec.http_status, rec.bytes = status, len(data)
            rec.sha256 = hashlib.sha256(data).hexdigest()  # of the stored bytes
            rec.http_date = _hdr(hdrs, "Date")
            man.rate_limit.update(
                {k: v for k, v in hdrs.items() if "ratelimit" in k.lower().replace("-", "")}
            )
            ok, n, nxt, perr = check_massive_page(data)
            rec.complete, rec.record_count, rec.error = ok, n, perr
            payloads[rec.name] = data
            man.files.append(rec)
            if not ok:
                err = perr
                break
            rows += n or 0
            lu.extend(_last_updated(data))
            if nxt is None:
                finished = True
            url = nxt
        if url is not None and pages >= MASSIVE_MAX_PAGES and err is None:
            err = f"stopped after {MASSIVE_MAX_PAGES} pages with next_url still present"
        man.extra["lists"][name] = {
            "pages": pages,
            "rows": rows,
            "complete": finished and err is None,
            "error": err,
        }
        man.extra[f"{name}_count"] = rows
    man.pages = sum(v["pages"] for v in man.extra["lists"].values())
    man.extra["slept_s"] = round(pacer.slept, 3)
    if lu:
        man.extra["last_updated_utc_min"], man.extra["last_updated_utc_max"] = min(lu), max(lu)
    lists_ok = man.extra["lists"] and all(v["complete"] for v in man.extra["lists"].values())
    man.status = _status(man.files, payloads) if lists_ok else (INCOMPLETE if payloads else FAILED)
    return store.write(store.new_dir(MASSIVE_SOURCE, fetched_at), man, payloads)


def _last_updated(data: bytes) -> list[str]:
    try:
        rows = json.loads(data.decode("utf-8")).get("results", [])
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return []
    return [
        str(r["last_updated_utc"])
        for r in rows
        if isinstance(r, dict) and r.get("last_updated_utc")
    ]


def _status(files: list[FileRecord], payloads: dict[str, bytes]) -> str:
    if files and all(f.complete for f in files):
        return COMPLETE
    return INCOMPLETE if payloads else FAILED


def _hdr(headers: dict[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None
