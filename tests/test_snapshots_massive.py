"""U2B-FREE-1: Massive /v3/reference/tickers snapshots – offline, synthetic pages only.
No real Massive calls. The key must never reach disk, manifests or error text."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import urllib.error
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradingbot.data import snapshots as mod
from tradingbot.data.snapshots import (
    COMPLETE,
    FAILED,
    INCOMPLETE,
    MASSIVE_LABEL,
    MASSIVE_SOURCE,
    SnapshotError,
    SnapshotStore,
    check_massive_page,
    massive_tickers_url,
    scrub_url,
    snapshot_alpaca_assets,
    snapshot_massive_tickers,
    snapshot_nasdaq,
)

from .test_snapshots import ASSETS, FakeHttp, nasdaq_http

D0 = date(2026, 11, 1)
T0 = datetime(2026, 11, 15, 20, 0, 0, tzinfo=UTC)
KEY = "mk-SECRET-0123456789"
A1 = massive_tickers_url(D0, True)
I1 = massive_tickers_url(D0, False)
A2 = f"{mod.MASSIVE_HOST}/v3/reference/tickers?cursor=QUNUSVZFLXBhZ2Uy"
I2 = f"{mod.MASSIVE_HOST}/v3/reference/tickers?cursor=SU5BQ1RJVkUtcGFnZTI"


def row(ticker: str, **kw) -> dict:
    base = {
        "ticker": ticker,
        "name": f"Synthetic {ticker} Inc",
        "market": "stocks",
        "locale": "us",
        "primary_exchange": "XNAS",
        "type": "CS",
        "active": True,
        "currency_name": "usd",
        "cik": "0000000001",
        "composite_figi": f"BBG00000{ticker:0>4}",
        "share_class_figi": f"BBG00001{ticker:0>4}",
        "last_updated_utc": "2026-11-14T00:00:00Z",
    }
    base.update(kw)
    return base


def page(results: list[dict], next_url: str | None = None, status: str = "OK") -> bytes:
    obj = {"status": status, "request_id": "r", "count": len(results), "results": results}
    if next_url:
        obj["next_url"] = next_url
    return json.dumps(obj).encode()


ACTIVE_1 = [row("AAA"), row("BBB", cik=None, composite_figi=None, share_class_figi=None)]
ACTIVE_2 = [row("CCC", type=None, primary_exchange=None)]
# ticker "AAA" was also used by an older, now-inactive issuer: same symbol, other FIGI/CIK
INACTIVE_1 = [
    row(
        "AAA",
        name="Old AAA Corp",
        active=False,
        cik="0000000777",
        composite_figi="BBG0000OLDAAA",
        share_class_figi="BBG0001OLDAAA",
        delisted_utc="2019-06-28T00:00:00Z",
        last_updated_utc="2025-03-01T00:00:00Z",
    ),
    row("ZZZ", active=False, delisted_utc=None, last_updated_utc="2026-11-13T00:00:00Z"),
]
HDR = {"Date": "Sat, 15 Nov 2026 20:00:01 GMT", "X-RateLimit-Limit": "5"}


def full_http() -> FakeHttp:
    return FakeHttp(
        {
            A1: (200, HDR, page(ACTIVE_1, next_url=A2 + "&apiKey=" + KEY)),
            A2: (200, HDR, page(ACTIVE_2)),
            I1: (200, HDR, page(INACTIVE_1, next_url=I2)),
            I2: (200, HDR, page([])),
        }
    )


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def run(http, tmp_path, **kw):
    clock = Clock()
    store = SnapshotStore(tmp_path)
    d = snapshot_massive_tickers(
        store,
        effective_date=D0,
        http_get=http,
        now=T0,
        api_key=KEY,
        sleep=clock.sleep,
        clock=clock,
        **kw,
    )
    return store, d, store.read_manifest(d), clock


# --- helpers -----------------------------------------------------------------------------------


def test_url_builder_and_scrub():
    assert A1.startswith(f"{mod.MASSIVE_HOST}/v3/reference/tickers?")
    assert "market=stocks" in A1 and "date=2026-11-01" in A1 and "active=true" in A1
    assert "limit=1000" in A1 and "active=false" in I1
    dirty = A2 + "&apiKey=" + KEY
    assert KEY not in scrub_url(dirty) and scrub_url(dirty) == A2
    assert scrub_url(A1) == A1
    assert KEY not in scrub_url(f"{mod.MASSIVE_HOST}/x?apiKey={KEY}&a=1")


def test_page_check():
    ok, n, nxt, err = check_massive_page(page(ACTIVE_1, next_url=A2))
    assert (ok, n, nxt, err) == (True, 2, A2, None)
    assert check_massive_page(page([]))[:3] == (True, 0, None)
    assert check_massive_page(b"<html>")[0] is False
    assert check_massive_page(b"[]")[3] == "not a JSON object"
    ok, _, _, err = check_massive_page(
        json.dumps({"status": "ERROR", "error": "Unknown API Key"}).encode()
    )
    assert not ok and "ERROR" in err
    assert check_massive_page(json.dumps({"status": "OK"}).encode())[3] == "results missing"


# --- complete fetch -----------------------------------------------------------------------------


def test_complete_fetch_two_lists_four_pages_raw_and_labelled(tmp_path):
    http = full_http()
    store, d, man, clock = run(http, tmp_path)
    assert man["source"] == MASSIVE_SOURCE and man["status"] == COMPLETE
    assert man["pages"] == 4 and d.parent.name == MASSIVE_SOURCE
    names = [f["name"] for f in man["files"]]
    assert names == [
        "active_p0001.json",
        "active_p0002.json",
        "inactive_p0001.json",
        "inactive_p0002.json",
    ]
    x = man["extra"]
    assert x["label"] == MASSIVE_LABEL and x["effective_date"] == "2026-11-01"
    assert x["fetched_at"] == T0.isoformat() and man["fetched_at"] == T0.isoformat()
    assert x["active_count"] == 3 and x["inactive_count"] == 2
    assert x["lists"] == {
        "active": {"pages": 2, "rows": 3, "complete": True, "error": None},
        "inactive": {"pages": 2, "rows": 2, "complete": True, "error": None},
    }
    assert x["last_updated_utc_min"] == "2025-03-01T00:00:00Z"
    assert x["last_updated_utc_max"] == "2026-11-14T00:00:00Z"
    assert man["rate_limit"] == {"X-RateLimit-Limit": "5"}
    assert "not a verified point-in-time" in man["notes"]
    # raw bytes kept unchanged, with sha256 per page
    for f in man["files"]:
        data = (d / f["name"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"] and f["complete"]
    # instrument fields preserved as delivered, including missing FIGI/CIK and reused ticker
    act1 = json.loads((d / "active_p0001.json").read_bytes())["results"]
    assert (
        act1[1]["ticker"] == "BBB" and act1[1]["cik"] is None and act1[1]["composite_figi"] is None
    )
    act2 = json.loads((d / "active_p0002.json").read_bytes())["results"]
    assert act2[0]["type"] is None and act2[0]["primary_exchange"] is None
    ina = json.loads((d / "inactive_p0001.json").read_bytes())["results"]
    assert ina[0]["ticker"] == "AAA" and ina[0]["delisted_utc"] == "2019-06-28T00:00:00Z"
    assert ina[0]["composite_figi"] != act1[0]["composite_figi"]  # same symbol, other issuer
    assert ina[1]["delisted_utc"] is None  # inactive without a recorded last trade date


def test_key_goes_in_header_only_and_never_to_disk_or_urls(tmp_path):
    http = full_http()
    store, d, man, clock = run(http, tmp_path)
    for url, headers in http.calls:
        assert headers["Authorization"] == f"Bearer {KEY}"
        assert "apiKey" not in url and KEY not in url
    # Massive's next_url carried an apiKey param in this scenario: it was stripped before use
    assert http.calls[1][0] == A2
    blob = "".join(p.read_text(encoding="utf-8") for p in d.iterdir())
    assert KEY not in blob and "Bearer" not in blob
    # the first active page echoed the key inside next_url: stored with REDACTED, flagged, hashed
    assert man["extra"]["redacted_files"] == ["active_p0001.json"]
    stored = (d / "active_p0001.json").read_bytes()
    assert b"REDACTED" in stored and hashlib.sha256(stored).hexdigest() == man["files"][0]["sha256"]
    assert all(KEY not in f["url"] and "apiKey" not in f["url"] for f in man["files"])
    assert man["files"][1]["url"] == A2


def test_pacing_keeps_calls_at_least_min_interval_apart(tmp_path):
    http = full_http()
    store, d, man, clock = run(http, tmp_path, min_interval=12.0)
    # first call immediate, then 3 waits of 12 s (clock does not advance by itself)
    assert clock.sleeps == [12.0, 12.0, 12.0] and man["extra"]["slept_s"] == 36.0
    http = full_http()
    store, d, man, clock = run(http, tmp_path, min_interval=0.0)
    assert clock.sleeps == [] and man["status"] == COMPLETE


# --- failures: never complete ------------------------------------------------------------------


def test_http_429_is_retried_by_default_http_get_with_retry_after(monkeypatch, tmp_path):
    import io

    class Resp(io.BytesIO):
        status = 200
        headers = {"Date": "x"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    script = [
        urllib.error.HTTPError("u", 429, "too many", {"Retry-After": "7"}, io.BytesIO(b"")),
        Resp(page(ACTIVE_1)),
    ]
    seen = []
    monkeypatch.setattr(
        mod.urllib.request, "urlopen", lambda req, timeout=0: _pop(script, seen, req)
    )
    slept: list[float] = []
    status, headers, data = mod.default_http_get(
        A1, {"Authorization": f"Bearer {KEY}"}, sleep=slept.append
    )
    assert status == 200 and slept == [7.0] and len(seen) == 2
    assert all(r.get_header("Authorization") == f"Bearer {KEY}" for r in seen)


def test_http_403_stops_list_and_snapshot_is_incomplete_without_key_leak(tmp_path):
    err = SnapshotError(f"HTTP 403: {A2}")  # default_http_get never includes headers in text
    http = FakeHttp(
        {
            A1: (200, HDR, page(ACTIVE_1, next_url=A2)),
            A2: err,
            I1: (200, HDR, page(INACTIVE_1)),
        }
    )
    store, d, man, clock = run(http, tmp_path)
    assert man["status"] == INCOMPLETE
    x = man["extra"]["lists"]
    assert x["active"] == {"pages": 2, "rows": 2, "complete": False, "error": f"HTTP 403: {A2}"}
    assert x["inactive"]["complete"] is True
    assert [f["name"] for f in man["files"]] == [
        "active_p0001.json",
        "active_p0002.json",
        "inactive_p0001.json",
    ]
    assert man["files"][1]["http_status"] is None and man["files"][1]["bytes"] == 0
    assert (d / "active_p0001.json").exists() and not (d / "active_p0002.json").exists()
    assert KEY not in (d / "manifest.json").read_text()


def test_timeout_on_first_page_leaves_failed_or_incomplete_never_complete(tmp_path):
    http = FakeHttp(
        {
            A1: SnapshotError("network error: timed out: " + A1),
            I1: SnapshotError("network error: timed out: " + I1),
        }
    )
    store, d, man, clock = run(http, tmp_path)
    assert (
        man["status"] == FAILED
        and man["pages"] == 2
        and not any(p.name != "manifest.json" for p in d.iterdir())
    )
    assert all("timed out" in f["error"] for f in man["files"])


def test_interrupted_pagination_bad_page_is_incomplete(tmp_path):
    http = FakeHttp(
        {
            A1: (200, HDR, page(ACTIVE_1, next_url=A2)),
            A2: (200, HDR, b'{"status":"OK","results":[{"ticker":"CCC"'),  # cut off mid-body
            I1: (200, HDR, page(INACTIVE_1)),
        }
    )
    store, d, man, clock = run(http, tmp_path)
    assert man["status"] == INCOMPLETE
    assert man["extra"]["lists"]["active"]["complete"] is False
    assert "not a JSON document" in man["extra"]["lists"]["active"]["error"]
    assert (d / "active_p0002.json").exists()  # partial bytes kept, flagged
    assert man["files"][1]["complete"] is False and man["extra"]["active_count"] == 2


def test_error_status_page_is_incomplete(tmp_path):
    http = FakeHttp(
        {
            A1: (200, HDR, page([], status="ERROR")),
            I1: (200, HDR, page(INACTIVE_1)),
        }
    )
    store, d, man, clock = run(http, tmp_path)
    assert man["status"] == INCOMPLETE and man["extra"]["lists"]["active"]["complete"] is False


def test_missing_key_fails_before_any_network_call(tmp_path, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    http = FakeHttp({})
    store = SnapshotStore(tmp_path)
    d = snapshot_massive_tickers(store, effective_date=D0, http_get=http, now=T0)
    man = store.read_manifest(d)
    assert man["status"] == FAILED and http.calls == []
    assert "MASSIVE_API_KEY not set" in man["files"][0]["error"]


def test_page_cap_never_truncates_silently(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "MASSIVE_MAX_PAGES", 2)
    loop = f"{mod.MASSIVE_HOST}/v3/reference/tickers?cursor=LOOP"
    http = FakeHttp(
        {
            A1: (200, HDR, page([row("AAA")], next_url=loop)),
            loop: (200, HDR, page([row("AAB")], next_url=loop)),
            I1: (200, HDR, page([])),
        }
    )
    store, d, man, clock = run(http, tmp_path)
    assert man["status"] == INCOMPLETE
    assert "stopped after 2 pages" in man["extra"]["lists"]["active"]["error"]


# --- append-only, U0 untouched, repo hygiene --------------------------------------------------


def test_append_only_and_u0_sources_unchanged(tmp_path):
    store, d1, man1, _ = run(full_http(), tmp_path)
    store, d2, man2, _ = run(full_http(), tmp_path)
    assert (
        d1 != d2 and d2.name.endswith("_1") and man1["sha256_combined"] == man2["sha256_combined"]
    )
    # U0 still works side by side and its manifests are unchanged in substance
    dn = snapshot_nasdaq(store, http_get=nasdaq_http(), now=T0)
    mn = store.read_manifest(dn)
    assert mn["status"] == COMPLETE and mn["source"] == "nasdaq_trader" and mn["extra"] == {}
    url = f"{mod.ALPACA_HOSTS['paper']}/v2/assets?asset_class=us_equity"
    da = snapshot_alpaca_assets(
        store, http_get=FakeHttp({url: (200, {}, ASSETS)}), now=T0, api_key="k", secret_key="s"
    )
    assert store.read_manifest(da)["status"] == COMPLETE
    assert store.latest_complete(MASSIVE_SOURCE) == d2
    assert [p.name for p in store.list(MASSIVE_SOURCE)] == [d1.name, d2.name]


def test_raw_data_directory_is_git_ignored():
    root = Path(__file__).resolve().parents[1]
    rules = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/**" in rules and ".env" in rules


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_massive_requires_date_and_prints_no_key(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "snapshot_cli", Path(__file__).resolve().parents[1] / "scripts" / "snapshot_listings.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    with pytest.raises(SystemExit):
        cli.main(["--sources", "massive", "--data-dir", str(tmp_path)])
    assert "--date" in capsys.readouterr().err
    monkeypatch.setattr(
        cli,
        "snapshot_massive_tickers",
        lambda store, effective_date, min_interval: snapshot_massive_tickers(
            store,
            effective_date=effective_date,
            http_get=full_http(),
            now=T0,
            api_key=KEY,
            sleep=lambda s: None,
            clock=lambda: 0.0,
            min_interval=min_interval,
        ),
    )
    rc = cli.main(["--sources", "massive", "--date", "2026-11-01", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "massive_tickers" in out and "complete" in out
    assert "effective_date=2026-11-01" in out and MASSIVE_LABEL in out
    assert "active=3 inactive=2" in out and KEY not in out and "Bearer" not in out
    monkeypatch.setattr(
        cli,
        "snapshot_massive_tickers",
        lambda store, effective_date, min_interval: snapshot_massive_tickers(
            store,
            effective_date=effective_date,
            http_get=FakeHttp({A1: SnapshotError("HTTP 403: " + A1), I1: (200, HDR, page([]))}),
            now=T0,
            api_key=KEY,
            sleep=lambda s: None,
            clock=lambda: 0.0,
        ),
    )
    rc = cli.main(["--sources", "massive", "--date", "2026-11-01", "--data-dir", str(tmp_path)])
    assert rc == 1 and "incomplete" in capsys.readouterr().out


def _pop(script, seen, req):
    seen.append(req)
    item = script.pop(0)
    if isinstance(item, Exception):
        raise item
    return item
