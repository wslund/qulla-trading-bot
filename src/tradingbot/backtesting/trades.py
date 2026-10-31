"""Simulated trades and exits for daily_stop_entry (STEP3_DESIGN §7, spec §6/§6.1/§8, D-013).

One ``Trade`` per risk-approved simulated entry; everything after the entry day is decided
by ``step_position`` – a pure function of the position state and ONE bar – so no exit code
can read ahead. Day order for an open position (spec §6.1):

  1. open:  pending orders from close T−1 fill at open × (1 − slip): a trailing exit sells
            everything (the stop has no effect that day); a partial sells its shares and the
            stop moves to breakeven (= entry fill) only after that fill; ``partial_skipped``
            moves the stop to breakeven at the same moment without any sell leg.
  2. stop:  open ≤ stop → exit at open (``stop_gap``, loss > 1R is real), else low ≤ stop →
            exit at stop. Both minus slippage.
  3. close: decisions for tomorrow's open – partial on the close of ``entry + partial_days``
            bars; trailing from that close onwards: close[T] < SMA10[T] → exit next open.
            A decision is never executed on the bar it was taken.

Interpretations (not in the spec text, flagged here): trailing starts at the partial
decision close, not before it; ``partial_days`` counts bars, not calendar days; if both a
partial and a trailing exit are decided on the same close the full exit wins; after a
partial fills at the open, the breakeven stop is active for the rest of that day.

Splits while open: on a bar with split_ratio r the as-traded prices from that bar on are
in new units, so shares × r, stop / r, entry reference / r; the position's value is
unchanged. A fractional share from r is sold at that bar's open as cash in lieu
(``split_cash_in_lieu_usd``), without costs. ``initial_risk_usd`` is in USD and unchanged.

Costs: ``cost_estimate`` is 3d's AssumedCosts at entry (planned legs). ``cost_actual`` is
recomputed from the legs that actually happened and their simulated prices. Slippage is
inside every fill price (entry × (1+slip), sells × (1−slip)); it is reported but NOT
subtracted again. Net P&L = proceeds − cost basis − commission − FX. R = net / initial
risk. ``usd_balance`` FX is paid once per run (Account), not per trade.

Account: the approver's account is constant for the run (no equity update after fills, no
cash depletion across trades, one position per ticker) – the portfolio engine's job later.
Trades flagged ``shares_reduced_at_fill`` are counterfactual (D-013) and are reported as a
separate group, never merged into the realistic set. ``open_at_end`` trades are valued at
the last close without exit costs and reported separately.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from tradingbot.risk.costs import COST_VERSION, AssumedCosts, CostParams
from tradingbot.risk.sizing import SIZING_VERSION
from tradingbot.scanner.features import FEATURE_VERSION, SPEC_VERSION
from tradingbot.scanner.levels import LEVELS_VERSION, LevelsError, compute_levels
from tradingbot.scanner.rules import RULES_VERSION
from tradingbot.signals.daily_trigger import (
    FILL_CANDIDATE,
    LEVELS_UNAVAILABLE,
    TRIGGER_VERSION,
    Approver,
    TriggerOutcome,
    TriggerParams,
    approve_and_record,
    build_signal,
    evaluate_day,
)
from tradingbot.signals.state import STATE_VERSION, Reason, SetupKey, SetupRegistry

TRADES_VERSION = "1"

# exit reasons
AMBIGUOUS_SAME_DAY = "ambiguous_same_day_stop"
STOP_GAP = "stop_gap"
STOP = "stop"
TRAIL = "trail_sma10"
OPEN_AT_END = "open_at_end"

VERSIONS = {
    "spec_version": SPEC_VERSION,
    "feature_version": FEATURE_VERSION,
    "rules_version": RULES_VERSION,
    "levels_version": LEVELS_VERSION,
    "trigger_version": TRIGGER_VERSION,
    "state_version": STATE_VERSION,
    "sizing_version": SIZING_VERSION,
    "cost_version": COST_VERSION,
    "trades_version": TRADES_VERSION,
}


@dataclass(frozen=True)
class ExitParams:
    """Spec §8 defaults; ``partial_fraction``/``min_shares_for_partial`` come from CostParams
    so the planned legs (3d) and the simulated legs use the same values."""

    partial_days: int = 3  # bars after entry; decision on that close, fill next open
    trail_ma: int = 10  # v0.2 locks 10; 20 is a test variant


@dataclass(frozen=True)
class Leg:
    """One simulated fill. ``price`` includes slippage; ``ref`` is the price before it."""

    date: date
    name: str  # entry / partial / exit / cash_in_lieu
    side: str
    shares: int | float
    ref: float
    price: float
    commission: float
    slippage: float
    fx: float

    @property
    def notional(self) -> float:
        return self.shares * self.price


@dataclass
class Position:
    """Mutable state of one open position; only ``step_position`` changes it."""

    key: SetupKey
    entry_date: date
    entry_fill: float  # current units; breakeven reference
    shares: int
    stop: float
    initial_risk_usd: float
    bars_held: int = 0
    partial_done: bool = False
    partial_skipped: bool = False
    pending_partial: int = 0  # shares to sell at next open
    pending_breakeven: bool = False  # partial_skipped: move stop at next open
    pending_exit: bool = False  # trailing exit at next open
    legs: list[Leg] = field(default_factory=list)
    splits: int = 0
    split_cash_in_lieu_usd: float = 0.0
    closed: bool = False
    exit_reason: str = ""
    exit_date: date | None = None


# --- pure per-bar logic ---------------------------------------------------------------------


def _sell(
    pos: Position, d: date, name: str, shares: int, ref: float, slip: float, costs: CostParams
) -> Leg:
    price = ref * (1 - slip)
    fx = shares * price * costs.fx_cost_bps / 10_000 if costs.fx_mode == "per_trade" else 0.0
    leg = Leg(
        d,
        name,
        "sell",
        shares,
        ref,
        price,
        costs.commission.commission(shares),
        shares * ref * slip,
        fx,
    )
    pos.legs.append(leg)
    pos.shares -= shares
    return leg


def apply_split(pos: Position, d: date, ratio: float, open_: float) -> None:
    """Express the position in post-split units; sells any fractional share as cash in lieu."""
    if ratio == 1.0:
        return
    raw = pos.shares * ratio
    whole = math.floor(raw + 1e-9)
    frac = raw - whole
    if frac > 1e-9:
        pos.split_cash_in_lieu_usd += frac * open_
        pos.legs.append(Leg(d, "cash_in_lieu", "sell", frac, open_, open_, 0.0, 0.0, 0.0))
    pos.shares = whole
    pos.pending_partial = math.floor(pos.pending_partial * ratio + 1e-9)
    pos.stop /= ratio
    pos.entry_fill /= ratio
    pos.splits += 1


def step_position(
    pos: Position,
    d: date,
    open_: float,
    high: float,
    low: float,
    close: float,
    sma: float,
    slippage_bps: float,
    costs: CostParams,
    exits: ExitParams,
) -> None:
    """Apply spec §6.1 steps 1–3 and the close decisions (§8) for one bar AFTER the entry
    day. ``sma`` is the trailing MA at this close in this bar's share units."""
    if pos.closed:
        raise RuntimeError("step on a closed position")
    slip = slippage_bps / 10_000
    pos.bars_held += 1
    # 1. open: pending orders from yesterday's close
    if pos.pending_exit:
        _sell(pos, d, "exit", pos.shares, open_, slip, costs)
        pos.closed, pos.exit_reason, pos.exit_date = True, TRAIL, d
        return
    if pos.pending_partial:
        _sell(pos, d, "partial", pos.pending_partial, open_, slip, costs)
        pos.pending_partial, pos.partial_done, pos.stop = 0, True, pos.entry_fill
    elif pos.pending_breakeven:
        pos.pending_breakeven, pos.partial_done, pos.stop = False, True, pos.entry_fill
    # 2. stop
    if open_ <= pos.stop:
        _sell(pos, d, "exit", pos.shares, open_, slip, costs)
        pos.closed, pos.exit_reason, pos.exit_date = True, STOP_GAP, d
        return
    if low <= pos.stop:
        _sell(pos, d, "exit", pos.shares, pos.stop, slip, costs)
        pos.closed, pos.exit_reason, pos.exit_date = True, STOP, d
        return
    # 3. close: decisions for tomorrow's open
    if pos.bars_held == exits.partial_days and not pos.partial_done:
        if pos.shares >= costs.min_shares_for_partial:
            pos.pending_partial = math.floor(pos.shares * costs.partial_fraction)
        else:
            pos.partial_skipped, pos.pending_breakeven = True, True
    if pos.bars_held >= exits.partial_days and close < sma:
        pos.pending_exit = True


