"""Fetch Alpaca SIP 1Min bars for candidate days only, append-only. Examples:

uv run python scripts/fetch_minute.py --tickers AAPL MSFT                 # all candidate days
uv run python scripts/fetch_minute.py --tickers AAPL --dates 2020-08-28 2020-08-31
uv run python scripts/fetch_minute.py --tickers AAPL --refetch     # new file per day, diff logged

Candidate days come from data/candidates/alpaca/<TICKER>.parquet (is_candidate rows). Each
fetch is written as data/intraday/alpaca/<TICKER>/<date>_<fetched_at>.parquet + .json
(SessionReport). A day that already has a fetch is skipped unless --refetch, in which case
the new fetch is compared with the latest one and revisions are printed (late corrections).
Keys from .env; never printed. Rate-limit headers of the last response are shown at the end.
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from tradingbot.config import load_dotenv
from tradingbot.data.intraday import (
    IntradayStore,
    compare_fetches,
    is_session,
    normalize_minute,
    session_report,
)
from tradingbot.data.providers.alpaca import AlpacaError, AlpacaProvider
from tradingbot.data.store import ParquetStore
from tradingbot.scanner.pipeline import read_candidates


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--dates", nargs="*", type=date.fromisoformat, default=None)
    ap.add_argument("--source", default="alpaca", help="candidate table source")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()
    load_dotenv()

    provider = AlpacaProvider(asof=args.asof)
    store = ParquetStore(args.data_dir)
    istore = IntradayStore(args.data_dir)
    for ticker in args.tickers:
        if args.dates:
            days = list(args.dates)
        else:
            cands = read_candidates(store, args.source, ticker)
            days = sorted(
                pd.Timestamp(d).date()
                for d in cands.loc[cands["is_candidate"].astype(bool), "date"]
            )
        for d in days:
            if not is_session(d):
                print(f"{ticker:8s} {d} not a session, skipped")
                continue
            previous = istore.fetches(ticker, d)
            if previous and not args.refetch:
                continue
            try:
                raw = provider.fetch_minute(ticker, d)
            except AlpacaError as e:
                print(f"{ticker:8s} {d} ERROR {e}")
                continue
            fetched_at = datetime.now(UTC)
            frame = normalize_minute(raw.frame, d)
            pages = int(raw.notes.split("pages=")[-1]) if "pages=" in raw.notes else 1
            rep = session_report(
                ticker, d, frame, pages=pages, fetched_at=fetched_at, note=raw.notes
            )
            istore.write(rep, frame)
            flag = " SPARSE" if rep.sparse_session else ""
            print(
                f"{ticker:8s} {d} raw={rep.n_raw:4d} session={rep.n_session:3d}/{rep.expected} "
                f"first={rep.first_ts} pages={rep.pages}{flag}"
            )
            if previous:
                diff = compare_fetches(pd.read_parquet(previous[-1]), frame)
                if diff["revised"]:
                    print(
                        f"         REVISED vs {previous[-1].name}: changed={len(diff['changed'])} "
                        f"only_old={len(diff['only_in_old'])} only_new={len(diff['only_in_new'])}"
                    )
    if provider.last_rate_limit:
        print("rate limit:", provider.last_rate_limit)


if __name__ == "__main__":
    main()
