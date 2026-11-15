"""Compute scanner features (spec §2–3) for stored series. Examples:

uv run python scripts/build_features.py --source yahoo --tickers AAPL MSFT NVDA
uv run python scripts/build_features.py --source alpaca --tickers AAPL --splits-from yahoo
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tradingbot.data.store import ParquetStore
from tradingbot.data.universe_model import UniverseModel
from tradingbot.scanner.pipeline import build_features


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--splits-from", default=None, help="source to copy split_ratio from")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument(
        "--universe",
        default=None,
        help="universe version under data/universe/ to identify each series against (D-025); "
        "default: pilot compatibility path (symbol = id, unverified)",
    )
    args = ap.parse_args()

    store = ParquetStore(args.data_dir)
    model = UniverseModel.read(args.data_dir, args.universe) if args.universe else None
    for ticker in args.tickers:
        try:
            m = build_features(
                store, args.source, ticker, splits_from=args.splits_from, universe_model=model
            )
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"{ticker:8s} ERROR {type(e).__name__}: {e}")
            continue
        print(
            f"{ticker:8s} rows={m['feature_rows']:5d} complete from {m['first_complete_date']} "
            f"to {m['last_date']} basis={m['input_price_basis']} "
            f"id={m['identity']['instrument_id']} ({m['identity']['method']}) {m['notes'] or ''}"
        )


if __name__ == "__main__":
    main()