def sma_in_bar_units(close: np.ndarray, split_ratio: np.ndarray, i: int, n: int) -> float:
    """Mean of the last ``n`` closes up to and including bar ``i``, each expressed in bar
    ``i``'s share units (earlier closes divided by the splits between them and ``i``)."""
    lo = max(0, i - n + 1)
    vals = []
    factor = 1.0
    for j in range(i, lo - 1, -1):
        vals.append(close[j] / factor)
        factor *= split_ratio[j]  # split on bar j applies to bars before j
    return float(np.mean(vals))


# --- the trade record -------------------------------------------------------------------------


@dataclass(frozen=True)
class Trade:
    ticker: str
    peak_date: date
    candidate_id: str | None
    setup_group_id: Any
    entry_date: date
    entry_fill: float  # entry-day units, incl. slippage
    shares: int
    shares_ordered: int
    shares_reduced_at_fill: bool  # counterfactual (D-013): report separately
    initial_stop: float
    initial_risk_usd: float
    gap_fill: bool
    ambiguous_same_day: bool
    partial_date: date | None
    partial_price: float | None
    partial_shares: int
    partial_skipped: bool
    exit_date: date
    exit_price: float  # last leg's fill price (or last close if open_at_end)
    exit_reason: str
    bars_held: int
    splits: int
    split_cash_in_lieu_usd: float
    open_at_end: bool
    pnl_gross_usd: float  # proceeds − cost basis at fill prices (slippage inside)
    cost_estimate: dict[str, float]  # 3d AssumedCosts at entry (planned legs)
    cost_actual: dict[str, float]  # from the legs that happened
    pnl_net_usd: float  # gross − actual commission − actual fx
    r_multiple: float
    legs: tuple[Leg, ...]
    slippage_bps: float
    fx_rate: float
    fx_mode: str
    versions: dict[str, str]

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("legs")
        d.pop("versions")
        d.update(self.versions)
        d["cost_estimate_total"] = sum(self.cost_estimate.values())
        d["cost_actual_total"] = sum(self.cost_actual.values())
        d["loss_over_1r"] = bool(self.r_multiple < -1.0)
        d["loss_over_2r"] = bool(self.r_multiple < -2.0)
        d.pop("cost_estimate")
        d.pop("cost_actual")
        return d


