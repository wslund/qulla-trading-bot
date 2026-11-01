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
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SNAPSHOT_VERSION = "1"
COMPLETE, INCOMPLETE, FAILED = "complete", "incomplete", "failed"

NASDAQ_FILES = {
    "nasdaqlisted.txt": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "otherlisted.txt": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
ALPACA_HOSTS = {"paper": "https://paper-api.alpaca.markets", "live": "https://api.alpaca.markets"}
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


def _status(files: list[FileRecord], payloads: dict[str, bytes]) -> str:
    if files and all(f.complete for f in files):
        return COMPLETE
    return INCOMPLETE if payloads else FAILED


def _hdr(headers: dict[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None
