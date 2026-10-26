"""Parquet storage layout under data/ (git-ignored).

data/raw/<source>/<ticker>/<start>_<end>_<fetched_at>.parquet   one file per fetch, never
                                                                 overwritten (append-only)
data/processed/daily/<source>/<ticker>.parquet     normalized series, merged across fetches
data/processed/daily/<source>/<ticker>.meta.json   FetchMeta: coverage, quality, fetch history

Write policy for processed series: UPSERT by date. Rows from the new fetch replace stored
rows on the same dates; stored rows outside the fetched range are kept. A short fetch can
therefore never shrink a longer stored history. ``mode="replace"`` discards the stored
series explicitly. Merging refuses if source or price_basis differ from the stored series.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from tradingbot.data.schema import FetchMeta


def _safe(ticker: str) -> str:
    return ticker.replace("/", "_").replace("^", "idx_").upper()


class ParquetStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def raw_dir(self, source: str, ticker: str) -> Path:
        return self.root / "raw" / source / _safe(ticker)

    def raw_path(self, source: str, ticker: str) -> Path:
        """Most recent raw fetch file (by name, i.e. by requested range then fetch time)."""
        files = sorted(self.raw_dir(source, ticker).glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"no raw fetch stored for {source}/{ticker}")
        return max(files, key=lambda f: f.stat().st_mtime)

    def processed_path(self, source: str, ticker: str) -> Path:
        return self.root / "processed" / "daily" / source / f"{_safe(ticker)}.parquet"

    def meta_path(self, source: str, ticker: str) -> Path:
        return self.processed_path(source, ticker).with_suffix(".meta.json")

    def write_raw(self, meta: FetchMeta, frame: pd.DataFrame) -> Path:
        """Append-only: a new file per fetch, named by requested range and fetch time."""
        d = self.raw_dir(meta.source, meta.ticker)
        d.mkdir(parents=True, exist_ok=True)
        stamp = meta.fetched_at.strftime("%Y%m%dT%H%M%SZ")
        p = d / f"{meta.requested_start}_{meta.requested_end}_{stamp}.parquet"
        frame.to_parquet(p, index=False)
        return p

    def merge_processed(self, meta: FetchMeta, new: pd.DataFrame) -> pd.DataFrame:
        """Return the series to store: upsert of ``new`` into the stored series, or ``new``."""
        p = self.processed_path(meta.source, meta.ticker)
        if meta.write_mode == "replace" or not p.exists():
            return new.reset_index(drop=True)
        try:
            old = self.read_processed(meta.source, meta.ticker)
        except ValueError as e:
            raise ValueError(f"{e}; or ingest with write_mode='replace'") from e
        if old.empty:
            return new.reset_index(drop=True)
        if new.empty:
            return old.reset_index(drop=True)
        for col in ("source", "price_basis"):
            o, n = str(old[col].iloc[0]), str(new[col].iloc[0])
            if o != n:
                raise ValueError(
                    f"{meta.source}/{meta.ticker}: stored {col}={o!r} but fetch has {n!r}; "
                    "use write_mode='replace' to discard the stored series"
                )
        new_dates = set(new["date"])
        kept = old[~old["date"].isin(new_dates)]
        merged = pd.concat([kept, new], ignore_index=True).sort_values("date")
        return merged.reset_index(drop=True)

    def write_processed(self, meta: FetchMeta, frame: pd.DataFrame) -> Path:
        """Write the (already merged) series and its metadata."""
        p = self.processed_path(meta.source, meta.ticker)
        p.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(p, index=False)
        self.meta_path(meta.source, meta.ticker).write_text(
            json.dumps(meta.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def read_processed(self, source: str, ticker: str) -> pd.DataFrame:
        df = pd.read_parquet(self.processed_path(source, ticker))
        if "price_basis" not in df.columns:
            raise ValueError(
                f"{source}/{ticker}: stored with schema v1 (no price_basis); re-run ingest"
            )
        return df

    def list_tickers(self, source: str) -> list[str]:
        d = self.root / "processed" / "daily" / source
        return sorted(p.stem for p in d.glob("*.parquet")) if d.exists() else []

    def list_sources(self) -> list[str]:
        d = self.root / "processed" / "daily"
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []

    def read_meta(self, source: str, ticker: str) -> dict:
        return json.loads(self.meta_path(source, ticker).read_text(encoding="utf-8"))
