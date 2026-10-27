"""Print stored raw and normalized OHLCV from several sources around one date. Example:

uv run python scripts/inspect_day.py --ticker MSFT --date 2020-03-18 --sources yahoo alpaca

Used to examine a cross-source discrepancy without assuming which source is right.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from tradingbot.data.store import ParquetStore

NORM_COLS = ["date", "open", "high", "low", "close", "volume", "split_ratio", "price_basis"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--date", type=date.fromisoformat, required=True)
    ap.add_argument("--sources", nargs="+", default=["yahoo", "alpaca"])
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    store = ParquetStore(args.data_dir)
    lo, hi = args.date - timedelta(days=args.days * 2), args.date + timedelta(days=args.days * 2)
    pd.set_option("display.width", 200)
    for src in args.sources:
        norm = store.read_processed(src, args.ticker)
        win = norm[(norm["date"] >= lo) & (norm["date"] <= hi)]
        print(f"=== {src} normalized ({win['price_basis'].iloc[0] if len(win) else '-'}) ===")
        print(win[NORM_COLS].to_string(index=False))
        files = sorted(
            store.raw_dir(src, args.ticker).glob("*.parquet"), key=lambda f: f.stat().st_mtime
        )
        raw = pd.concat([pd.read_parquet(f).assign(_file=f.name) for f in files], ignore_index=True)
        tcol = next((c for c in ("Date", "t") if c in raw.columns), None)
        if tcol:
            ts = pd.to_datetime(raw[tcol], utc=True).dt.tz_convert("America/New_York").dt.date
            rwin = raw[(ts >= lo) & (ts <= hi)]
            print(f"--- {src} raw ---")
            print(rwin.to_string(index=False))
        print()


if __name__ == "__main__":
    main()
