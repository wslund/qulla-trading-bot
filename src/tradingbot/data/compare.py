"""Compare the same ticker from two sources on overlapping dates."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class CompareReport:
    ticker: str
    source_a: str
    source_b: str
    common_days: int
    only_in_a: int
    only_in_b: int
    close_max_rel_diff: float
    close_mean_rel_diff: float
    volume_median_rel_diff: float
    close_days_over_tol: int
    dates_over_tol: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.ticker} {self.source_a} vs {self.source_b}: common={self.common_days} "
            f"onlyA={self.only_in_a} onlyB={self.only_in_b} "
            f"close max={self.close_max_rel_diff:.2%} mean={self.close_mean_rel_diff:.3%} "
            f"volume median={self.volume_median_rel_diff:.1%} "
            f"days>tol={self.close_days_over_tol}"
        )


def compare_series(
    a: pd.DataFrame, b: pd.DataFrame, *, column: str = "close", tol: float = 0.005
) -> CompareReport:
    """Relative differences on ``column`` (default as-traded close) over common dates."""
    ticker = str(a["ticker"].iloc[0])
    ia = a.set_index("date")
    ib = b.set_index("date")
    common = ia.index.intersection(ib.index)
    ca = ia.loc[common, column].to_numpy(float)
    cb = ib.loc[common, column].to_numpy(float)
    rel = np.abs(ca - cb) / np.where(cb == 0, np.nan, np.abs(cb))
    va = ia.loc[common, "volume"].to_numpy(float)
    vb = ib.loc[common, "volume"].to_numpy(float)
    vrel = np.abs(va - vb) / np.where(vb == 0, np.nan, vb)
    over = rel > tol
    return CompareReport(
        ticker=ticker,
        source_a=str(a["source"].iloc[0]),
        source_b=str(b["source"].iloc[0]),
        common_days=len(common),
        only_in_a=len(ia.index.difference(ib.index)),
        only_in_b=len(ib.index.difference(ia.index)),
        close_max_rel_diff=float(np.nanmax(rel)) if len(common) else float("nan"),
        close_mean_rel_diff=float(np.nanmean(rel)) if len(common) else float("nan"),
        volume_median_rel_diff=float(np.nanmedian(vrel)) if len(common) else float("nan"),
        close_days_over_tol=int(np.nansum(over)),
        dates_over_tol=[str(d) for d in np.asarray(common)[over][:10]],
    )