def _cost_dict(ac: AssumedCosts | None) -> dict[str, float]:
    if ac is None:
        return {"commission": 0.0, "slippage": 0.0, "fx": 0.0}
    return {"commission": ac.commission, "slippage": ac.slippage, "fx": ac.fx}


def close_trade(
    pos: Position,
    *,
    ticker: str,
    row: pd.Series | dict,
    entry_leg: Leg,
    decision: Any,
    outcome: TriggerOutcome,
    costs: CostParams,
    last_close: float | None = None,
) -> Trade:
    """Assemble the Trade from a closed position (or an open one at the end of data)."""
    open_at_end = not pos.closed
    sells = [leg for leg in pos.legs if leg.side == "sell"]
    proceeds = sum(leg.notional for leg in sells)
    if open_at_end:
        assert last_close is not None
        proceeds += pos.shares * last_close
    gross = proceeds - entry_leg.notional
    actual = {
        "commission": entry_leg.commission + sum(leg.commission for leg in sells),
        "slippage": entry_leg.slippage + sum(leg.slippage for leg in sells),
        "fx": entry_leg.fx + sum(leg.fx for leg in sells),
    }
    net = gross - actual["commission"] - actual["fx"]
    partial = next((leg for leg in sells if leg.name == "partial"), None)
    exit_leg = next((leg for leg in sells if leg.name == "exit"), None)
    fc = outcome.fill
    assert fc is not None
    return Trade(
        ticker=ticker,
        peak_date=pos.key[1],
        candidate_id=row.get("candidate_id") if hasattr(row, "get") else None,
        setup_group_id=row.get("setup_group_id") if hasattr(row, "get") else None,
        entry_date=pos.entry_date,
        entry_fill=entry_leg.price,
        shares=int(entry_leg.shares),
        shares_ordered=int(getattr(decision, "shares_ordered", entry_leg.shares)),
        shares_reduced_at_fill=bool(getattr(decision, "shares_reduced_at_fill", False)),
        initial_stop=fc.stop_price,
        initial_risk_usd=pos.initial_risk_usd,
        gap_fill=fc.gap_fill,
        ambiguous_same_day=fc.ambiguous_same_day,
        partial_date=partial.date if partial else None,
        partial_price=partial.price if partial else None,
        partial_shares=int(partial.shares) if partial else 0,
        partial_skipped=pos.partial_skipped,
        exit_date=pos.exit_date or row["date"],
        exit_price=exit_leg.price if exit_leg else float(last_close),
        exit_reason=pos.exit_reason or OPEN_AT_END,
        bars_held=pos.bars_held,
        splits=pos.splits,
        split_cash_in_lieu_usd=pos.split_cash_in_lieu_usd,
        open_at_end=open_at_end,
        pnl_gross_usd=gross,
        cost_estimate=_cost_dict(getattr(decision, "costs", None)),
        cost_actual=actual,
        pnl_net_usd=net,
        r_multiple=net / pos.initial_risk_usd,
        legs=tuple([entry_leg, *pos.legs]),
        slippage_bps=outcome.signal.params.slippage_bps,
        fx_rate=costs.fx_rate,
        fx_mode=costs.fx_mode,
        versions={
            **VERSIONS,
            **{
                k: str(row[k])
                for k in ("spec_version", "feature_version", "rules_version")
                if hasattr(row, "get") and row.get(k) is not None
            },
        },
    )


