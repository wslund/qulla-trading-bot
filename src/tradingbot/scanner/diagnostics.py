"""Diagnostic measures for setup review. None of these is a rule; none changes rule output.

- prior_move_detail: where the stored prior_move actually starts and peaks, recomputed with
  the spec §2 definition on bars <= T-1 and cross-checked against the stored value.
- extra_measures: close vs SMA10 and the 5-bar close change up to T-1.
- add_group_diagnostics: consecutive candidate rows sharing the same prior-move peak are one
  setup group (the same consolidation seen on successive days).

All computations use bars <= asof_date (T-1) only; tests mutate T and later to prove it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from tradingbot.scanner.features import FeatureParams, to_computation_basis

DIAGNOSTICS_VERSION = "1"


@dataclass
class PriorMoveDetail:
    start_date: date | None
    start_close: float | None  # chart (split-adjusted) units
    peak_date: date | None
    peak_close: float | None
    pct: float | None  # recomputed: peak / start - 1
    stored_pct: float | None
    window_start: date | None
    consistent: bool  # recomputed pct matches the stored feature (or both missing)


def prior_move_detail(
    series: pd.DataFrame, row: pd.Series, params: FeatureParams | None = None
) -> PriorMoveDetail:
    """Spec §2 start/peak for the decision row, using bars <= asof_date only."""
    p = params or FeatureParams()
    adj, _ = to_computation_basis(series)
    adj = adj.reset_index(drop=True)
    asof = pd.Timestamp(row["asof_date"]).date()
    upto = adj[adj["date"] <= asof]  # T-1 protection: nothing after asof is visible
    stored = row.get("prior_move_pct")
    stored = None if stored is None or pd.isna(stored) else float(stored)
    i = len(upto) - 1
    lo = i - p.prior_move_window + 1
    if i < 0 or lo < 0:
        return PriorMoveDetail(None, None, None, None, None, stored, None, stored is None)
    close = upto["close"].to_numpy(float)
    seg = close[lo : i + 1]
    k = int(np.argmax(seg))
    window_start = upto["date"].iloc[lo]
    if k == 0:
        return PriorMoveDetail(
            None,
            None,
            upto["date"].iloc[lo],
            float(seg[0]),
            None,
            stored,
            window_start,
            stored is None,
        )
    j = int(np.argmin(seg[:k]))
    pct = float(seg[k] / seg[j] - 1)
    consistent = stored is not None and abs(pct - stored) <= 1e-9 + 1e-6 * abs(stored)
    return PriorMoveDetail(
        start_date=upto["date"].iloc[lo + j],
        start_close=float(seg[j]),
        peak_date=upto["date"].iloc[lo + k],
        peak_close=float(seg[k]),
        pct=pct,
        stored_pct=stored,
        window_start=window_start,
        consistent=consistent,
    )


def extra_measures(series: pd.DataFrame, row: pd.Series) -> dict[str, float | None]:
    """close vs SMA10 (from the stored row) and 5-bar close change (bars <= asof)."""
    out: dict[str, float | None] = {"close_vs_sma10": None, "ret_5d": None}
    cp, s10 = row.get("close_prev"), row.get("sma10")
    if cp is not None and s10 is not None and not pd.isna(cp) and not pd.isna(s10) and s10:
        out["close_vs_sma10"] = float(cp) / float(s10) - 1
    adj, _ = to_computation_basis(series)
    asof = pd.Timestamp(row["asof_date"]).date()
    upto = adj[adj["date"] <= asof]["close"].to_numpy(float)
    if len(upto) >= 6:
        out["ret_5d"] = float(upto[-1] / upto[-6] - 1)
    return out


def add_group_diagnostics(cands: pd.DataFrame) -> pd.DataFrame:
    """Add setup_group_id / group_rank / group_size for candidate rows (others get NA).

    A group = candidate rows that are consecutive in the table and share the same
    prior_move_peak_date. Rule columns and is_candidate are not touched.
    """
    out = cands.copy()
    gid: list[str | None] = []
    current: str | None = None
    prev_cand = False
    prev_peak = None
    for is_c, peak, ticker, d in zip(
        out["is_candidate"].astype(bool),
        out["prior_move_peak_date"],
        out["ticker"],
        out["date"],
        strict=True,
    ):
        if is_c and prev_cand and peak == prev_peak:
            gid.append(current)
        elif is_c:
            current = f"{ticker}:peak{peak}:from{d}"  # run start date keeps ids unique
            gid.append(current)
        else:
            gid.append(None)
        prev_cand, prev_peak = is_c, peak
    out["setup_group_id"] = gid
    out["group_rank"] = (
        out.groupby("setup_group_id").cumcount().add(1).where(out["setup_group_id"].notna())
    )
    out["group_size"] = (
        out.groupby("setup_group_id")["date"].transform("size").where(out["setup_group_id"].notna())
    )
    out["diagnostics_version"] = DIAGNOSTICS_VERSION
    return out
