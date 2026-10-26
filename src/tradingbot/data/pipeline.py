"""Ingest pipeline: fetch -> store raw -> normalize -> quality -> store processed + meta."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tradingbot.data.providers.base import MarketDataProvider, RawBars
from tradingbot.data.quality import check_quality
from tradingbot.data.schema import FetchMeta, empty_normalized
from tradingbot.data.store import ParquetStore

PROVIDERS: dict[str, str] = {
    "yahoo": "tradingbot.data.providers.yahoo:YahooProvider",
    "stooq": "tradingbot.data.providers.stooq:StooqProvider",
    "alpaca": "tradingbot.data.providers.alpaca:AlpacaProvider",
}


def get_provider(name: str) -> MarketDataProvider:
    try:
        module_name, cls_name = PROVIDERS[name].split(":")
    except KeyError as e:
        raise ValueError(f"unknown provider {name!r}; known: {sorted(PROVIDERS)}") from e
    module = __import__(module_name, fromlist=[cls_name])
    return getattr(module, cls_name)()


def process(
    provider: MarketDataProvider,
    raw: RawBars,
    store: ParquetStore,
    start: date,
    end: date,
    *,
    fetched_at: datetime | None = None,
) -> FetchMeta:
    """Everything after the network call. Pure given ``raw``; used by tests with fixtures."""
    meta = FetchMeta(
        source=provider.name,
        ticker=raw.ticker,
        requested_start=start,
        requested_end=end,
        fetched_at=fetched_at or datetime.now(UTC),
        provider_version=raw.provider_version,
        adjustment_method=provider.adjustment_method,
        notes=raw.notes,
    )
    store.write_raw(provider.name, raw.ticker, raw.frame)
    if raw.frame.empty:
        meta.quality_status = "FAIL"
        meta.quality_issues = ["FAIL: provider returned no rows (unknown or delisted ticker?)"]
        store.write_processed(meta, empty_normalized())
        return meta

    norm = provider.normalize(raw)
    report = check_quality(norm)
    meta.rows = len(norm)
    meta.actual_start = norm["date"].iloc[0]
    meta.actual_end = norm["date"].iloc[-1]
    meta.quality_status = report.status
    meta.quality_issues = report.issues
    store.write_processed(meta, norm)
    return meta


def ingest(
    provider: MarketDataProvider, store: ParquetStore, ticker: str, start: date, end: date
) -> FetchMeta:
    raw = provider.fetch_daily(ticker, start, end)
    return process(provider, raw, store, start, end)
