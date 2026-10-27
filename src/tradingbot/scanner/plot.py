"""Static setup charts for manual review of candidates and near misses.

The setup assessment on the chart uses only bars <= T-1: SMA lines, the consolidation
window, the prior-move peak and the breakout level stop at T-1. Bars from T on are drawn
greyed out for context only and are never used for any marked value. Feature values and
rule results are taken as stored (computed by features.py / rules.py); nothing is
recomputed here, so the chart cannot disagree with the scanner.

matplotlib is an optional dependency (``uv sync --extra viz``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from tradingbot.data.compare import rebase
from tradingbot.data.schema import AS_TRADED, SPLIT_ADJUSTED
from tradingbot.scanner.rules import RULE_NAMES

FEATURE_LINES = [
    ("prior_move_pct", "prior move", "{:.1%}"),
    ("consol_days", "consol days", "{:.0f}"),
    ("consol_tightness", "tightness", "{:.2f}"),
    ("consol_depth", "depth", "{:.1%}"),
    ("consol_pct_below_high", "below high", "{:.1%}"),
    ("consol_closes_below_sma20", "closes<SMA20", "{:.0f}"),
    ("close_vs_sma20", "close vs SMA20", "{:+.1%}"),
    ("close_vs_sma50", "close vs SMA50", "{:+.1%}"),
    ("sma50_slope", "SMA50 slope", "{:+.1%}"),
    ("adr20", "ADR20", "{:.1%}"),
    ("dollar_volume_20d", "$vol 20d", "{:,.0f}"),
]


@dataclass
class PlotInfo:
    """What was drawn; returned for tests and logs."""

    ticker: str
    decision_date: date
    asof_date: date
    bars_before: int  # bars <= T-1 drawn
    bars_after: int  # context bars >= T drawn (greyed)
    sma_last_date: date | None  # last date any SMA line reaches (must be <= asof_date)
    consol_start: date | None
    consol_end: date | None
    peak_date: date | None
    breakout_level: float | None
    rules_passed: int
    rules_total: int
    is_candidate: bool
    path: Path


def _series_for_chart(series: pd.DataFrame) -> pd.DataFrame:
    """Split-adjusted prices for a continuous chart (as-traded would jump at splits)."""
    basis = str(series["price_basis"].iloc[0])
    if basis == AS_TRADED and not series["split_ratio"].isna().any():
        return rebase(series, SPLIT_ADJUSTED)
    return series.copy()


def plot_setup(
    series: pd.DataFrame,
    row: pd.Series,
    out_path: Path,
    *,
    lookback: int = 120,
    lookahead: int = 20,
) -> PlotInfo:
    """Draw one decision row (features + rule columns) over its price history to ``out_path``.

    ``row`` must come from the candidates table (apply_rules output) so that rule results
    and feature values are the stored ones.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = _series_for_chart(series).reset_index(drop=True)
    dates = pd.to_datetime(s["date"])
    t = pd.Timestamp(row["date"])
    asof = pd.Timestamp(row["asof_date"])
    i_t = int(np.searchsorted(dates.to_numpy(), t.to_datetime64()))
    lo = max(0, i_t - lookback)
    hi = min(len(s), i_t + lookahead)
    before = s.iloc[lo:i_t]
    after = s.iloc[i_t:hi]
    # Scale factor: stored SMA levels are in as-traded units of T-1; the chart is split-adjusted.
    close_at_asof_chart = float(s["close"].iloc[i_t - 1])
    scale = close_at_asof_chart / float(row["close_prev"]) if row["close_prev"] else 1.0

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(14, 8.5), sharex=True, gridspec_kw={"height_ratios": [3.2, 1]}
    )
    _draw_bars(ax, before, color="black")
    _draw_bars(ax, after, color="0.7")
    axv.bar(pd.to_datetime(before["date"]), before["volume"], color="0.35", width=0.8)
    axv.bar(pd.to_datetime(after["date"]), after["volume"], color="0.8", width=0.8)

    # SMA lines strictly from bars <= T-1 (rolling over the full history, then cut).
    sma_last = None
    full_close = s["close"]
    for w, c in ((10, "tab:blue"), (20, "tab:orange"), (50, "tab:red")):
        sma = full_close.rolling(w).mean().iloc[lo:i_t]
        ax.plot(pd.to_datetime(before["date"]), sma, color=c, lw=1.1, label=f"SMA{w}")
        if sma.notna().any():
            sma_last = before["date"].iloc[-1]

    peak_date = row.get("prior_move_peak_date")
    consol_start = consol_end = None
    breakout = None
    if peak_date is not None and not pd.isna(peak_date):
        pk = pd.Timestamp(peak_date)
        i_pk = int(np.searchsorted(dates.to_numpy(), pk.to_datetime64()))
        if i_pk + 1 < i_t:
            win = s.iloc[i_pk + 1 : i_t]
            consol_start, consol_end = win["date"].iloc[0], win["date"].iloc[-1]
            breakout = float(win["high"].max())
            ax.axvspan(pd.Timestamp(consol_start), asof, color="tab:green", alpha=0.08, lw=0)
            ax.hlines(breakout, pd.Timestamp(consol_start), asof, colors="tab:green", ls="--", lw=1)
            ax.annotate(
                f"breakout level {breakout:.2f}",
                (pd.Timestamp(consol_start), breakout),
                xytext=(2, 3),
                textcoords="offset points",
                fontsize=8,
                color="tab:green",
            )
        ax.plot([pk], [float(s["high"].iloc[i_pk])], marker="v", color="tab:purple", ms=9)
        ax.annotate(
            "prior-move peak",
            (pk, float(s["high"].iloc[i_pk])),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color="tab:purple",
        )
    ax.axvline(t, color="tab:red", lw=1.2)
    ax.annotate(
        "T (decision)",
        (t, ax.get_ylim()[1]),
        xytext=(-4, -12),
        textcoords="offset points",
        ha="right",
        fontsize=8,
        color="tab:red",
    )

    passed, total = int(row["rules_passed"]), int(row["rules_total"])
    is_cand = bool(row["is_candidate"])
    rule_txt = []
    for n in RULE_NAMES:
        v = row.get(f"rule_{n}")
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
            continue
        rule_txt.append(f"{'PASS' if bool(v) else 'FAIL'}  {n}")
    feat_txt = []
    for col, label, fmt in FEATURE_LINES:
        v = row.get(col)
        feat_txt.append(f"{label}: " + ("n/a" if v is None or pd.isna(v) else fmt.format(v)))
    sma_txt = (
        f"close[T-1]={float(row['close_prev']):.2f} (chart x{scale:.4g})  "
        f"SMA10={float(row['sma10']):.2f} SMA20={float(row['sma20']):.2f} "
        f"SMA50={float(row['sma50']):.2f}"
    )
    box = "\n".join(rule_txt) + "\n\n" + "\n".join(feat_txt)
    ax.text(
        1.01,
        1.0,
        box,
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=8,
        family="monospace",
        bbox={"boxstyle": "round", "fc": "white", "ec": "0.6"},
    )
    kind = "CANDIDATE" if is_cand else f"near miss ({passed}/{total})"
    ax.set_title(
        f"{row['ticker']}  T={t.date()}  (as of {asof.date()})  {kind}   "
        f"source={row['source']} basis={row['input_price_basis']} "
        f"spec {row['spec_version']} rules {row.get('rules_version', '?')}\n{sma_txt}",
        fontsize=10,
        loc="left",
    )
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.25)
    axv.grid(alpha=0.25)
    axv.set_ylabel("volume")
    fig.autofmt_xdate()
    fig.subplots_adjust(right=0.78)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return PlotInfo(
        ticker=str(row["ticker"]),
        decision_date=t.date(),
        asof_date=asof.date(),
        bars_before=len(before),
        bars_after=len(after),
        sma_last_date=sma_last,
        consol_start=consol_start,
        consol_end=consol_end,
        peak_date=None
        if peak_date is None or pd.isna(peak_date)
        else pd.Timestamp(peak_date).date(),
        breakout_level=breakout,
        rules_passed=passed,
        rules_total=total,
        is_candidate=is_cand,
        path=out_path,
    )


def _draw_bars(ax, bars: pd.DataFrame, color: str) -> None:
    if bars.empty:
        return
    x = pd.to_datetime(bars["date"])
    ax.vlines(x, bars["low"], bars["high"], color=color, lw=1)
    ax.hlines(bars["open"], x - pd.Timedelta(hours=8), x, color=color, lw=1)
    ax.hlines(bars["close"], x, x + pd.Timedelta(hours=8), color=color, lw=1)


def select_rows(
    cands: pd.DataFrame,
    *,
    dates: list[date] | None = None,
    start: date | None = None,
    end: date | None = None,
    near_misses: bool = False,
) -> pd.DataFrame:
    """Rows to plot: explicit dates, or candidates (and optionally one-rule misses) in a range."""
    if dates:
        return cands[cands["date"].isin(dates)]
    sel = cands
    if start:
        sel = sel[sel["date"] >= start]
    if end:
        sel = sel[sel["date"] <= end]
    mask = sel["is_candidate"].astype(bool)
    if near_misses:
        mask = mask | (sel["rules_passed"] >= sel["rules_total"] - 1)
    return sel[mask]
