"""Report for one portfolio run (PORTFOLIO_DESIGN §9, spec §11.6; P6).

``build_report(result)`` turns a ``PortfolioResult`` into plain dicts/frames with explicit
units; ``write_run`` stores everything under one run id; ``render_text`` prints the
summary. Nothing here changes the simulation – the report reads the ledger, trades,
rejections, reservations and 3d decisions the engine already produced.

Money: the account is in USD. SEK figures use the constant pilot ``fx_rate`` from the run's
CostParams (never a market rate). Four amounts are kept apart: deposit (SEK and USD gross),
deposit FX cost (usd_balance mode), trading P&L (Σ trade net + unrealized, i.e. after
commission, per-leg FX and slippage inside prices) and end equity. Identity reported and
checked: ``end_equity = deposit_usd − deposit_fx − cash_in_lieu_adj + trading_pnl`` where
cash in lieu is already inside trade P&L.

Rejections are split into STRATEGY reasons (the signal/risk pipeline would have refused the
trade in a single-ticker run too) and PORTFOLIO reasons (only the shared account/limits
refused it). A reason in neither list lands in ``other`` and is printed, never dropped.

Pilot data has no point-in-time universe: every report carries the D-012 note.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradingbot.backtesting.portfolio import PortfolioResult, rejections_frame
from tradingbot.backtesting.trades import trades_frame

REPORT_VERSION = "1"
D012_NOTE = "funktionstest på pilotdata utan point-in-time-universum (D-012); inte evidens för edge"

STRATEGY_REASONS = (
    "no_trigger",
    "gap_too_large",
    "stop_invalid",
    "levels_unavailable",
    "no_bar",
    "key_consumed",
    "position_open",
    "no_peak",
    "not_candidate",
    "zero_shares",
    "too_small",
    "cost_to_risk",
    "risk_exceeded",
    "not_a_fill",
)
PORTFOLIO_REASONS = (
    "reservation_exceeded",
    "insufficient_capital",
    "max_positions",
    "open_risk",
    "gross_exposure",
    "insufficient_cash",
    "sector_limit",
    "halted",
    "loss_limit_daily",
    "loss_limit_weekly",
    "not_prioritized",
    "duplicate_reservation",
)


def classify_rejections(reasons: list[str]) -> dict[str, dict[str, int]]:
    counts = pd.Series(reasons, dtype=object).value_counts() if reasons else pd.Series(dtype=int)
    out = {"strategy": {}, "portfolio": {}, "other": {}}
    for reason, n in counts.items():
        bucket = (
            "strategy"
            if reason in STRATEGY_REASONS
            else "portfolio"
            if reason in PORTFOLIO_REASONS
            else "other"
        )
        out[bucket][str(reason)] = int(n)
    return out


def _trade_stats(trades) -> dict[str, Any]:
    if not trades:
        return {
            "n": 0,
            "win_rate": None,
            "profit_factor": None,
            "mean_r": None,
            "median_r": None,
            "sum_net_usd": 0.0,
            "avg_win_r": None,
            "avg_loss_r": None,
        }
    r = np.array([t.r_multiple for t in trades])
    net = np.array([t.pnl_net_usd for t in trades])
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    return {
        "n": int(len(trades)),
        "win_rate": float((net > 0).mean()),
        "profit_factor": float(wins / losses)
        if losses > 0
        else (float("inf") if wins > 0 else None),
        "mean_r": float(r.mean()),
        "median_r": float(np.median(r)),
        "sum_net_usd": float(net.sum()),
        "avg_win_r": float(r[r > 0].mean()) if (r > 0).any() else None,
        "avg_loss_r": float(r[r < 0].mean()) if (r < 0).any() else None,
    }


def build_report(result: PortfolioResult) -> dict[str, Any]:
    acc = result.account
    rows = result.ledger_frame()
    trades = result.trades
    realistic = [t for t in trades if not t.shares_reduced_at_fill and not t.open_at_end]
    deposit_usd = acc.start_cash_usd
    deposit_sek = deposit_usd * acc.fx_rate
    unrealized = float(acc.unrealized_usd)
    trading_pnl = float(sum(t.pnl_net_usd for t in trades)) + unrealized
    # open_at_end trades are valued at last close without exit costs: their pnl is unrealized,
    # so avoid counting it twice.
    trading_pnl -= float(sum(t.pnl_net_usd for t in trades if t.open_at_end))
    end_equity = float(acc.equity_usd)
    identity_gap = end_equity - (deposit_usd - acc.deposit_fx_cost_usd + trading_pnl)

    equity_curve = rows[
        [
            "date",
            "equity",
            "equity_sek",
            "cash",
            "reserved",
            "market_value",
            "n_positions",
            "drawdown_pct",
            "gross_exposure_pct",
            "state",
        ]
    ].copy()
    utilization = {
        "sessions": int(len(rows)),
        "sessions_with_position": int((rows["n_positions"] > 0).sum()) if len(rows) else 0,
        "mean_gross_exposure_pct": float(rows["gross_exposure_pct"].replace(np.inf, np.nan).mean())
        if len(rows)
        else None,
        "max_gross_exposure_pct": float(rows["gross_exposure_pct"].replace(np.inf, np.nan).max())
        if len(rows)
        else None,
        "mean_positions": float(rows["n_positions"].mean()) if len(rows) else None,
        "max_positions": int(rows["n_positions"].max()) if len(rows) else 0,
        # reservations are released at the close, so the ledger row shows 0: measure them
        # from the PRE_OPEN reservation rows instead
        "mean_reserved_per_candidate_day_usd": None,
        "max_reserved_day_usd": None,
    }
    res_rows = [r for r in result.reservations if r.get("kind") == "row" and r.get("accepted")]
    if res_rows:
        per_day = pd.DataFrame(res_rows).groupby("date")["reservation_usd"].sum()
        utilization["mean_reserved_per_candidate_day_usd"] = float(per_day.mean())
        utilization["max_reserved_day_usd"] = float(per_day.max())
    rej_reasons = [r.reason for r in result.rejections]
    rejections = classify_rejections(rej_reasons)
    decisions = pd.DataFrame(result.decisions)
    blocked = (
        decisions[decisions["reason"] == "reservation_exceeded"] if len(decisions) else decisions
    )
    reservation_blocked_usd = (
        float((blocked["capital_required"] - blocked["reservation_usd"]).sum())
        if len(blocked)
        else 0.0
    )
    day_rows = [r for r in result.reservations if r.get("kind") == "day"]
    stale = [e for e in result.events if e.get("event") == "no_bar_open_position"]
    stale_by_ticker: dict[str, list[str]] = {}
    for e in stale:
        stale_by_ticker.setdefault(e["ticker"], []).append(str(e["date"]))
    states = (
        pd.Series([ls.state for ls in result.loss_states[1:]]).value_counts()
        if len(result.loss_states) > 1
        else pd.Series(dtype=int)
    )

    report = {
        "report_version": REPORT_VERSION,
        "note": D012_NOTE,
        "capital": {
            "deposit_sek": float(deposit_sek),
            "fx_rate_sek_per_usd": float(acc.fx_rate),
            "fx_rate_note": "konstant pilotparameter, inte en marknadskurs",
            "fx_mode": acc.fx_mode,
            "deposit_usd_gross": float(deposit_usd),
            "deposit_fx_cost_usd": float(acc.deposit_fx_cost_usd),
            "start_cash_usd": float(deposit_usd - acc.deposit_fx_cost_usd),
            "trading_pnl_usd": trading_pnl,
            "realized_usd": float(acc.realized_usd),
            "unrealized_usd": unrealized,
            "end_equity_usd": end_equity,
            "end_equity_sek": float(end_equity * acc.fx_rate),
            "net_pnl_usd": float(end_equity - deposit_usd),
            "net_pnl_sek": float((end_equity - deposit_usd) * acc.fx_rate),
            "identity_gap_usd": float(identity_gap),
            "cash_in_lieu_usd": float(acc.cash_in_lieu_usd),
        },
        "costs_usd": {
            "commission": float(acc.commission_usd),
            "fx_per_trade": float(acc.fx_usd - acc.deposit_fx_cost_usd),
            "fx_deposit": float(acc.deposit_fx_cost_usd),
            "slippage_in_prices_info": float(acc.slippage_usd),
            "total_charged": float(acc.commission_usd + acc.fx_usd),
        },
        "performance": {
            "all_trades": _trade_stats(trades),
            "realistic": _trade_stats(realistic),
            "counterfactual_reduced": _trade_stats([t for t in trades if t.shares_reduced_at_fill]),
            "open_at_end": int(sum(t.open_at_end for t in trades)),
            "ambiguous_same_day": int(sum(t.ambiguous_same_day for t in trades)),
            "partial_skipped": int(sum(t.partial_skipped for t in trades)),
            "loss_over_1r": int(sum(t.r_multiple < -1 for t in trades)),
            "max_drawdown_pct": float(rows["drawdown_pct"].max()) if len(rows) else None,
            "exit_reasons": {
                str(k): int(v)
                for k, v in pd.Series([t.exit_reason for t in trades]).value_counts().items()
            }
            if trades
            else {},
        },
        "utilization": utilization,
        "rejections": rejections,
        "limits": {
            "binding_counts": {k: v for k, v in rejections["portfolio"].items()},
            "reservation_blocked_fills": int(
                rejections["portfolio"].get("reservation_exceeded", 0)
            ),
            "reservation_blocked_usd": reservation_blocked_usd,
            "priority_binding_days": int(sum(bool(r.get("priority_binding")) for r in day_rows)),
            "days_with_candidates": int(len(day_rows)),
            "loss_state_sessions": {str(k): int(v) for k, v in states.items()},
            "final_state": result.loss_states[-1].state if result.loss_states else None,
            "halted_on": str(
                next((ls.halted_on for ls in result.loss_states if ls.halted_on), None)
            ),
        },
        "stale_marks": {
            "sessions": int(len(stale)),
            "by_ticker": stale_by_ticker,
            "note": "öppen position utan dagsstapel: värderad till senast kända kurs",
        },
        "entry_model": result.params.entry_model,
        "candidates": {
            **result.candidate_stats,
            "days_with_reservable_candidates": int(len(day_rows)),
            "note": "candidate_rows = rader med is_candidate; candidate_days_distinct = "
            "sessioner med minst en sådan rad; days_with_reservable_candidates = sessioner "
            "där minst en rad passerade registret och nivåerna (key_consumed, position_open, "
            "no_peak och levels_unavailable räknas inte)",
        },
        "orb": result.summary()["orb"],
        "versions": {
            **result.summary()["versions"],
            "portfolio_version": result.summary()["portfolio"]["portfolio_version"],
        },
    }
    return {"summary": report, "equity_curve": equity_curve}


def params_to_dict(params) -> dict:
    def conv(x):
        if is_dataclass(x):
            return {k: conv(v) for k, v in asdict(x).items()}
        if isinstance(x, dict):
            return {k: conv(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [conv(v) for v in x]
        return x

    return conv(params)


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=5).strip()
    except Exception:  # noqa: BLE001 - outside a repo or git missing
        return None


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12]


def write_run(
    result: PortfolioResult, out_dir: Path, *, run_id: str | None = None, args: dict | None = None
) -> Path:
    """Write trades, rejections, reservations, decisions, ledger, equity curve, summary and
    the reproducible config under out_dir/<run_id>/."""
    config = {
        "params": params_to_dict(result.params),
        "args": {
            k: (sorted(v) if k == "tickers" else str(v) if isinstance(v, Path) else v)
            for k, v in (args or {}).items()
        },
        "tickers": sorted(
            {t.ticker for t in result.trades} | {r.ticker for r in result.rejections}
        ),
        "git_commit": git_commit(),
        "python": platform.python_version(),
        "pandas": pd.__version__,
    }
    hashed = {k: v for k, v in config.items() if k != "git_commit"}
    hashed["args"] = {k: v for k, v in hashed["args"].items() if k != "run_id"}
    config["config_hash"] = config_hash(hashed)
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + config["config_hash"]
    d = Path(out_dir) / run_id
    d.mkdir(parents=True, exist_ok=True)
    rep = build_report(result)
    rep["summary"]["run_id"] = run_id
    rep["summary"]["config_hash"] = config["config_hash"]
    if result.trades:
        trades_frame(result.trades).to_parquet(d / "trades.parquet", index=False)
    if result.rejections:
        rejections_frame(result).to_parquet(d / "rejections.parquet", index=False)
    if result.reservations:
        pd.DataFrame(result.reservations).astype({"key": str}, errors="ignore").to_parquet(
            d / "reservations.parquet", index=False
        )
    if result.decisions:
        pd.DataFrame(result.decisions).astype({"key": str}).to_parquet(
            d / "decisions.parquet", index=False
        )
    result.ledger_frame().to_parquet(d / "ledger.parquet", index=False)
    result.account.ledger.events_frame().to_parquet(d / "events.parquet", index=False)
    rep["equity_curve"].to_parquet(d / "equity_curve.parquet", index=False)
    (d / "summary.json").write_text(
        json.dumps(rep["summary"], indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    (d / "config.json").write_text(
        json.dumps(config, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    return d


def render_text(summary: dict) -> str:
    c, perf, lim, u = (
        summary["capital"],
        summary["performance"],
        summary["limits"],
        summary["utilization"],
    )
    k, rj, st = summary["costs_usd"], summary["rejections"], summary["stale_marks"]
    cd = summary["candidates"]
    r = perf["realistic"]
    a = perf["all_trades"]
    f = _fmt
    lines = [
        f"== {summary['note']} ==",
        f"Insatt: {c['deposit_sek']:.0f} SEK = {c['deposit_usd_gross']:.2f} USD "
        f"@ {c['fx_rate_sek_per_usd']} ({c['fx_rate_note']}), fx_mode={c['fx_mode']}, "
        f"deposit-FX {c['deposit_fx_cost_usd']:.2f} USD",
        f"Handels-P&L: {c['trading_pnl_usd']:+.2f} USD (realiserat {c['realized_usd']:+.2f}, "
        f"orealiserat {c['unrealized_usd']:+.2f})",
        f"Slut-equity {c['end_equity_usd']:.2f} USD = {c['end_equity_sek']:.0f} SEK; "
        f"netto {c['net_pnl_usd']:+.2f} USD ({c['net_pnl_sek']:+.0f} SEK); "
        f"identitetsgap {c['identity_gap_usd']:+.6f}",
        f"Kostnader (USD): courtage {k['commission']:.2f}, FX per affär {k['fx_per_trade']:.2f}, "
        f"slippage i priserna {k['slippage_in_prices_info']:.2f}",
        f"Affärer (realistiska): n={r['n']} win_rate={f(r['win_rate'], '.0%')} "
        f"PF={f(r['profit_factor'], '.2f')} mean_R={f(r['mean_r'], '.3f')} "
        f"median_R={f(r['median_r'], '.3f')}",
        f"Alla: n={a['n']} kontrafaktiska={perf['counterfactual_reduced']['n']} "
        f"open_at_end={perf['open_at_end']} ambiguous={perf['ambiguous_same_day']} "
        f"partial_skipped={perf['partial_skipped']} förlust>1R={perf['loss_over_1r']}",
        f"Max drawdown {f(perf['max_drawdown_pct'], '.2%')}; exits {perf['exit_reasons']}",
        f"Utnyttjande: sessioner {u['sessions']}, med position {u['sessions_with_position']}, "
        f"snitt brutto {f(u['mean_gross_exposure_pct'], '.1%')}, max positioner "
        f"{u['max_positions']}, reserverat per kandidatdag snitt "
        f"{f(u['mean_reserved_per_candidate_day_usd'], '.2f')} USD",
        f"Avslag strategi: {rj['strategy']}",
        f"Avslag portfölj: {rj['portfolio']}",
        f"Avslag övrigt: {rj['other']}",
        f"Gränser som bundit: {lim['binding_counts']}; "
        f"reservation_blocked_fills={lim['reservation_blocked_fills']} "
        f"({lim['reservation_blocked_usd']:.2f} USD); "
        f"priority_binding_days={lim['priority_binding_days']} av {lim['days_with_candidates']}",
        f"Förlustlägen {lim['loss_state_sessions']}; slutläge {lim['final_state']} "
        f"halted_on={lim['halted_on']}",
        f"Gamla kurser (position utan dagsstapel): {st['sessions']} sessioner {st['by_ticker']}",
        f"Kandidater: {cd['candidate_rows']} rader på {cd['candidate_days_distinct']} sessioner, "
        f"{cd['days_with_reservable_candidates']} dagar med reserverbar kandidat, "
        f"{cd['rows_off_calendar']} rader utanför kalendern",
    ]
    if summary.get("entry_model") == "orb":
        o = summary["orb"]
        lines.append(f"ORB: {o['attempts']} försök, utfall {o.get('outcomes', {})}")
        for k, v in sorted(o.get("by_ticker_year", {}).items()):
            lines.append(
                f"  {k}: försök {v['attempts']}, orb_incomplete {v['orb_incomplete']} "
                f"({v['orb_incomplete_ratio']:.0%}), no_minute_bars {v['no_minute_bars']}, "
                f"lapsed {v['lapsed']}, fill {v['fill_candidate']}, "
                f"open_mismatch {v['open_mismatch']}"
            )
    return "\n".join(lines)


def _fmt(x, spec: str) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x))):
        return "–"
    if isinstance(x, float) and np.isinf(x):
        return "inf"
    return format(x, spec)