# --- driver: one ticker, day by day --------------------------------------------------------------


@dataclass(frozen=True)
class Rejection:
    """A candidate row that did not become a trade, with the first reason that stopped it."""

    ticker: str
    date: date
    peak_date: date | None
    stage: str  # registry / levels / trigger / risk
    reason: str


def _entry_leg(d: date, decision: Any, fc, slip: float, costs: CostParams) -> Leg:
    shares = int(decision.shares)
    fill = fc.fill_price
    ref = fill / (1 + slip)
    fx = shares * fill * costs.fx_cost_bps / 10_000 if costs.fx_mode == "per_trade" else 0.0
    return Leg(
        d,
        "entry",
        "buy",
        shares,
        ref,
        fill,
        costs.commission.commission(shares),
        shares * (fill - ref),
        fx,
    )


def run_ticker(
    series: pd.DataFrame,
    cands: pd.DataFrame,
    approver: Approver,
    *,
    costs: CostParams,
    trigger: TriggerParams | None = None,
    exits: ExitParams | None = None,
    registry: SetupRegistry | None = None,
) -> tuple[list[Trade], list[Rejection], SetupRegistry]:
    """Replay one ticker's as-traded series bar by bar: exits for the open position first
    (§6.1), then at most one new entry. The approver must return an object with
    ``approved``, ``reason``, ``shares``, ``actual_risk`` (3d's RiskDecision)."""
    tp = trigger or TriggerParams()
    xp = exits or ExitParams()
    reg = registry or SetupRegistry()
    s = series.sort_values("date").reset_index(drop=True)
    dates = [pd.Timestamp(x).date() for x in s["date"]]
    o, h, lo_, c = (s[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    sr = s["split_ratio"].to_numpy(float)
    by_day = {pd.Timestamp(r["date"]).date(): r for _, r in cands.iterrows()}
    ticker = str(s["ticker"].iloc[0])
    slip = tp.slippage_bps / 10_000
    trades: list[Trade] = []
    rejections: list[Rejection] = []
    pos: Position | None = None
    ctx: dict[str, Any] = {}

    for i, d in enumerate(dates):
        if pos is not None:
            apply_split(pos, d, sr[i], o[i])
            sma = sma_in_bar_units(c, sr, i, xp.trail_ma)
            step_position(pos, d, o[i], h[i], lo_[i], c[i], sma, tp.slippage_bps, costs, xp)
            if pos.closed:
                reg.record_close(pos.key, d, pos.exit_reason)
                trades.append(close_trade(pos, ticker=ticker, costs=costs, **ctx))
                pos = None
        row = by_day.get(d)
        if row is None or not bool(row.get("is_candidate", False)):
            continue
        dec = reg.decide(row)
        if not dec.allow_order:
            if dec.reason != Reason.NOT_CANDIDATE:
                rejections.append(
                    Rejection(
                        ticker, d, dec.key[1] if dec.key else None, "registry", dec.reason.value
                    )
                )
            continue
        try:
            sig = build_signal(dec, compute_levels(s, row), tp)
        except LevelsError as e:
            reg.record_no_fill(dec.key, d, LEVELS_UNAVAILABLE)
            rejections.append(
                Rejection(ticker, d, dec.key[1], "levels", f"{LEVELS_UNAVAILABLE}: {e}")
            )
            continue
        out = evaluate_day(sig, o[i], h[i], lo_[i])
        if out.code != FILL_CANDIDATE:
            approve_and_record(reg, out, False, out.code)
            rejections.append(Rejection(ticker, d, dec.key[1], "trigger", out.code))
            continue
        decision = approver(out)
        if isinstance(decision, tuple):
            raise TypeError("run_ticker needs an approver returning a RiskDecision-like object")
        final = approve_and_record(reg, out, bool(decision.approved), str(decision.reason))
        if not decision.approved:
            rejections.append(Rejection(ticker, d, dec.key[1], "risk", decision.reason))
            continue
        fc = out.fill
        assert fc is not None
        entry = _entry_leg(d, decision, fc, slip, costs)
        pos = Position(
            dec.key,
            d,
            fc.fill_price,
            int(decision.shares),
            fc.stop_price,
            float(decision.actual_risk),
        )
        ctx = {"row": row, "entry_leg": entry, "decision": decision, "outcome": out}
        if final == "filled_stopped_same_day":
            # 3c already closed the key in the registry; one exit leg, no second close.
            _sell(pos, d, "exit", pos.shares, fc.stop_price, slip, costs)
            pos.closed, pos.exit_reason, pos.exit_date = True, AMBIGUOUS_SAME_DAY, d
            trades.append(close_trade(pos, ticker=ticker, costs=costs, **ctx))
            pos = None
    if pos is not None:
        trades.append(close_trade(pos, ticker=ticker, costs=costs, last_close=c[-1], **ctx))
    return trades, rejections, reg


# --- reporting ----------------------------------------------------------------------------------


def trades_frame(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([t.to_row() for t in trades])


def rejections_frame(rejections: list[Rejection]) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in rejections])


def summarize(trades: list[Trade], rejections: list[Rejection]) -> dict[str, Any]:
    """Function-test statistics (D-012: never evidence of edge on pilot data).

    ``realistic`` excludes counterfactual (``shares_reduced_at_fill``) and ``open_at_end``
    trades; those are counted in their own groups. Rejections are counted per reason."""

    def stats(ts: list[Trade]) -> dict[str, Any]:
        if not ts:
            return {"n": 0}
        r = np.array([t.r_multiple for t in ts])
        net = np.array([t.pnl_net_usd for t in ts])
        wins, losses = net[net > 0].sum(), -net[net < 0].sum()
        return {
            "n": len(ts),
            "mean_r": float(r.mean()),
            "sum_net_usd": float(net.sum()),
            "profit_factor": float(wins / losses) if losses > 0 else float("inf"),
            "win_rate": float((r > 0).mean()),
            "loss_over_1r": int((r < -1).sum()),
            "loss_over_2r": int((r < -2).sum()),
            "cost_actual_usd": float(sum(sum(t.cost_actual.values()) for t in ts)),
            "cost_estimate_usd": float(sum(sum(t.cost_estimate.values()) for t in ts)),
            "exit_reasons": {
                str(k): int(v)
                for k, v in pd.Series([t.exit_reason for t in ts]).value_counts().items()
            },
        }

    realistic = [t for t in trades if not t.shares_reduced_at_fill and not t.open_at_end]
    counterfactual = [t for t in trades if t.shares_reduced_at_fill]
    rej = pd.Series([r.reason for r in rejections]).value_counts() if rejections else pd.Series()
    return {
        "realistic": stats(realistic),
        "counterfactual_reduced": stats(counterfactual),
        "open_at_end": len([t for t in trades if t.open_at_end]),
        "ambiguous_same_day": sum(t.ambiguous_same_day for t in trades),
        "partial_skipped": sum(t.partial_skipped for t in trades),
        "rejections": {str(k): int(v) for k, v in rej.items()},
        "versions": VERSIONS,
    }
