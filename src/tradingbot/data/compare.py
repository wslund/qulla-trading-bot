"""Compare the same ticker from two sources on overlapping dates, on one price basis."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingbot.data.schema import AS_TRADED, PRICE_COLUMNS, SPLIT_ADJUSTED, TOTAL_RETURN


def rebase(df: pd.DataFrame, basis: str) -> pd.DataFrame:
    """Return a copy with open/high/low/close/volume expressed on ``basis``.

    Only as_traded frames (with split_ratio and adj_factor) can be rebased; a frame already
    on the requested basis is returned unchanged. Anything else raises, because comparing
    across bases is meaningless.
    """
    current = str(df["price_basis"].iloc[0])
    if current == basis:
        return df.copy()
    if current != AS_TRADED:
        raise ValueError(f"cannot rebase {current} to {basis}: split/dividend data unavailable")
    out = df.copy()
    needed = "split_ratio" if basis == SPLIT_ADJUSTED else "adj_factor"
    if needed in out.columns and out[needed].isna().any():
        raise ValueError(f"cannot rebase to {basis}: {needed} unknown (NaN) for this source")
    if basis == SPLIT_ADJUSTED:
        ratio = out["split_ratio"].to_numpy(float)
        future = np.cumprod(ratio[::-1])[::-1]  # product of ratios at and after each bar
        future = np.append(future[1:], 1.0)  # strictly after
        factor = 1.0 / future
        vol_factor = future
    elif basis == TOTAL_RETURN:
        factor = out["adj_factor"].to_numpy(float)
        vol_factor = 1.0 / factor
    else:
        raise ValueError(f"unknown basis {basis!r}")
    for c in PRICE_COLUMNS:
        out[c] = out[c].to_numpy(float) * factor
    out["volume"] = out["volume"].to_numpy(float) * vol_factor
    out["price_basis"] = basis
    return out


@dataclass
class CompareReport:
    ticker: str
    source_a: str
    source_b: str
    basis: str
    common_days: int
    only_in_a: int
    only_in_b: int
    close_max_rel_diff: float
    close_mean_rel_diff: float
    volume_median_rel_diff: float
    volume_ratio_median: float  # median of b/a volume, signed view of systematic differences
    volume_ratio_iqr: tuple[float, float]  # 25th/75th percentile of b/a volume
    close_days_over_tol: int
    dates_over_tol: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.ticker} {self.source_a} vs {self.source_b} [{self.basis}]: "
            f"common={self.common_days} "
            f"onlyA={self.only_in_a} onlyB={self.only_in_b} "
            f"close max={self.close_max_rel_diff:.2%} mean={self.close_mean_rel_diff:.3%} "
            f"volume median={self.volume_median_rel_diff:.1%} "
            f"volB/volA median={self.volume_ratio_median:.3f} "
            f"IQR=[{self.volume_ratio_iqr[0]:.3f}, {self.volume_ratio_iqr[1]:.3f}] "
            f"days>tol={self.close_days_over_tol}"
        )


def compare_series(
    a: pd.DataFrame,
    b: pd.DataFrame,
    *,
    column: str = "close",
    tol: float = 0.005,
    basis: str | None = None,
    assume_b_basis: bool = False,
) -> CompareReport:
    """Relative differences on ``column`` over common dates, after rebasing both to ``basis``.

    ``basis`` defaults to b's basis. Raises if either frame cannot be expressed on it.
    With ``assume_b_basis=True`` b's numbers are taken as already being on ``basis`` (used
    to infer the undocumented basis of a source such as Stooq by trying each candidate).
    """
    basis = basis or str(b["price_basis"].iloc[0])
    a = rebase(a, basis)
    b = b.copy() if assume_b_basis else rebase(b, basis)
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
    vratio = vb / np.where(va == 0, np.nan, va)
    over = rel > tol
    has = len(common) > 0
    return CompareReport(
        ticker=ticker,
        source_a=str(a["source"].iloc[0]),
        source_b=str(b["source"].iloc[0]),
        basis=basis,
        common_days=len(common),
        only_in_a=len(ia.index.difference(ib.index)),
        only_in_b=len(ib.index.difference(ia.index)),
        close_max_rel_diff=float(np.nanmax(rel)) if len(common) else float("nan"),
        close_mean_rel_diff=float(np.nanmean(rel)) if len(common) else float("nan"),
        volume_median_rel_diff=float(np.nanmedian(vrel)) if has else float("nan"),
        volume_ratio_median=float(np.nanmedian(vratio)) if has else float("nan"),
        volume_ratio_iqr=(
            (float(np.nanpercentile(vratio, 25)), float(np.nanpercentile(vratio, 75)))
            if has
            else (float("nan"), float("nan"))
        ),
        close_days_over_tol=int(np.nansum(over)),
        dates_over_tol=[str(d) for d in np.asarray(common)[over][:10]],
    )
