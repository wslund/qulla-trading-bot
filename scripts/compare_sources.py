"""Compare already-ingested tickers between two sources. Example:

uv run python scripts/compare_sources.py --a yahoo --b stooq --tickers AAPL MSFT
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tradingbot.data.compare import compare_series
from tradingbot.data.store import ParquetStore


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--column", default="close")
    ap.add_argument("--tol", type=float, default=0.005)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    store = ParquetStore(args.data_dir)
    for ticker in args.tickers:
        rep = compare_series(
            store.read_processed(args.a, ticker),
            store.read_processed(args.b, ticker),
            column=args.column,
            tol=args.tol,
        )
        print(rep.summary())
        if rep.dates_over_tol:
            print(f"         first dates over tol: {rep.dates_over_tol}")


if __name__ == "__main__":
    main()
