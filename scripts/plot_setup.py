"""Export static setup charts for candidates / near misses. Examples:

uv run python scripts/plot_setup.py --source yahoo --ticker NVDA --dates 2016-08-31 2017-06-27
uv run python scripts/plot_setup.py --source yahoo --ticker NVDA --start 2016-01-01 --near-misses

Requires stored candidates (scripts/scan.py) and the viz extra (uv sync --extra viz).
Images go to data/plots/<source>/<TICKER>_<date>_<candidate|miss>.png (git-ignored).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from tradingbot.data.store import ParquetStore
from tradingbot.scanner.pipeline import read_candidates
from tradingbot.scanner.plot import plot_setup, select_rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--dates", nargs="*", type=date.fromisoformat)
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--near-misses", action="store_true")
    ap.add_argument("--lookback", type=int, default=120)
    ap.add_argument("--lookahead", type=int, default=20)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    store = ParquetStore(args.data_dir)
    series = store.read_processed(args.source, args.ticker)
    cands = read_candidates(store, args.source, args.ticker)
    rows = select_rows(
        cands, dates=args.dates, start=args.start, end=args.end, near_misses=args.near_misses
    )
    if rows.empty:
        print("no matching rows")
        return
    out_dir = args.out_dir or (args.data_dir / "plots" / args.source)
    for _, row in rows.iterrows():
        kind = "candidate" if bool(row["is_candidate"]) else "miss"
        path = out_dir / f"{args.ticker.upper()}_{row['date']}_{kind}.png"
        info = plot_setup(series, row, path, lookback=args.lookback, lookahead=args.lookahead)
        print(
            f"{info.path}  {info.rules_passed}/{info.rules_total}  consol "
            f"{info.consol_start}..{info.consol_end}  peak {info.peak_date}  "
            f"breakout {info.breakout_level:.2f}"
            if info.breakout_level
            else f"{info.path}"
        )


if __name__ == "__main__":
    main()
