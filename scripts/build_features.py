"""Compute scanner features (spec §2–3) for stored series. Examples:

uv run python scripts/build_features.py --source yahoo --tickers AAPL MSFT NVDA
uv run python scripts/build_features.py --source alpaca --tickers AAPL --splits-from yahoo
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tradingbot.data.store import ParquetStore
from tradingbot.scanner.pipeline import build_features


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--splits-from", default=None, help="source to copy split_ratio from")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    store = ParquetStore(args.data_dir)
    for ticker in args.tickers:
        try:
            m = build_features(store, args.source, ticker, splits_from=args.splits_from)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"{ticker:8s} ERROR {type(e).__name__}: {e}")
            continue
        print(
            f"{ticker:8s} rows={m['feature_rows']:5d} complete from {m['first_complete_date']} "
            f"to {m['last_date']} basis={m['input_price_basis']} {m['notes'] or ''}"
        )


if __name__ == "__main__":
    main()
