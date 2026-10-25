"""Parquet storage layout under data/ (git-ignored).

data/raw/<source>/<ticker>.parquet                 raw provider frame, unchanged
data/processed/daily/<source>/<ticker>.parquet     normalized frame
data/processed/daily/<source>/<ticker>.meta.json   FetchMeta incl. quality status
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

    def raw_path(self, source: str, ticker: str) -> Path:
        return self.root / "raw" / source / f"{_safe(ticker)}.parquet"

    def processed_path(self, source: str, ticker: str) -> Path:
        return self.root / "processed" / "daily" / source / f"{_safe(ticker)}.parquet"

    def meta_path(self, source: str, ticker: str) -> Path:
        return self.processed_path(source, ticker).with_suffix(".meta.json")

    def write_raw(self, source: str, ticker: str, frame: pd.DataFrame) -> Path:
        p = self.raw_path(source, ticker)
        p.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(p, index=False)
        return p

    def write_processed(self, meta: FetchMeta, frame: pd.DataFrame) -> Path:
        p = self.processed_path(meta.source, meta.ticker)
        p.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(p, index=False)
        self.meta_path(meta.source, meta.ticker).write_text(
            json.dumps(meta.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def read_processed(self, source: str, ticker: str) -> pd.DataFrame:
        return pd.read_parquet(self.processed_path(source, ticker))

    def read_meta(self, source: str, ticker: str) -> dict:
        return json.loads(self.meta_path(source, ticker).read_text(encoding="utf-8"))
