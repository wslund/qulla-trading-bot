"""List rule-based candidates on one date or over a period. Examples:

uv run python scripts/scan.py --source yahoo --tickers AAPL MSFT --date 2020-08-31
uv run python scripts/scan.py --source yahoo --tickers AAPL --start 2020-01-01 --end 2020-12-31
uv run python scripts/scan.py --source alpaca --tickers AAPL --splits-from yahoo --near-misses

Features are built if missing (or with --rebuild). Candidates are written to
data/candidates/<source>/<TICKER>.parquet with metadata. Thresholds come from the spec
(scanner.rules.Thresholds); override individual ones with --set name=value.
"""

from __future__ import annotations

import argparse
from dataclasses import fields, replace
from datetime import date
from pathlib import Path

import pandas as pd

from tradingbot.data.store import ParquetStore
from tradingbot.scanner.pipeline import (
    build_candidates,
    build_features,
    features_path,
    read_candidates,
)
from tradingbot.scanner.rules import RULE_NAMES, Thresholds

SHOW = [
    "date",
    "ticker",
    "close_prev",
    "prior_move_pct",
    "consol_days",
    "consol_tightness",
    "consol_depth",
    "consol_pct_below_high",
    "adr20",
    "rules_passed",
    "rules_total",
]


def parse_thresholds(items: list[str]) -> Thresholds:
    th = Thresholds()
    types = {f.name: f for f in fields(Thresholds)}
    for item in items or []:
        name, _, value = item.partition("=")
        if name not in types:
            raise SystemExit(f"unknown threshold {name!r}; known: {sorted(types)}")
        if value.lower() in ("none", "off"):
            parsed = None
        elif value.lower() in ("true", "false"):
            parsed = value.lower() == "true"
        elif "." in value or "e" in value.lower():
            parsed = float(value)
        else:
            parsed = int(value)
        th = replace(th, **{name: parsed})
    return th


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--splits-from", default=None)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--near-misses", action="store_true", help="also list rows failing one rule")
    ap.add_argument("--set", nargs="*", default=[], metavar="NAME=VALUE")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()
    if args.date and (args.start or args.end):
        ap.error("use --date or --start/--end, not both")
    lo = args.date or args.start or date(1900, 1, 1)
    hi = args.date or args.end or date(2100, 1, 1)
    th = parse_thresholds(args.set)
    store = ParquetStore(args.data_dir)
    pd.set_option("display.width", 220)

    frames = []
    for ticker in args.tickers:
        try:
            if args.rebuild or not features_path(store, args.source, ticker).exists():
                build_features(store, args.source, ticker, splits_from=args.splits_from)
            m = build_candidates(store, args.source, ticker, thresholds=th)
            df = read_candidates(store, args.source, ticker)
        except Exception as e:  # noqa: BLE001
            print(f"{ticker:8s} ERROR {type(e).__name__}: {e}")
            continue
        sel = df[(df["date"] >= lo) & (df["date"] <= hi)]
        print(
            f"{ticker:8s} rows={len(sel):5d} candidates={int(sel['is_candidate'].sum()):4d} "
            f"(all-time {m['candidates']}/{m['rows']}) basis={m['input_price_basis']} "
            f"quality={m['input_quality_status']}"
        )
        frames.append(sel)
    if not frames:
        return
    allrows = pd.concat(frames, ignore_index=True)
    cands = allrows[allrows["is_candidate"]]
    groups = cands["setup_group_id"].nunique() if "setup_group_id" in cands else "?"
    print(f"\n=== candidates {lo}..{hi}: {len(cands)} rows in {groups} setup groups ===")
    if len(cands):
        print(cands[SHOW].to_string(index=False))
    if args.near_misses:
        near = allrows[
            (~allrows["is_candidate"]) & (allrows["rules_passed"] >= allrows["rules_total"] - 1)
        ]
        print(f"\n=== near misses (one rule failed): {len(near)} ===")
        for _, r in near.iterrows():
            failed = [n for n in RULE_NAMES if r[f"rule_{n}"] is False]
            print(
                f"{r['date']} {r['ticker']:6s} failed={failed} missing={r['missing_rules'] or '-'}"
            )


if __name__ == "__main__":
    main()
