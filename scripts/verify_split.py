"""Print raw and reconstructed as-traded closes around a known split to confirm the
adjustment assumptions in providers/yahoo.py. Example (AAPL 4:1 on 2020-08-31):

uv run python scripts/verify_split.py --ticker AAPL --split-date 2020-08-31

Expected: Yahoo raw Close is ~125 on both sides of the split (split-adjusted), while the
reconstructed as-traded close is ~500 before and ~129 after.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from tradingbot.data.pipeline import get_provider


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="yahoo")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--split-date", type=date.fromisoformat, required=True)
    ap.add_argument("--days", type=int, default=3)
    args = ap.parse_args()

    provider = get_provider(args.source)
    start = args.split_date - timedelta(days=args.days * 2)
    end = args.split_date + timedelta(days=args.days * 2)
    raw = provider.fetch_daily(args.ticker, start, end)
    norm = provider.normalize(raw)
    print("--- raw (provider) ---")
    print(raw.frame[["Date", "Close", "Adj Close", "Volume", "Stock Splits"]].to_string())
    print("--- normalized (as-traded) ---")
    print(norm[["date", "close", "volume", "adj_close", "adj_factor", "split_ratio"]].to_string())


if __name__ == "__main__":
    main()
