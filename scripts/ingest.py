"""Fetch daily bars into data/. Example:

uv run python scripts/ingest.py --source yahoo --tickers AAPL MSFT --start 2015-01-01
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradingbot.data.pipeline import PROVIDERS, get_provider, ingest
from tradingbot.data.store import ParquetStore


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=sorted(PROVIDERS), default="yahoo")
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    provider = get_provider(args.source)
    store = ParquetStore(args.data_dir)
    for ticker in args.tickers:
        try:
            meta = ingest(provider, store, ticker, args.start, args.end)
        except Exception as e:  # noqa: BLE001 - report and continue with next ticker
            print(f"{ticker:8s} ERROR {type(e).__name__}: {e}")
            continue
        issues = "; ".join(meta.quality_issues) or "-"
        print(
            f"{ticker:8s} {meta.quality_status:4s} rows={meta.rows:5d} "
            f"{meta.actual_start} .. {meta.actual_end}  {issues}"
        )


if __name__ == "__main__":
    main()
