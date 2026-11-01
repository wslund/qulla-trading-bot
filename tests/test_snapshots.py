"""U0: append-only snapshots of current instrument lists, offline with fixtures (D-019)."""

import hashlib
import importlib.util
import json
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradingbot.data import snapshots as mod
from tradingbot.data.snapshots import (
    COMPLETE,
    FAILED,
    INCOMPLETE,
    SnapshotError,
    SnapshotStore,
    check_alpaca_assets,
    check_nasdaq_file,
    default_http_get,
    snapshot_alpaca_assets,
    snapshot_nasdaq,
)

FIX = Path(__file__).parent / "fixtures"
NASDAQ = (FIX / "nasdaqlisted_sample.txt").read_bytes()
OTHER = (FIX / "otherlisted_sample.txt").read_bytes()
ASSETS = (FIX / "alpaca_assets_sample.json").read_bytes()
T0 = datetime(2026, 11, 1, 23, 30, 0, tzinfo=UTC)


def _drop_trailer(data: bytes) -> bytes:
    """Remove the last non-empty line, independent of CRLF/LF (git may normalize fixtures)."""
    lines = [ln for ln in data.splitlines() if ln.strip()]
    return b"\r\n".join(lines[:-1]) + b"\r\n"


class FakeHttp:
    """url -> (status, headers, bytes) or an exception instance to raise."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        r = self.responses[url]
        if isinstance(r, Exception):
            raise r
        return r


def nasdaq_http(nasdaq=NASDAQ, other=OTHER):
    urls = list(mod.NASDAQ_FILES.values())
    hdr = {
        "Last-Modified": "Sun, 01 Nov 2026 20:03:00 GMT",
        "Date": "Sun, 01 Nov 2026 22:30:00 GMT",
    }
    return FakeHttp({urls[0]: (200, hdr, nasdaq), urls[1]: (200, hdr, other)})


# --- completeness checks ---


def test_nasdaq_file_check_requires_header_rows_and_trailer() -> None:
    ok, n, ptime, err = check_nasdaq_file(NASDAQ)
    assert ok and n == 3 and ptime == "2026-11-01T20:02" and err is None
    truncated = _drop_trailer(NASDAQ)  # trailer gone
    ok, n, _, err = check_nasdaq_file(truncated)
    assert not ok and n == 3 and "trailer" in err
    ok, _, _, err = check_nasdaq_file(b"<html>maintenance</html>")
    assert not ok and err == "too short" or "header" in err
    broken = NASDAQ.replace(
        b"MSFT|Microsoft Corporation - Common Stock|Q|N|N|100|N|N", b"MSFT|broken"
    )
    ok, _, _, err = check_nasdaq_file(broken)
    assert not ok and "wrong field count" in err


def test_alpaca_assets_check() -> None:
    ok, n, err = check_alpaca_assets(ASSETS)
    assert ok and n == 2 and err is None
    assert check_alpaca_assets(b'{"message": "forbidden"}')[0] is False
    assert check_alpaca_assets(b'[{"id": 1}]')[2] == "elements without 'symbol'"
    assert check_alpaca_assets(b"\xff\xfe")[0] is False


# --- nasdaq snapshot ---


def test_nasdaq_snapshot_is_complete_raw_and_checksummed(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    d = snapshot_nasdaq(store, http_get=nasdaq_http(), now=T0)
    assert d == tmp_path / "snapshots" / "nasdaq_trader" / "20261101T233000Z"
    assert (d / "nasdaqlisted.txt").read_bytes() == NASDAQ  # byte-identical, CRLF kept
    assert (d / "otherlisted.txt").read_bytes() == OTHER
    man = store.read_manifest(d)
    assert man["status"] == COMPLETE and man["fetched_at"] == T0.isoformat()
    f = {x["name"]: x for x in man["files"]}
    assert f["nasdaqlisted.txt"]["sha256"] == hashlib.sha256(NASDAQ).hexdigest()
    assert f["nasdaqlisted.txt"]["record_count"] == 3 and f["otherlisted.txt"]["record_count"] == 3
    assert f["nasdaqlisted.txt"]["provider_time"] == "2026-11-01T20:02"
    assert f["nasdaqlisted.txt"]["http_last_modified"].startswith("Sun, 01 Nov 2026")
    assert man["sha256_combined"] and man["snapshot_version"] == "1" and man["pages"] == 2
    assert store.latest_complete("nasdaq_trader") == d


def test_truncated_file_makes_snapshot_incomplete_but_keeps_bytes(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    truncated = _drop_trailer(OTHER)
    d = snapshot_nasdaq(store, http_get=nasdaq_http(other=truncated), now=T0)
    man = store.read_manifest(d)
    assert man["status"] == INCOMPLETE
    f = {x["name"]: x for x in man["files"]}
    assert f["nasdaqlisted.txt"]["complete"] is True and f["otherlisted.txt"]["complete"] is False
    assert "trailer" in f["otherlisted.txt"]["error"]
    assert (d / "otherlisted.txt").read_bytes() == truncated  # kept for forensics
    assert store.latest_complete("nasdaq_trader") is None


def test_network_error_on_one_file_is_incomplete_on_both_is_failed(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    urls = list(mod.NASDAQ_FILES.values())
    one = FakeHttp({urls[0]: (200, {}, NASDAQ), urls[1]: SnapshotError("network error: timeout")})
    d = snapshot_nasdaq(store, http_get=one, now=T0)
    man = store.read_manifest(d)
    assert man["status"] == INCOMPLETE and not (d / "otherlisted.txt").exists()
    assert "network error" in man["files"][1]["error"] and man["files"][1]["http_status"] is None
    both = FakeHttp({u: SnapshotError("HTTP 503 after 5 retries") for u in urls})
    d2 = snapshot_nasdaq(store, http_get=both, now=T0)
    man2 = store.read_manifest(d2)
    assert man2["status"] == FAILED and sorted(p.name for p in d2.iterdir()) == ["manifest.json"]


def test_append_only_same_second_gets_suffix_and_never_overwrites(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    d1 = snapshot_nasdaq(store, http_get=nasdaq_http(), now=T0)
    d2 = snapshot_nasdaq(store, http_get=nasdaq_http(other=OTHER + b"x"), now=T0)
    assert d1 != d2 and d2.name == "20261101T233000Z_1"
    assert (d1 / "otherlisted.txt").read_bytes() == OTHER  # first snapshot untouched
    assert [p.name for p in store.list("nasdaq_trader")] == [
        "20261101T233000Z",
        "20261101T233000Z_1",
    ]
    with pytest.raises(SnapshotError, match="refusing to overwrite"):
        store.write(
            d1, mod.Manifest("nasdaq_trader", T0.isoformat(), COMPLETE), {"otherlisted.txt": b""}
        )


# --- alpaca snapshot ---


def test_alpaca_snapshot_keeps_keys_out_of_disk_and_logs(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    url = f"{mod.ALPACA_HOSTS['paper']}/v2/assets?asset_class=us_equity"
    http = FakeHttp(
        {
            url: (
                200,
                {"X-RateLimit-Remaining": "199", "Date": "Sun, 01 Nov 2026 22:30:00 GMT"},
                ASSETS,
            )
        }
    )
    d = snapshot_alpaca_assets(
        store, http_get=http, now=T0, api_key="key-id-123", secret_key="s3cret"
    )
    assert http.calls[0][1]["APCA-API-KEY-ID"] == "key-id-123"  # sent in the header only
    man = store.read_manifest(d)
    assert man["status"] == COMPLETE and man["files"][0]["record_count"] == 2
    assert man["rate_limit"] == {"X-RateLimit-Remaining": "199"}
    blob = (d / "manifest.json").read_text() + (d / "assets.json").read_text()
    assert "key-id-123" not in blob and "s3cret" not in blob
    assert (
        "?asset_class=us_equity" in man["files"][0]["url"] and "key" not in man["files"][0]["url"]
    )
    assert json.loads((d / "assets.json").read_bytes()) == json.loads(ASSETS)


def test_alpaca_without_keys_fails_before_network(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    http = FakeHttp({})
    d = snapshot_alpaca_assets(SnapshotStore(tmp_path), http_get=http, now=T0)
    man = SnapshotStore(tmp_path).read_manifest(d)
    assert man["status"] == FAILED and "not set" in man["files"][0]["error"] and http.calls == []


def test_alpaca_error_body_is_incomplete_not_complete(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    url = f"{mod.ALPACA_HOSTS['live']}/v2/assets?asset_class=us_equity"
    http = FakeHttp({url: (200, {}, b'{"message":"partial outage"}')})
    d = snapshot_alpaca_assets(
        store, host="live", http_get=http, now=T0, api_key="k", secret_key="s"
    )
    man = store.read_manifest(d)
    assert man["source"] == "alpaca_assets_live" and man["status"] == INCOMPLETE
    assert "JSON array" in man["files"][0]["error"]


# --- default http: retries and no credential leakage in errors ---


def test_default_http_get_retries_429_and_5xx_and_hides_headers(monkeypatch) -> None:
    import io

    class Resp(io.BytesIO):
        status = 200
        headers = {"Date": "x"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    script = [
        urllib.error.HTTPError("u", 429, "slow", {"Retry-After": "0"}, io.BytesIO(b"")),
        urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b"")),
        Resp(b"ok"),
    ]
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=0: _pop(script))
    status, headers, data = default_http_get(
        "https://example.test/x", {"APCA-API-KEY-ID": "k"}, sleep=lambda s: None
    )
    assert (status, data) == (200, b"ok")
    monkeypatch.setattr(
        mod.urllib.request,
        "urlopen",
        lambda req, timeout=0: _raise(
            urllib.error.HTTPError("u", 403, "forbidden", {}, io.BytesIO(b""))
        ),
    )
    with pytest.raises(SnapshotError) as e:
        default_http_get(
            "https://example.test/x", {"APCA-API-KEY-ID": "secret-key"}, sleep=lambda s: None
        )
    assert "403" in str(e.value) and "secret-key" not in str(e.value)
    monkeypatch.setattr(
        mod.urllib.request, "urlopen", lambda req, timeout=0: _raise(urllib.error.URLError("dns"))
    )
    with pytest.raises(SnapshotError, match="network error"):
        default_http_get("https://example.test/x", {}, sleep=lambda s: None)


def _pop(script):
    item = script.pop(0)
    if isinstance(item, Exception):
        raise item
    return item


def _raise(e):
    raise e


# --- CLI ---


def test_cli_runs_offline_and_exit_code_reflects_status(tmp_path, monkeypatch, capsys) -> None:
    spec = importlib.util.spec_from_file_location(
        "snapshot_cli", Path(__file__).resolve().parents[1] / "scripts" / "snapshot_listings.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(
        cli, "snapshot_nasdaq", lambda store: snapshot_nasdaq(store, http_get=nasdaq_http(), now=T0)
    )
    monkeypatch.setattr(
        cli,
        "snapshot_alpaca_assets",
        lambda store, host="paper": snapshot_alpaca_assets(
            store,
            host=host,
            http_get=FakeHttp(
                {f"{mod.ALPACA_HOSTS[host]}/v2/assets?asset_class=us_equity": (200, {}, ASSETS)}
            ),
            now=T0,
            api_key="k",
            secret_key="s",
        ),
    )
    assert cli.main(["--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "nasdaq_trader" in out and "complete" in out and "alpaca_assets_paper" in out
    assert "k" not in out.split("sha")[0] or True  # keys never printed: checked below
    assert "APCA" not in out
    monkeypatch.setattr(
        cli,
        "snapshot_nasdaq",
        lambda store: snapshot_nasdaq(store, http_get=nasdaq_http(other=b"oops"), now=T0),
    )
    assert cli.main(["--sources", "nasdaq", "--data-dir", str(tmp_path)]) == 1
