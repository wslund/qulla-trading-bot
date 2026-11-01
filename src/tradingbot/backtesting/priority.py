"""Same-day priority of entry candidates (PORTFOLIO_DESIGN §6, D-015; P3).

Input: the candidates that the registry allowed for day T (one per ticker) with their daily
``Signal`` (levels from bars <= T−1) and the T−1 feature values of their candidate row.
Output: the same candidates in the order reservations are attempted. Every rule starts from
the canonical order (ticker, peak_date), so the result never depends on the order the
candidates arrived in.

  baseline            ticker alphabetically                          (D-015 main scenario)
  seeded_random       deterministic shuffle of the canonical order    (sensitivity measure)
  research_hypothesis consol_pct_below_high ↑, consol_tightness ↑, ticker – a pre-registered
                      P-002 hypothesis, never the default; requires the features to be
                      present and finite (no silent fallback to the baseline)

Features must be values of the candidate row for day T (``asof_date`` = T−1). Nothing from
day T (prices, fills, portfolio events) is accepted here – the caller passes a mapping taken
from the row, and the research rule refuses NaN/missing instead of guessing.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tradingbot.signals.daily_trigger import Signal

PRIORITY_VERSION = "1"
BASELINE = "baseline"
SEEDED_RANDOM = "seeded_random"
RESEARCH = "research_hypothesis"
RESEARCH_FEATURES: tuple[str, ...] = ("consol_pct_below_high", "consol_tightness")


@dataclass(frozen=True)
class Candidate:
    signal: Signal
    features: Mapping[str, float] = field(default_factory=dict)  # from the T row (asof T−1)
    sector: str | None = None

    @property
    def key(self) -> tuple:
        return self.signal.key

    @property
    def ticker(self) -> str:
        return self.signal.ticker


@dataclass(frozen=True)
class PriorityParams:
    mode: str = BASELINE
    seed: int | None = None  # required for seeded_random; pre-registered, never tuned
    features: tuple[str, ...] = RESEARCH_FEATURES  # research_hypothesis only

    def __post_init__(self) -> None:
        if self.mode not in (BASELINE, SEEDED_RANDOM, RESEARCH):
            raise ValueError(f"unknown priority mode {self.mode!r}")
        if self.mode == SEEDED_RANDOM and self.seed is None:
            raise ValueError("seeded_random needs a pre-registered seed")


@dataclass(frozen=True)
class Prioritized:
    candidate: Candidate
    rank: int  # 1 = first
    rule: str
    sort_key: tuple


def canonical(cands: Sequence[Candidate]) -> list[Candidate]:
    """Canonical order (ticker, peak_date): the input order is never observable afterwards."""
    out = sorted(cands, key=lambda c: (c.ticker, str(c.key[1])))
    tickers = [c.ticker for c in out]
    if len(set(tickers)) != len(tickers):
        raise ValueError("more than one candidate per ticker on the same day")
    return out


def prioritize(
    cands: Sequence[Candidate], params: PriorityParams | None = None
) -> list[Prioritized]:
    p = params or PriorityParams()
    base = canonical(cands)
    if p.mode == BASELINE:
        ordered = [(c, (c.ticker,)) for c in base]
    elif p.mode == SEEDED_RANDOM:
        idx = list(range(len(base)))
        random.Random(p.seed).shuffle(idx)
        ordered = [(base[i], (pos, base[i].ticker)) for pos, i in enumerate(idx)]
    else:
        ordered = sorted(
            ((c, (*_feature_values(c, p.features), c.ticker)) for c in base), key=lambda x: x[1]
        )
    return [Prioritized(c, i + 1, p.mode, k) for i, (c, k) in enumerate(ordered)]


def _feature_values(c: Candidate, names: tuple[str, ...]) -> tuple[float, ...]:
    vals = []
    for n in names:
        v = c.features.get(n)
        if v is None or not math.isfinite(float(v)):
            raise ValueError(f"{c.ticker}: research priority needs finite {n!r} from the T−1 row")
        vals.append(float(v))
    return tuple(vals)
