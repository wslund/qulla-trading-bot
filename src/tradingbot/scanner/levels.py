"""Order levels for a decision day T, in as-traded units (STEP3_DESIGN §2, step 3a).

Only the bars up to and including T-1 (``asof_date``) are used, except ``split_ratio`` of
bar T, which converts the levels into the share basis that trades on T (assumed known
before the open; flagged ``split_on_entry_day``). Splits *inside* the consolidation
window are handled by computing on a split-adjusted basis anchored at T-1: when the
series is cut at T-1 and rebased, the cumulative future-split factor at T-1 is 1, so the
adjusted values are already T-1 as-traded units.

``OrderLevels`` can only be produced here, from an as_traded series with known splits.
Chart (split-adjusted) levels are never order levels; plot.py does not import this module.
No trigger, fill or risk logic lives here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from tradingbot.data.compare import rebase
from tradingbot.data.schema import AS_TRADED, SPLIT_ADJUSTED
from tradingbot.scanner.features import FeatureError

LEVELS_VERSION = "1"


class LevelsError(FeatureError):
    """Levels cannot be computed from this input."""


@dataclass(frozen=True)
class OrderLevels:
    ticker: str
    decision_date: date  # T
    asof_date: date  # T-1, last bar used for prices
    peak_date: date
    window_start: date  # first bar after the peak
    window_end: date  # == asof_date
    window_bars: int
    breakout_level_at: float  # max(high) in window, units: shares trading on T
    low_prev_at: float  # low[T-1], same units
    adr20: float  # ratio, from the feature row
    split_ratio_on_t: float  # 1.0 when no split on T (or T unknown in the series)
    split_on_entry_day: bool
    units: str = "as_traded"
    levels_version: str = LEVELS_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def _validate(series: pd.DataFrame) -> None:
    if series.empty:
        raise LevelsError("empty series")
    basis = str(series["price_basis"].iloc[0])
    if basis != AS_TRADED:
        raise LevelsError(f"order levels require price_basis=as_traded, got {basis!r}")
    if series["split_ratio"].isna().any():
        raise LevelsError("split_ratio unknown (NaN); attach splits before computing levels")
    if not series["date"].is_monotonic_increasing or series["date"].duplicated().any():
        raise LevelsError("dates must be sorted and unique")


def compute_levels(series: pd.DataFrame, row: pd.Series | dict) -> OrderLevels:
    """Levels for the decision row ``row`` (needs date, asof_date, prior_move_peak_date, adr20).

    Raises LevelsError when the basis is wrong, splits are unknown, the peak is missing or
    the window is empty.
    """
    _validate(series)
    ticker = str(row["ticker"])
    t = pd.Timestamp(row["date"]).date()
    asof = pd.Timestamp(row["asof_date"]).date()
    peak = row.get("prior_move_peak_date")
    if peak is None or pd.isna(peak):
        raise LevelsError(f"{ticker} {t}: no prior_move_peak_date; levels undefined")
    peak = pd.Timestamp(peak).date()
    if not (peak < asof < t):
        raise LevelsError(f"{ticker}: expected peak {peak} < asof {asof} < T {t}")

    # Look-ahead protection: only bars <= T-1 take part in any price computation.
    upto = series[series["date"] <= asof].reset_index(drop=True)
    if upto.empty or upto["date"].iloc[-1] != asof:
        raise LevelsError(f"{ticker}: bar for asof_date {asof} missing in series")
    # Rebase the cut series: cumulative future splits at T-1 are 1, so the result is
    # expressed in T-1 as-traded share units even when a split falls inside the window.
    adj = rebase(upto, SPLIT_ADJUSTED)
    window = adj[adj["date"] > peak]
    if window.empty:
        raise LevelsError(f"{ticker}: empty consolidation window after peak {peak}")

    level = float(window["high"].max())
    low_prev = float(adj["low"].iloc[-1])

    # Share basis trading on T: a split with ex-date T divides every level.
    on_t = series.loc[series["date"] == t, "split_ratio"]
    ratio = float(on_t.iloc[0]) if len(on_t) else 1.0
    if ratio != 1.0:
        level /= ratio
        low_prev /= ratio

    adr = row.get("adr20")
    adr = float("nan") if adr is None or pd.isna(adr) else float(adr)
    return OrderLevels(
        ticker=ticker,
        decision_date=t,
        asof_date=asof,
        peak_date=peak,
        window_start=window["date"].iloc[0],
        window_end=window["date"].iloc[-1],
        window_bars=int(len(window)),
        breakout_level_at=level,
        low_prev_at=low_prev,
        adr20=adr,
        split_ratio_on_t=ratio,
        split_on_entry_day=ratio != 1.0,
    )


def compute_levels_table(series: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """Levels for every row in a candidate/feature table; rows without a peak get NaN levels
    and ``levels_error`` set. Basis/split errors on the series itself still raise."""
    _validate(series)
    out = []
    for _, row in rows.iterrows():
        try:
            d = compute_levels(series, row).to_dict()
            d["levels_error"] = None
        except LevelsError as e:
            d = {
                "ticker": row["ticker"],
                "decision_date": pd.Timestamp(row["date"]).date(),
                "asof_date": pd.Timestamp(row["asof_date"]).date(),
                "breakout_level_at": np.nan,
                "low_prev_at": np.nan,
                "levels_error": str(e),
            }
        out.append(d)
    return pd.DataFrame(out)


# --- comparison operators (STEP3_DESIGN §2, decision 8/13) -----------------------------------


def breakout_triggered(high: float, level: float) -> bool:
    """Spec §4: strictly above the level; exact touch does not count."""
    return bool(high > level)


def stop_hit(low: float, stop: float) -> bool:
    """Touching the stop is enough (conservative against us)."""
    return bool(low <= stop)


def gap_exceeds(open_: float, level: float, max_gap_entry: float) -> bool:
    """Spec §5.1.4: refuse when the open is strictly above level * (1 + max_gap_entry)."""
    return bool(open_ > level * (1.0 + max_gap_entry))
