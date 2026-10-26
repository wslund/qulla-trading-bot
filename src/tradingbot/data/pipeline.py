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
    write_mode: str = "upsert",
) -> FetchMeta:
    """Everything after the network call. Pure given ``raw``; used by tests with fixtures.

    The processed series is merged into the stored one by date (see store.ParquetStore);
    quality and coverage in the metadata describe the stored series after the merge.
    """
    if write_mode not in ("upsert", "replace"):
        raise ValueError(f"write_mode must be 'upsert' or 'replace', got {write_mode!r}")
    meta = FetchMeta(
        source=provider.name,
        ticker=raw.ticker,
        requested_start=start,
        requested_end=end,
        fetched_at=fetched_at or datetime.now(UTC),
        provider_version=raw.provider_version,
        adjustment_method=provider.adjustment_method,
        notes=raw.notes,
        write_mode=write_mode,
    )
    store.write_raw(meta, raw.frame)
    new = empty_normalized() if raw.frame.empty else provider.normalize(raw)
    meta.fetch_rows = len(new)

    # Carry the fetch history of the stored series forward.
    try:
        meta.fetch_history = list(
            store.read_meta(provider.name, raw.ticker).get("fetch_history", [])
        )
    except FileNotFoundError:
        meta.fetch_history = []
    if write_mode == "replace":
        meta.fetch_history = []
    meta.fetch_history.append(
        f"{start}..{end} fetched {meta.fetched_at.isoformat()} rows={len(new)} mode={write_mode}"
    )

    merged = store.merge_processed(meta, new)
    if merged.empty:
        meta.quality_status = "FAIL"
        meta.quality_issues = ["FAIL: provider returned no rows (unknown or delisted ticker?)"]
        store.write_processed(meta, merged)
        return meta
    if new.empty:
        meta.notes = (
            meta.notes + "; " if meta.notes else ""
        ) + "fetch returned no rows; stored series kept"

    report = check_quality(merged)
    meta.rows = len(merged)
    meta.actual_start = merged["date"].iloc[0]
    meta.actual_end = merged["date"].iloc[-1]
    meta.quality_status = report.status
    meta.quality_issues = report.issues
    store.write_processed(meta, merged)
    return meta


def ingest(
    provider: MarketDataProvider,
    store: ParquetStore,
    ticker: str,
    start: date,
    end: date,
    *,
    write_mode: str = "upsert",
) -> FetchMeta:
    raw = provider.fetch_daily(ticker, start, end)
    return process(provider, raw, store, start, end, write_mode=write_mode)
