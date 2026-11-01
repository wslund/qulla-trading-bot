"""Portfolio backtest of daily_stop_entry on stored data (FUNCTION TEST, D-012). Examples:

uv run python scripts/run_portfolio.py --source alpaca --splits-from yahoo --tickers AAPL MSFT NVDA
uv run python scripts/run_portfolio.py --source alpaca --splits-from yahoo --tickers AAPL MSFT \\
    --entry-model orb                      # minute bars from scripts/fetch_minute.py
uv run python scripts/run_portfolio.py --source alpaca --splits-from yahoo --tickers AAPL MSFT \\
    --equity-sek 200000 --risk 0.01 --slippage 20 --commission fixed --fx-mode usd_balance \\
    --max-open-positions 6 --priority seeded_random --seed 7

One account, one XNYS clock, all tickers (backtesting.portfolio.run_portfolio). Output under
data/portfolio/<source>/<run_id>/: trades, rejections, reservations, decisions, ledger,
events, equity_curve (parquet), summary.json and config.json (parameters, args, commit,
config hash) so a run can be repeated. Nothing is committed to git. Pilot data has no
point-in-time universe: the report is a function test, never evidence of edge.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradingbot.backtesting.portfolio import PortfolioParams, run_portfolio
from tradingbot.backtesting.priority import PriorityParams
from tradingbot.backtesting.report import render_text, write_run
from tradingbot.backtesting.trades import ExitParams
from tradingbot.data.intraday import IntradayStore
from tradingbot.data.store import ParquetStore
from tradingbot.risk.costs import IBKR_FIXED, IBKR_TIERED, CostParams
from tradingbot.risk.limits import LimitParams
from tradingbot.risk.sizing import RiskParams
from tradingbot.scanner.features import attach_splits
from tradingbot.scanner.pipeline import build_candidates, read_candidates
from tradingbot.signals.daily_trigger import TriggerParams
from tradingbot.signals.orb import OrbParams


def load_universe(store: ParquetStore, source: str, tickers: list[str], splits_from: str | None):
    series, cands = {}, {}
    for t in tickers:
        s = store.read_processed(source, t)
        if splits_from:
            s = attach_splits(s, store.read_processed(splits_from, t))
        try:
            c = read_candidates(store, source, t)
        except FileNotFoundError:
            build_candidates(store, source, t)
            c = read_candidates(store, source, t)
        series[t.upper()], cands[t.upper()] = s, c
    return series, cands


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", required=True)
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--splits-from", default=None)
    ap.add_argument("--equity-sek", type=float, default=10_000.0)  # D-008; also 50k / 200k
    ap.add_argument("--risk", type=float, default=0.005)  # test 0.005 / 0.01
    ap.add_argument("--slippage", type=float, default=10.0, help="bps scenario: 5/10/20")
    ap.add_argument("--commission", choices=["tiered", "fixed"], default="tiered")
    ap.add_argument("--fx-mode", choices=["usd_balance", "per_trade"], default="per_trade")
    ap.add_argument("--fx-cost-bps", type=float, default=25.0, help="assumption, Q-003")
    ap.add_argument("--max-cost-to-risk", type=float, default=0.25)
    ap.add_argument("--max-open-positions", type=int, default=4)
    ap.add_argument("--max-open-risk-pct", type=float, default=0.04)
    ap.add_argument(
        "--priority",
        choices=["baseline", "seeded_random", "research_hypothesis"],
        default="baseline",
    )
    ap.add_argument(
        "--seed", type=int, default=None, help="required for seeded_random (pre-registered)"
    )
    ap.add_argument(
        "--entry-model",
        choices=["daily", "orb"],
        default="daily",
        help="orb needs minute bars from scripts/fetch_minute.py (latest fetch per day)",
    )
    ap.add_argument("--orb-minutes", type=int, default=5)
    ap.add_argument("--run-id", default=None, help="default: UTC timestamp + config hash")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args(argv)

    costs = CostParams(
        commission=IBKR_TIERED if args.commission == "tiered" else IBKR_FIXED,
        fx_mode=args.fx_mode,
        fx_cost_bps=args.fx_cost_bps,
    )
    params = PortfolioParams(
        equity_start_sek=args.equity_sek,
        risk=RiskParams(risk_per_trade=args.risk, max_cost_to_risk=args.max_cost_to_risk),
        costs=costs,
        limits=LimitParams(
            max_open_positions=args.max_open_positions, max_open_risk_pct=args.max_open_risk_pct
        ),
        trigger=TriggerParams(slippage_bps=args.slippage),
        exits=ExitParams(),
        priority=PriorityParams(args.priority, seed=args.seed),
        entry_model=args.entry_model,
        orb=OrbParams(orb_minutes=args.orb_minutes),
    )
    store = ParquetStore(args.data_dir)
    series, cands = load_universe(store, args.source, args.tickers, args.splits_from)
    loader = IntradayStore(args.data_dir).read_latest if args.entry_model == "orb" else None
    result = run_portfolio(series, cands, params, minute_bars=loader)
    out = write_run(
        result, args.data_dir / "portfolio" / args.source, run_id=args.run_id, args=vars(args)
    )
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    print(render_text(summary))
    print(f"run written to {out}")
    return out


if __name__ == "__main__":
    main()
