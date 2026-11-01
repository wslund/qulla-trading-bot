"""Portfolio engine for daily_stop_entry: one clock, one account, many tickers (P4).

PORTFOLIO_DESIGN §2–§8 as code. Per XNYS session T (``SessionClock``), four phases, each run
for every ticker before the next phase starts, tickers in alphabetical order:

  PRE_OPEN  reads bars <= T−1 (+ split_ratio[T] through compute_levels) and the account as
            marked at close T−1.  Registry decisions, levels, Signal; P3 priority (D-015);
            P3 reservations through P2 limits with LossState decided at close T−1 (D-016).
            Rejected candidates -> record_no_fill (key stays ARMED).
  OPEN      reads open[T] and split_ratio[T].  §7.1: splits on positions and account first;
            then 3f step_position(phase="open"): pending exits / partials at the open, stop
            gaps. Fills are mirrored into the account; closed positions -> record_close.
  INTRADAY  reads high/low[T].  Existing positions: step_position(phase="intraday") (stop
            touched).  New entries: evaluate_day on the reserved orders only; assess_fill
            (3d) with cash = the order's own reservation (never the account); an overrun is
            classified reservation_exceeded / insufficient_capital (D-016); approved ->
            approve_and_record + Account.buy; ambiguous same-day stop -> closed at once.
            No order reads anything another ticker did today (order invariance).
  CLOSE     reads close[T].  step_position(phase="close") (partial / trailing decisions for
            T+1); release unfilled reservations; mark-to-market; LossState.update_at_close
            -> state for T+1; ledger row with identity checks.

Cash freed by exits on T is not usable for entries on T: reservations were made in
PRE_OPEN against the T−1 account (D-016). Sizing uses equity at close T−1 (RISK_MODEL
§2.1.1). The exit rules are 3f's, unchanged; the account only mirrors the legs 3f produces.
Only ``daily_stop_entry`` here; ORB is P5.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from tradingbot.backtesting.account import Account
from tradingbot.backtesting.clock import Phase, SessionClock
from tradingbot.backtesting.priority import Candidate, PriorityParams, prioritize
from tradingbot.backtesting.reservations import (
    DayReservations,
    ReservationPlan,
    classify_overrun,
    release_unfilled,
    reserve_day,
)
from tradingbot.backtesting.trades import (
    AMBIGUOUS_SAME_DAY,
    ExitParams,
    Position,
    Rejection,
    Trade,
    _entry_leg,
    _sell,
    close_trade,
    sma_in_bar_units,
    step_position,
    summarize,
)
from tradingbot.backtesting.trades import apply_split as split_position
from tradingbot.risk.costs import CostParams
from tradingbot.risk.limits import LimitParams, LossState
from tradingbot.risk.sizing import APPROVED, INSUFFICIENT_CAPITAL, RiskParams, assess_fill
from tradingbot.risk.sizing import Account as SizingAccount
from tradingbot.scanner.levels import LevelsError, compute_levels
from tradingbot.signals.daily_trigger import (
    FILL_CANDIDATE,
    LEVELS_UNAVAILABLE,
    TriggerParams,
    approve_and_record,
    build_signal,
    evaluate_day,
)
from tradingbot.signals.state import Reason, SetupRegistry

PORTFOLIO_VERSION = "1"
FEATURE_COLUMNS_FOR_PRIORITY = ("consol_pct_below_high", "consol_tightness", "prior_move_pct")


@dataclass(frozen=True)
class PortfolioParams:
    equity_start_sek: float = 10_000.0  # D-008
    risk: RiskParams = field(default_factory=RiskParams)
    costs: CostParams = field(default_factory=CostParams)
    limits: LimitParams = field(default_factory=LimitParams)
    trigger: TriggerParams = field(default_factory=TriggerParams)
    exits: ExitParams = field(default_factory=ExitParams)
    priority: PriorityParams = field(default_factory=PriorityParams)


@dataclass
class _Open:
    """An open position with what close_trade needs later."""

    pos: Position
    ctx: dict[str, Any]
    legs_synced: int = 0  # how many of pos.legs are already mirrored into the account


@dataclass
class _Order:
    candidate: Candidate
    plan: ReservationPlan
    rank: int


@dataclass
class PortfolioResult:
    trades: list[Trade]
    rejections: list[Rejection]
    reservations: list[dict]
    decisions: list[dict]  # 3d RiskDecision rows at fill attempts
    loss_states: list[LossState]
    account: Account
    registry: SetupRegistry
    params: PortfolioParams
    events: list[dict] = field(default_factory=list)  # non-bookkeeping notes (no_bar, …)

    def ledger_frame(self) -> pd.DataFrame:
        return self.account.ledger.rows_frame()

    def summary(self) -> dict:
        s = summarize(self.trades, self.rejections)
        rows = self.ledger_frame()
        rej = pd.Series([r.reason for r in self.rejections])
        s["portfolio"] = {
            "sessions": int(len(rows)),
            "end_equity_usd": float(rows["equity"].iloc[-1]) if len(rows) else None,
            "max_drawdown_pct": float(rows["drawdown_pct"].max()) if len(rows) else None,
            "max_positions_held": int(rows["n_positions"].max()) if len(rows) else 0,
            "final_state": self.loss_states[-1].state if self.loss_states else None,
            "halted_on": next((ls.halted_on for ls in self.loss_states if ls.halted_on), None),
            "binding_limits": {
                k: int(v)
                for k, v in rej[
                    rej.isin(
                        [
                            "max_positions",
                            "open_risk",
                            "gross_exposure",
                            "insufficient_cash",
                            "reservation_exceeded",
                            "insufficient_capital",
                            "halted",
                            "loss_limit_daily",
                            "loss_limit_weekly",
                        ]
                    )
                ]
                .value_counts()
                .items()
            },
            "priority_binding_days": int(
                sum(
                    r.get("priority_binding", False)
                    for r in self.reservations
                    if r.get("kind") == "day"
                )
            ),
            "portfolio_version": PORTFOLIO_VERSION,
        }
        return s


def run_portfolio(
    series: dict[str, pd.DataFrame],
    cands: dict[str, pd.DataFrame],
    params: PortfolioParams | None = None,
    *,
    registry: SetupRegistry | None = None,
) -> PortfolioResult:
    """Replay all tickers through one account. ``series``/``cands`` map ticker -> frames as
    produced by the data layer and the scanner (as-traded, with split_ratio)."""
    p = params or PortfolioParams()
    reg = registry or SetupRegistry()
    tickers = sorted(series)
    data = {t: _TickerData(series[t], cands.get(t)) for t in tickers}
    start = min(d.dates[0] for d in data.values())
    end = max(d.dates[-1] for d in data.values())
    clock = SessionClock(start, end)
    prev_date = start - pd.Timedelta(days=1)  # "close before the first session"
    prev_date = prev_date.date() if hasattr(prev_date, "date") else prev_date
    account = Account.from_sek(p.equity_start_sek, p.costs, start)
    account.mark({}, prev_date)
    loss = LossState.start(account.equity_usd, prev_date)
    account.state = loss.state

    open_pos: dict[str, _Open] = {}
    trades: list[Trade] = []
    rejections: list[Rejection] = []
    res_rows: list[dict] = []
    dec_rows: list[dict] = []
    events: list[dict] = []
    loss_states: list[LossState] = [loss]
    pending_stops: dict[tuple, float] = {}
    slip = p.trigger.slippage_bps / 10_000

    for session in clock:
        T = session.date
        asof = account.last_mark_date
        equity_prev = account.equity_usd  # close T−1 (RISK_MODEL §2.1.1)
        orders: dict[str, _Order] = {}

        # ---------------- PRE_OPEN ----------------
        candidates: list[Candidate] = []
        for t in tickers:
            row = data[t].row_on(T)
            if row is None or not bool(row.get("is_candidate", False)):
                continue
            dec = reg.decide(row)
            if not dec.allow_order:
                if dec.reason != Reason.NOT_CANDIDATE:
                    rejections.append(
                        Rejection(
                            t, T, dec.key[1] if dec.key else None, "registry", dec.reason.value
                        )
                    )
                continue
            try:
                sig = build_signal(dec, compute_levels(data[t].series, row), p.trigger)
            except LevelsError as e:
                reg.record_no_fill(dec.key, T, LEVELS_UNAVAILABLE)
                rejections.append(
                    Rejection(t, T, dec.key[1], "levels", f"{LEVELS_UNAVAILABLE}: {e}")
                )
                continue
            feats = {
                c: float(row[c])
                for c in FEATURE_COLUMNS_FOR_PRIORITY
                if c in row.index and pd.notna(row[c])
            }
            candidates.append(Candidate(sig, feats))
        if candidates:
            prioritized = prioritize(candidates, p.priority)
            stops = {t: o.pos.stop for t, o in open_pos.items()}
            day: DayReservations = reserve_day(
                account,
                prioritized,
                asof=asof,
                on=T,
                state=loss.state,
                risk=p.risk,
                costs=p.costs,
                limits=p.limits,
                stops=stops,
                pending_stops=pending_stops,
            )
            by_key = {pr.candidate.key: pr for pr in prioritized}
            for plan in day.accepted:
                pr = by_key[plan.key]
                orders[plan.ticker] = _Order(pr.candidate, plan, pr.rank)
            for r in day.rejected:
                reg.record_no_fill(r.key, T, r.reason)
                rejections.append(Rejection(r.ticker, T, r.key[1], r.stage, r.reason))
            res_rows.extend({"kind": "row", **x} for x in day.rows())
            res_rows.append(
                {
                    "kind": "day",
                    "date": T,
                    "n_candidates": len(candidates),
                    "n_reserved": len(day.accepted),
                    "priority_binding": day.priority_binding,
                    "rule": day.rule,
                }
            )

        # ---------------- OPEN ----------------
        for t in sorted(open_pos):
            o = open_pos[t]
            bar = data[t].bar_on(T)
            if bar is None:
                events.append({"date": T, "ticker": t, "event": "no_bar_open_position"})
                continue
            if bar.split_ratio != 1.0:
                split_position(o.pos, T, bar.split_ratio, bar.open)
                account.apply_split(t, bar.split_ratio, bar.open, T, phase=Phase.OPEN)
                o.legs_synced = len(o.pos.legs)  # cash in lieu already booked by the account
            step_position(
                o.pos,
                T,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                float("nan"),
                p.trigger.slippage_bps,
                p.costs,
                p.exits,
                phase="open",
            )
            _sync_legs(o, account, Phase.OPEN)
            if o.pos.closed:
                _close(o, t, T, reg, account, trades, p.costs, open_pos)

        # ---------------- INTRADAY ----------------
        for t in sorted(open_pos):
            o = open_pos[t]
            bar = data[t].bar_on(T)
            if bar is None:
                continue
            step_position(
                o.pos,
                T,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                float("nan"),
                p.trigger.slippage_bps,
                p.costs,
                p.exits,
                phase="intraday",
            )
            _sync_legs(o, account, Phase.INTRADAY)
            if o.pos.closed:
                _close(o, t, T, reg, account, trades, p.costs, open_pos)
        for t in sorted(orders):
            od = orders[t]
            sig = od.candidate.signal
            bar = data[t].bar_on(T)
            if bar is None:
                approve_and_record(reg, _no_bar_outcome(sig), False, "no_bar")
                rejections.append(Rejection(t, T, sig.key[1], "trigger", "no_bar"))
                continue
            out = evaluate_day(sig, bar.open, bar.high, bar.low)
            if out.code != FILL_CANDIDATE:
                approve_and_record(reg, out, False, out.code)
                rejections.append(Rejection(t, T, sig.key[1], "trigger", out.code))
                continue
            res = account.reservations[sig.key]
            sizing_acct = SizingAccount(
                equity_prev,
                res.amount_usd,
                account.fx_rate,
                account.fx_mode,
                account.deposit_fx_cost_usd,
            )
            d = assess_fill(out, sizing_acct, p.risk, p.costs, od.plan.plan)
            reason = d.reason
            if not d.approved and reason == INSUFFICIENT_CAPITAL:
                reason = classify_overrun(account, sig.key, d.capital_required) or reason
            dec_rows.append(
                {
                    "date": T,
                    "ticker": t,
                    "key": sig.key,
                    "reason": reason,
                    "shares": d.shares,
                    "shares_ordered": d.shares_ordered,
                    "shares_reduced_at_fill": d.shares_reduced_at_fill,
                    "capital_required": d.capital_required,
                    "reservation_usd": res.amount_usd,
                    "cost_to_risk": d.cost_to_risk,
                    "rank": od.rank,
                }
            )
            if not d.approved:
                approve_and_record(reg, out, False, reason)
                rejections.append(Rejection(t, T, sig.key[1], "risk", reason))
                continue
            if d.shares_ordered != od.plan.shares_ordered:
                raise RuntimeError(
                    f"{t} {T}: sized {d.shares_ordered} but reserved {od.plan.shares_ordered}"
                )
            final = approve_and_record(reg, out, True, APPROVED)
            fc = out.fill
            assert fc is not None
            entry = _entry_leg(T, d, fc, slip, p.costs)
            account.buy(
                sig.key,
                int(d.shares),
                fc.fill_price,
                entry.commission,
                entry.fx,
                T,
                slippage=entry.slippage,
                phase=Phase.INTRADAY,
            )
            pos = Position(
                sig.key, T, fc.fill_price, int(d.shares), fc.stop_price, float(d.actual_risk)
            )
            pos.partial_skipped = int(d.shares) < p.costs.min_shares_for_partial
            o = _Open(
                pos, {"row": data[t].row_on(T), "entry_leg": entry, "decision": d, "outcome": out}
            )
            open_pos[t] = o
            if final == "filled_stopped_same_day":
                _sell(pos, T, "exit", pos.shares, fc.stop_price, slip, p.costs)
                pos.closed, pos.exit_reason, pos.exit_date = True, AMBIGUOUS_SAME_DAY, T
                _sync_legs(o, account, Phase.INTRADAY)
                _close(o, t, T, None, account, trades, p.costs, open_pos)  # registry closed by 3c

        # ---------------- CLOSE ----------------
        for t in sorted(open_pos):
            o = open_pos[t]
            bar = data[t].bar_on(T)
            if bar is None:
                continue
            sma = sma_in_bar_units(
                data[t].close, data[t].split, data[t].index_of(T), p.exits.trail_ma
            )
            step_position(
                o.pos,
                T,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                sma,
                p.trigger.slippage_bps,
                p.costs,
                p.exits,
                phase="close",
            )
        released = release_unfilled(account, T)
        for k in released:
            pending_stops.pop(k, None)
        marks = {}
        for t in open_pos:
            bar = data[t].bar_on(T)
            marks[t] = bar.close if bar is not None else account.holdings[t].mark
        account.mark(marks, T, phase=Phase.CLOSE)
        loss = loss.update_at_close(account.equity_usd, T, p.limits)
        loss_states.append(loss)
        account.state = loss.state
        account.ledger.close_session(account, session)

    for t in sorted(open_pos):
        o = open_pos[t]
        trades.append(
            close_trade(
                o.pos,
                ticker=t,
                costs=p.costs,
                last_close=data[t].close[-1],
                last_date=data[t].dates[-1],
                **o.ctx,
            )
        )
    return PortfolioResult(
        trades, rejections, res_rows, dec_rows, loss_states, account, reg, p, events
    )


# --- helpers ---------------------------------------------------------------------------------


class _Bar:
    __slots__ = ("open", "high", "low", "close", "split_ratio")

    def __init__(self, o, h, lo, c, sr):
        self.open, self.high, self.low, self.close, self.split_ratio = o, h, lo, c, sr


class _TickerData:
    def __init__(self, series: pd.DataFrame, cands: pd.DataFrame | None) -> None:
        s = series.sort_values("date").reset_index(drop=True)
        self.series = s
        self.dates = [pd.Timestamp(x).date() for x in s["date"]]
        self._idx = {d: i for i, d in enumerate(self.dates)}
        self.open, self.high, self.low, self.close = (
            s[k].to_numpy(float) for k in ("open", "high", "low", "close")
        )
        self.split = s["split_ratio"].to_numpy(float)
        self._rows = {}
        if cands is not None and len(cands):
            self._rows = {pd.Timestamp(r["date"]).date(): r for _, r in cands.iterrows()}

    def index_of(self, d: date) -> int:
        return self._idx[d]

    def bar_on(self, d: date) -> _Bar | None:
        i = self._idx.get(d)
        if i is None:
            return None
        return _Bar(self.open[i], self.high[i], self.low[i], self.close[i], self.split[i])

    def row_on(self, d: date):
        return self._rows.get(d)


def _sync_legs(o: _Open, account: Account, phase: Phase) -> None:
    """Mirror the legs 3f appended to the position into the account (sells only; cash in
    lieu is booked by Account.apply_split)."""
    ticker = o.pos.key[0]
    for leg in o.pos.legs[o.legs_synced :]:
        if leg.side == "sell" and leg.name != "cash_in_lieu":
            account.sell(
                ticker,
                int(leg.shares),
                leg.price,
                leg.commission,
                leg.fx,
                leg.date,
                slippage=leg.slippage,
                phase=phase,
                reason=leg.name,
            )
    o.legs_synced = len(o.pos.legs)


def _close(
    o: _Open,
    t: str,
    T: date,
    reg: SetupRegistry | None,
    account: Account,
    trades: list[Trade],
    costs: CostParams,
    open_pos: dict,
) -> None:
    if reg is not None:
        reg.record_close(o.pos.key, T, o.pos.exit_reason)
    trades.append(close_trade(o.pos, ticker=t, costs=costs, **o.ctx))
    del open_pos[t]
    if t in account.holdings:
        raise RuntimeError(f"{t}: position closed in 3f but shares remain in the account")


def _no_bar_outcome(sig):
    from tradingbot.signals.daily_trigger import TriggerOutcome

    return TriggerOutcome(sig, "no_bar", detail="no daily bar on T")


def rejections_frame(result: PortfolioResult) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in result.rejections])
