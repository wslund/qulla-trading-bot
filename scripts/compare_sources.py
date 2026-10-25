"""Compare already-ingested tickers between two sources on one price basis. Example:

uv run python scripts/compare_sources.py --a yahoo --b stooq --tickers AAPL MSFT NVDA --infer

--infer compares under both split_adjusted and total_return so the basis of a source
without split/dividend data (Stooq) can be read off: the basis with the smaller mean
difference is the one it uses. Without --infer, the basis defaults to source b's.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tradingbot.data.compare import compare_series
from tradingbot.data.schema import PRICE_BASES, SPLIT_ADJUSTED, TOTAL_RETURN
from tradingbot.data.store import ParquetStore


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--column", default="close")
    ap.add_argument("--tol", type=float, default=0.005)
    ap.add_argument("--basis", choices=PRICE_BASES, default=None)
    ap.add_argument("--infer", action="store_true")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    store = ParquetStore(args.data_dir)
    bases = [SPLIT_ADJUSTED, TOTAL_RETURN] if args.infer else [args.basis]
    for ticker in args.tickers:
        a = store.read_processed(args.a, ticker)
        b = store.read_processed(args.b, ticker)
        for basis in bases:
            try:
                rep = compare_series(
                    a, b, column=args.column, tol=args.tol, basis=basis, assume_b_basis=args.infer
                )
            except ValueError as e:
                print(f"{ticker} [{basis}]: {e}")
                continue
            print(rep.summary())
            if rep.dates_over_tol:
                print(f"         first dates over tol: {rep.dates_over_tol}")


if __name__ == "__main__":
    main()
