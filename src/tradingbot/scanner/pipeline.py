"""Build and store the feature table for one ticker from the processed data store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from tradingbot.data.store import ParquetStore
from tradingbot.scanner.features import (
    FEATURE_VERSION,
    SPEC_VERSION,
    FeatureParams,
    attach_splits,
    compute_features,
    params_dict,
)


def features_path(store: ParquetStore, source: str, ticker: str) -> Path:
    return store.root / "features" / source / f"{ticker.upper()}.parquet"


def build_features(
    store: ParquetStore,
    source: str,
    ticker: str,
    *,
    params: FeatureParams | None = None,
    splits_from: str | None = None,
    built_at: datetime | None = None,
) -> dict:
    """Compute features for a stored series and write Parquet + .meta.json. Returns the meta.

    ``splits_from`` names another stored source whose split_ratio is attached first (for
    as_traded series without split data, e.g. Alpaca raw with Yahoo's split list).
    """
    p = params or FeatureParams()
    df = store.read_processed(source, ticker)
    input_meta = store.read_meta(source, ticker)
    notes = []
    if splits_from:
        splits_df = store.read_processed(splits_from, ticker)
        df = attach_splits(df, splits_df)
        notes.append(f"split_ratio attached from {splits_from}")
    feats = compute_features(df, p)
    complete = feats.dropna(subset=["sma50", "adr20", "prior_move_pct"])
    meta = {
        "source": source,
        "ticker": ticker.upper(),
        "input_price_basis": str(df["price_basis"].iloc[0]) if len(df) else None,
        "input_rows": int(len(df)),
        "input_coverage": [input_meta.get("actual_start"), input_meta.get("actual_end")],
        "input_quality_status": input_meta.get("quality_status"),
        "input_fetched_at": input_meta.get("fetched_at"),
        "splits_from": splits_from,
        "feature_rows": int(len(feats)),
        "first_complete_date": str(complete["date"].iloc[0]) if len(complete) else None,
        "last_date": str(feats["date"].iloc[-1]) if len(feats) else None,
        "params": params_dict(p),
        "spec_version": SPEC_VERSION,
        "feature_version": FEATURE_VERSION,
        "built_at": (built_at or datetime.now(UTC)).isoformat(),
        "notes": "; ".join(notes),
        "disclaimer": (
            "Technical function test on a small, surviving-ticker sample; not a point-in-time "
            "universe and not evidence of strategy profitability (DATA_PILOT.md §6)."
        ),
    }
    out = features_path(store, source, ticker)
    out.parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(out, index=False)
    out.with_suffix(".meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def read_features(store: ParquetStore, source: str, ticker: str) -> pd.DataFrame:
    return pd.read_parquet(features_path(store, source, ticker))
