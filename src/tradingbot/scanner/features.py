"""Historical features for the rule-based scanner (STRATEGY_SPEC v0.2.1 §2–3, step 1).

Point-in-time convention (spec §11.2): the feature row dated T describes what was known
before the open of T, i.e. it uses bars up to and including T-1 only. Every feature is
either a ratio or expressed in as-traded units of T-1, so backward split adjustment of
earlier bars (which depends on *later* splits) cannot leak information: a split on T or
later rescales all bars <= T-1 uniformly and leaves every feature for T unchanged.
tests/test_features.py proves this by mutating T and later.

Price basis: computations run on a split-adjusted series. An as_traded frame is rebased
with its own split_ratio column; if that column is unknown (NaN, e.g. Alpaca raw) the
computation is refused unless splits are attached first (attach_splits). A split_adjusted
frame is used as is. total_return frames are refused (dividend-adjusted levels).

No trigger, no outcome, no position logic here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from tradingbot.data.compare import rebase
from tradingbot.data.schema import AS_TRADED, SPLIT_ADJUSTED

FEATURE_VERSION = "1.1"  # 1.1: added dollar_volume_20d (additive; existing formulas unchanged)
SPEC_VERSION = "0.2.1"


class FeatureError(ValueError):
    """Input cannot be used for features (basis, missing splits, schema)."""


@dataclass(frozen=True)
class FeatureParams:
    """Window lengths from the spec. Thresholds (min_prior_move etc.) belong to step 2."""

    sma_windows: tuple[int, ...] = (10, 20, 50)
    slope_lookback: int = 10  # spec §2: SMA50[T-1] > SMA50[T-11]
    adr_window: int = 20
    prior_move_window: int = 63  # spec §2
    consol_min_days: int = 12  # spec §3: thirds below 4 days make range5 meaningless
    consol_max_days: int = 45
    range_window: int = 5  # spec §3 range5


FEATURE_COLUMNS = [
    "date",  # decision day T (features use bars <= T-1)
    "asof_date",  # T-1, the last bar used
    "ticker",
    "source",
    "input_price_basis",
    "close_prev",  # close[T-1] in the input basis (as-traded when input is as_traded)
    "sma10",
    "sma20",
    "sma50",  # SMA levels in the same units as close_prev
    "sma10_slope",
    "sma20_slope",
    "sma50_slope",  # SMA[T-1] / SMA[T-1-slope_lookback] - 1
    "close_vs_sma20",
    "close_vs_sma50",  # close[T-1] / SMA - 1
    "adr20",  # mean(high/low - 1) over adr_window bars
    "dollar_volume_20d",  # median(close * volume) over adr_window bars, as-traded units
    "prior_move_pct",  # spec §2: window max / min-before-max - 1
    "prior_move_peak_date",
    "consol_days",  # bars after the peak up to T-1
    "consol_tightness",  # median(range5 last third) / median(range5 first third)
    "consol_higher_lows",
    "consol_depth",  # 1 - min(close in window) / peak close
    "consol_pct_below_high",  # 1 - close[T-1] / max(high in window)
    "consol_closes_below_sma20",
    "spec_version",
    "feature_version",
]


def attach_splits(target: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    """Copy split_ratio (by date) from ``source`` into an as_traded ``target`` lacking it.

    Refuses unless both are the same ticker and ``source`` has known splits covering the
    whole date range of ``target``. Dates present in target but not in source get 1.0 only
    when they lie inside source's coverage (a split on a missing date would be lost, so
    that case is refused too).
    """
    if str(target["ticker"].iloc[0]) != str(source["ticker"].iloc[0]):
        raise FeatureError("attach_splits: different tickers")
    if source["split_ratio"].isna().any():
        raise FeatureError("attach_splits: source split_ratio contains NaN")
    t_dates = pd.to_datetime(target["date"])
    s_dates = pd.to_datetime(source["date"])
    if t_dates.min() < s_dates.min() or t_dates.max() > s_dates.max():
        raise FeatureError("attach_splits: source does not cover target's date range")
    missing = set(s_dates[source["split_ratio"] != 1.0]) - set(t_dates)
    if missing:
        raise FeatureError(f"attach_splits: split dates absent in target: {sorted(missing)[:5]}")
    splits = pd.Series(source["split_ratio"].to_numpy(float), index=s_dates)
    out = target.copy()
    out["split_ratio"] = splits.reindex(t_dates).fillna(1.0).to_numpy()
    out["dividend"] = out["dividend"].fillna(0.0) if "dividend" in out else 0.0
    return out


def to_computation_basis(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Return (split-adjusted frame for computation, input basis)."""
    basis = str(df["price_basis"].iloc[0])
    if basis == SPLIT_ADJUSTED:
        return df.copy(), basis
    if basis == AS_TRADED:
        if df["split_ratio"].isna().any():
            raise FeatureError(
                "as_traded series without known split_ratio; attach splits from a source "
                "that has them (attach_splits) before computing features"
            )
        return rebase(df, SPLIT_ADJUSTED), basis
    raise FeatureError(f"price_basis {basis!r} not supported for features")


