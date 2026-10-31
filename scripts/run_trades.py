"""Simulate daily_stop_entry trades per ticker on stored data (FUNCTION TEST, D-012). Examples:

uv run python scripts/run_trades.py --source alpaca --splits-from yahoo --tickers AAPL MSFT
uv run python scripts/run_trades.py --source yahoo --tickers AAPL --equity-sek 200000 --risk 0.01 \\
    --slippage 20 --commission fixed --fx-mode usd_balance

Pilot data has no point-in-time universe: the output is a function test of the pipeline,
never evidence of edge. Results are written to data/trades/<source>/<run>.{trades,rejections}
.parquet plus a summary JSON; nothing is committed to git.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from tradingbot.backtesting.trades import (
    ExitParams,
    rejections_frame,
    run_ticker,
    summarize,
    trades_frame,
)
from tradingbot.data.store import ParquetStore
from tradingbot.risk.costs import IBKR_FIXED, IBKR_TIERED, CostParams
from tradingbot.risk.sizing import RiskApprover, RiskParams, account_from_sek
from tradingbot.scanner.features import attach_splits
from tradingbot.scanner.pipeline import build_candidates, read_candidates
from tradingbot.signals.daily_trigger import TriggerParams
from tradingbot.signals.state import SetupRegistry


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--splits-from", default=None)
    ap.add_argument("--equity-sek", type=float, default=10_000.0)  # D-008; also 50k/200k
    ap.add_argument("--risk", type=float, default=0.005)  # test 0.005 / 0.01
    ap.add_argument("--slippage", type=float, default=10.0, help="bps scenario: 5/10/20")
    ap.add_argument("--commission", choices=["tiered", "fixed"], default="tiered")
    ap.add_argument("--fx-mode", choices=["usd_balance", "per_trade"], default="per_trade")
    ap.add_argument("--max-cost-to-risk", type=float, default=0.25)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    store = ParquetStore(args.data_dir)
    costs = CostParams(
        commission=IBKR_TIERED if args.commission == "tiered" else IBKR_FIXED,
        fx_mode=args.fx_mode,
    )
    risk = RiskParams(risk_per_trade=args.risk, max_cost_to_risk=args.max_cost_to_risk)
    approver = RiskApprover(account_from_sek(args.equity_sek, costs), risk, costs)
    trigger = TriggerParams(slippage_bps=args.slippage)
    registry = SetupRegistry()
    trades, rejections = [], []
    for ticker in args.tickers:
        series = store.read_processed(args.source, ticker)
        if args.splits_from:
            series = attach_splits(series, store.read_processed(args.splits_from, ticker))
        try:
            cands = read_candidates(store, args.source, ticker)
        except FileNotFoundError:
            build_candidates(store, args.source, ticker)
            cands = read_candidates(store, args.source, ticker)
        t, r, _ = run_ticker(
            series,
            cands,
            approver,
            costs=costs,
            trigger=trigger,
            exits=ExitParams(),
            registry=registry,
        )
        trades += t
        rejections += r
        print(f"{ticker:8s} trades={len(t):3d} rejected={len(r):4d}")

    summary = summarize(trades, rejections)
    summary["params"] = {
        **approver.params_dict(),
        "trigger": vars(trigger),
        "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
    }
    summary["note"] = "function test on pilot data (D-012); not evidence of edge"
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.data_dir / "trades" / args.source
    out.mkdir(parents=True, exist_ok=True)
    if trades:
        trades_frame(trades).to_parquet(out / f"{run}.trades.parquet", index=False)
    if rejections:
        rejections_frame(rejections).to_parquet(out / f"{run}.rejections.parquet", index=False)
    (out / f"{run}.summary.json").write_text(
        json.dumps(summary, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "realistic",
                    "counterfactual_reduced",
                    "open_at_end",
                    "ambiguous_same_day",
                    "partial_skipped",
                    "rejections",
                )
            },
            indent=2,
            default=str,
        )
    )
    if trades:
        cols = [
            "ticker",
            "entry_date",
            "shares",
            "entry_fill",
            "initial_stop",
            "exit_date",
            "exit_reason",
            "r_multiple",
            "pnl_net_usd",
            "partial_skipped",
            "shares_reduced_at_fill",
        ]
        with pd.option_context("display.width", 200):
            print(trades_frame(trades)[cols].to_string(index=False))


if __name__ == "__main__":
    main()
