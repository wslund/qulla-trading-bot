"""Alpaca adapter with a fake HTTP layer: pagination, rate limits, errors, normalization."""

import io
import json
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from tradingbot.data.compare import compare_series, rebase
from tradingbot.data.providers import alpaca as mod
from tradingbot.data.providers.alpaca import AlpacaError, AlpacaProvider
from tradingbot.data.quality import WARN, check_quality
from tradingbot.data.schema import AS_TRADED, NORMALIZED_COLUMNS, SPLIT_ADJUSTED

FIXTURES = Path(__file__).parent / "fixtures"
PAGE1 = json.loads((FIXTURES / "alpaca_bars_page1.json").read_text())
PAGE2 = json.loads((FIXTURES / "alpaca_bars_page2.json").read_text())


class FakeHTTP:
    """Scripted responses: each item is a JSON-able dict or an (status, body) error."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list[dict] = []

    def __call__(self, req, timeout=0):
        url = urllib.parse.urlparse(req.full_url)
        self.requests.append(
            {
                "path": url.path,
                "query": dict(urllib.parse.parse_qsl(url.query)),
                "headers": dict(req.header_items()),
            }
        )
        item = self.script.pop(0)
        if isinstance(item, tuple):
            status, body = item
            headers = {"Retry-After": "0"} if status == 429 else {}
            raise urllib.error.HTTPError(req.full_url, status, "err", headers, io.BytesIO(body))
        return _Resp(json.dumps(item).encode())


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def provider() -> AlpacaProvider:
    return AlpacaProvider("key-id", "secret", sleep=lambda s: None)


def _patch(monkeypatch: pytest.MonkeyPatch, script: list) -> FakeHTTP:
    fake = FakeHTTP(script)
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    return fake


def test_pagination_and_request_shape(monkeypatch, provider: AlpacaProvider) -> None:
    fake = _patch(monkeypatch, [PAGE1, PAGE2])
    raw = provider.fetch_daily("syn", date(2024, 3, 4), date(2024, 3, 19))
    assert len(raw.frame) == 12
    assert len(fake.requests) == 2
    q0 = fake.requests[0]["query"]
    assert fake.requests[0]["path"] == "/v2/stocks/SYN/bars"
    assert q0["timeframe"] == "1Day" and q0["feed"] == "sip" and q0["adjustment"] == "raw"
    assert q0["start"] == "2024-03-04" and q0["end"] == "2024-03-19" and q0["limit"] == "10000"
    assert "page_token" not in q0
    assert fake.requests[1]["query"]["page_token"] == "TOKEN2"
    h = fake.requests[0]["headers"]
    assert h["Apca-api-key-id"] == "key-id" and h["Apca-api-secret-key"] == "secret"
    assert "key" not in raw.provider_version and "secret" not in raw.provider_version


def test_normalize_as_traded_with_unknown_adjustments(monkeypatch, provider) -> None:
    _patch(monkeypatch, [PAGE1, PAGE2])
    raw = provider.fetch_daily("SYN", date(2024, 3, 4), date(2024, 3, 19))
    df = provider.normalize(raw)
    assert list(df.columns) == NORMALIZED_COLUMNS
    assert (df["price_basis"] == AS_TRADED).all()
    assert df["date"].iloc[0] == date(2024, 3, 4)  # 05:00Z -> 2024-03-04 New York
    assert df["date"].iloc[6] == date(2024, 3, 12)  # 04:00Z after DST switch
    assert df[["adj_close", "adj_factor", "split_ratio", "dividend"]].isna().all().all()
    # Raw bars carry no split info, so the 2:1 split day is an "unexplained" jump: WARN.
    rep = check_quality(df)
    assert rep.status == WARN
    assert any("unexplained close jumps" in i and "2024-03-12" in i for i in rep.issues)


def test_alpaca_matches_yahoo_as_traded(monkeypatch, provider, normalized) -> None:
    """Same synthetic history: Yahoo (as-traded reconstructed) vs Alpaca raw must agree."""
    _patch(monkeypatch, [PAGE1, PAGE2])
    alp = provider.normalize(provider.fetch_daily("SYN", date(2024, 3, 4), date(2024, 3, 19)))
    rep = compare_series(normalized, alp, basis=AS_TRADED)
    assert rep.basis == AS_TRADED and rep.common_days == 12
    assert rep.close_max_rel_diff < 1e-4
    np.testing.assert_allclose(alp["volume"], normalized["volume"], rtol=1e-6)


def test_alpaca_cannot_be_rebased(monkeypatch, provider) -> None:
    _patch(monkeypatch, [PAGE1, PAGE2])
    alp = provider.normalize(provider.fetch_daily("SYN", date(2024, 3, 4), date(2024, 3, 19)))
    with pytest.raises(ValueError, match="unknown"):
        rebase(alp, SPLIT_ADJUSTED)


def test_rate_limit_retry_then_success(monkeypatch, provider) -> None:
    fake = _patch(monkeypatch, [(429, b"too many requests"), (503, b"unavailable"), PAGE2])
    raw = provider.fetch_daily("SYN", date(2024, 3, 14), date(2024, 3, 19))
    assert len(raw.frame) == 4 and len(fake.requests) == 3


def test_client_error_raises_without_retry(monkeypatch, provider) -> None:
    fake = _patch(monkeypatch, [(401, b'{"message":"unauthorized"}')])
    with pytest.raises(AlpacaError, match="HTTP 401"):
        provider.fetch_daily("SYN", date(2024, 3, 4), date(2024, 3, 19))
    assert len(fake.requests) == 1


def test_retries_exhausted(monkeypatch) -> None:
    p = AlpacaProvider("k", "s", max_retries=2, sleep=lambda s: None)
    _patch(monkeypatch, [(429, b"x")] * 3)
    with pytest.raises(AlpacaError, match="after 2 retries"):
        p.fetch_daily("SYN", date(2024, 3, 4), date(2024, 3, 19))


def test_empty_response(monkeypatch, provider) -> None:
    _patch(monkeypatch, [{"bars": None, "symbol": "GONE", "next_page_token": None}])
    raw = provider.fetch_daily("GONE", date(2020, 1, 1), date(2020, 2, 1))
    assert raw.frame.empty
    assert provider.normalize(raw).empty


def test_missing_keys_raise_before_network(monkeypatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(AlpacaError, match="not set"):
        AlpacaProvider().fetch_daily("SYN", date(2024, 3, 4), date(2024, 3, 19))


def test_dotenv_loader(tmp_path, monkeypatch) -> None:
    from tradingbot.config import load_dotenv

    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.setenv("KEEP", "orig")
    env = tmp_path / ".env"
    env.write_text('# comment\nALPACA_API_KEY="abc"\nKEEP=new\n\nBROKEN\n')
    assert load_dotenv(env) == 1
    import os

    assert os.environ["ALPACA_API_KEY"] == "abc"
    assert os.environ["KEEP"] == "orig"
    assert load_dotenv(tmp_path / "missing") == 0