def compute_features(df: pd.DataFrame, params: FeatureParams | None = None) -> pd.DataFrame:
    """One feature row per bar date T (T = the bar after the last bar used).

    The last input bar yields a row for the *next* trading day, whose date is unknown
    without a calendar, so the output has exactly len(df) - 1 rows: row i is dated
    df.date[i+1] and uses bars 0..i.
    """
    p = params or FeatureParams()
    if len(df) < 2:
        return pd.DataFrame(columns=FEATURE_COLUMNS)
    if not df["date"].is_monotonic_increasing or df["date"].duplicated().any():
        raise FeatureError("dates must be sorted and unique")
    adj, input_basis = to_computation_basis(df)
    adj = adj.reset_index(drop=True)
    close = adj["close"].to_numpy(float)
    high = adj["high"].to_numpy(float)
    low = adj["low"].to_numpy(float)
    n = len(adj)
    # Scale from computation basis back to input units at each bar (identity if same basis).
    scale = df["close"].to_numpy(float) / close

    s = pd.Series(close)
    smas = {w: s.rolling(w).mean().to_numpy() for w in p.sma_windows}
    slopes = {w: smas[w] / np.roll(smas[w], p.slope_lookback) - 1 for w in p.sma_windows}
    for w in p.sma_windows:
        slopes[w][: p.slope_lookback] = np.nan
    adr = pd.Series(high / low - 1).rolling(p.adr_window).mean().to_numpy()
    # close * volume is invariant to split adjustment, so the input frame can be used directly.
    dollar_volume = (
        pd.Series(df["close"].to_numpy(float) * df["volume"].to_numpy(float))
        .rolling(p.adr_window)
        .median()
        .to_numpy()
    )
    range5 = (
        pd.Series(high).rolling(p.range_window).max().to_numpy()
        - pd.Series(low).rolling(p.range_window).min().to_numpy()
    ) / close

    rows = []
    for i in range(n - 1):  # i = index of T-1; the row is dated df.date[i+1]
        pm, peak_idx = _prior_move(close, i, p.prior_move_window)
        cons = _consolidation(close, high, low, range5, smas[20], peak_idx, i, p)
        rows.append(
            {
                "date": df["date"].iloc[i + 1],
                "asof_date": df["date"].iloc[i],
                "ticker": df["ticker"].iloc[0],
                "source": df["source"].iloc[0],
                "input_price_basis": input_basis,
                "close_prev": close[i] * scale[i],
                "sma10": smas[10][i] * scale[i],
                "sma20": smas[20][i] * scale[i],
                "sma50": smas[50][i] * scale[i],
                "sma10_slope": slopes[10][i],
                "sma20_slope": slopes[20][i],
                "sma50_slope": slopes[50][i],
                "close_vs_sma20": close[i] / smas[20][i] - 1,
                "close_vs_sma50": close[i] / smas[50][i] - 1,
                "adr20": adr[i],
                "dollar_volume_20d": dollar_volume[i],
                "prior_move_pct": pm,
                "prior_move_peak_date": df["date"].iloc[peak_idx] if peak_idx is not None else None,
                **cons,
                "spec_version": SPEC_VERSION,
                "feature_version": FEATURE_VERSION,
            }
        )
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)


def _prior_move(close: np.ndarray, i: int, window: int) -> tuple[float, int | None]:
    """Spec §2: max(close in window) / min(close in window before the max) - 1."""
    lo = i - window + 1
    if lo < 0:
        return np.nan, None
    seg = close[lo : i + 1]
    k = int(np.argmax(seg))
    if k == 0:
        return np.nan, lo  # peak at the window start: no 'before' segment
    return float(seg[k] / seg[:k].min() - 1), lo + k


def _consolidation(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    range5: np.ndarray,
    sma20: np.ndarray,
    peak_idx: int | None,
    i: int,
    p: FeatureParams,
) -> dict:
    """Spec §3 measures over the window (peak_idx, i]. NaN when the window is too short."""
    out = {
        "consol_days": np.nan,
        "consol_tightness": np.nan,
        "consol_higher_lows": None,
        "consol_depth": np.nan,
        "consol_pct_below_high": np.nan,
        "consol_closes_below_sma20": np.nan,
    }
    if peak_idx is None:
        return out
    a, b = peak_idx + 1, i + 1  # window is bars a..b-1
    days = b - a
    out["consol_days"] = days
    if days < 3:
        return out
    peak = close[peak_idx]
    out["consol_depth"] = 1 - close[a:b].min() / peak
    out["consol_pct_below_high"] = 1 - close[i] / high[a:b].max()
    out["consol_closes_below_sma20"] = int(np.sum(close[a:b] < sma20[a:b]))
    third = days // 3
    first, last = slice(a, a + third), slice(b - third, b)
    out["consol_higher_lows"] = bool(low[last].min() >= low[first].min())
    if days >= p.consol_min_days:
        base = np.nanmedian(range5[first])
        out["consol_tightness"] = float(np.nanmedian(range5[last]) / base) if base > 0 else np.nan
    return out


def params_dict(p: FeatureParams) -> dict:
    return asdict(p)
