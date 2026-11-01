"""Step 3e: opening range breakout on minute bars (STEP3_DESIGN §5/§9/§11 'ORB')."""

import io
import json
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tradingbot.backtesting.trades import STOP_GAP, STOP_INTRADAY, run_ticker, summarize
from tradingbot.data.intraday import (
    IntradayStore,
    compare_fetches,
    expected_session_minutes,
    normalize_minute,
    session_bars,
    session_bounds,
    session_report,
)
from tradingbot.data.providers.alpaca import AlpacaError, AlpacaProvider
from tradingbot.risk.sizing import COST_TO_RISK, RiskApprover
from tradingbot.signals.daily_trigger import FILL_CANDIDATE, GAP_TOO_LARGE, TriggerParams
from tradingbot.signals.orb import (
    LAPSED,
    NO_MINUTE_BARS,
    ORB_INCOMPLETE,
    LookAheadError,
    OpeningRange,
    simulate_orb_day,
)
from tradingbot.signals.state import Status

from .test_alpaca import FakeHTTP, _Resp
from .test_daily_trigger import _series_with_breakout, sig
from .test_features import apply_split
from .test_risk import RISK_HALF, RISK_ONE, TIERED, acct

SLIP = 0.001
TP = TriggerParams(slippage_bps=10.0)
SUMMER = date(2024, 7, 15)  # 13:30Z open
WINTER = date(2024, 1, 16)  # 14:30Z open
DST_MONDAY = date(2024, 3, 11)  # first session after the spring shift
HALF_DAY = date(2024, 11, 29)  # 13:00 ET close


def make_session(
    session_date: date,
    bars: dict[int, tuple],
    *,
    base: float = 99.0,
    band: float = 0.2,
    drop=(),
    premarket=False,
    close_bar=False,
) -> pd.DataFrame:
    """One minute bar per session minute: flat at ``base`` ± band, overridden per minute index
    by ``bars[k] = (open, high, low, close)``. Optional pre-market and 16:00 bars (out of
    session) to prove the filter."""
    o, c = session_bounds(session_date)
    n = int((c - o).total_seconds() // 60)
    rows = []

    def row(t, ohlc, in_session):
        op, hi, lo, cl = ohlc
        return dict(
            ts_utc=t,
            ts_ny=t.tz_convert("America/New_York"),
            open=op,
            high=hi,
            low=lo,
            close=cl,
            volume=1000.0,
            n=10.0,
            vw=cl,
            in_session=in_session,
        )

    if premarket:
        rows.append(row(o - timedelta(minutes=90), (base, base * 1.2, base, base), False))
    for k in range(n):
        if k in drop:
            continue
        ohlc = bars.get(k, (base, base + band, base - band, base))
        rows.append(row(o + timedelta(minutes=k), ohlc, True))
    if close_bar:
        rows.append(row(c, (base, base * 1.2, base * 0.8, base), False))
    return pd.DataFrame(rows)


# Opening range 09:30–09:34: highs up to 100.8 (above level 100), lows down to 98.5.
OR_BARS = {
    0: (99.0, 100.2, 98.8, 99.5),
    1: (99.5, 100.8, 99.0, 100.0),
    2: (100.0, 100.5, 98.5, 99.0),
    3: (99.0, 99.8, 98.7, 99.2),
    4: (99.2, 100.0, 98.9, 99.6),
}
TRIGGER = 100.8
STOP_EXPECTED = 98.5  # OR low; fill × (1 − 5 %) is lower


def run(session_date=SUMMER, bars=None, signal=None, **kw):
    s = make_session(session_date, bars if bars is not None else OR_BARS, **kw)
    o, c = session_bounds(session_date)
    return simulate_orb_day(signal or sig(), session_bars(s), o, c)


# --- opening range and trigger ---


def test_opening_range_values_trigger_and_no_fill_inside_range() -> None:
    bars = {**OR_BARS, 3: (99.0, 103.0, 98.7, 99.2)}  # price through the level inside the OR
    out = run(bars=bars)
    assert out.orb_high == 103.0 and out.orb_low == 98.5 and out.trigger == 103.0
    assert out.code == LAPSED  # never reached again after the range -> no fill (§5.1.5)
    low_or = {
        **OR_BARS,
        0: (99.0, 99.8, 98.8, 99.5),
        1: (99.5, 99.9, 99.0, 99.5),
        2: (99.5, 99.7, 98.5, 99.0),
    }  # orb_high < level -> trigger = level
    assert run(bars={**low_or, 10: (99.0, 100.5, 98.9, 100.2)}).trigger == 100.0


def test_opening_range_cannot_be_read_before_complete() -> None:
    o, _ = session_bounds(SUMMER)
    orr = OpeningRange(o, 5)
    s = make_session(SUMMER, OR_BARS)
    for bar in list(s.itertuples(index=False))[:4]:
        orr.feed(bar)
        with pytest.raises(LookAheadError):
            _ = orr.high
    orr.feed(list(s.itertuples(index=False))[4])
    assert orr.high == TRIGGER and orr.low == STOP_EXPECTED
    # Bar 09:35 with a huge high does not change the range.
    out = run(bars={**OR_BARS, 5: (99.0, 200.0, 98.9, 150.0)})
    assert out.orb_high == TRIGGER and out.trigger == TRIGGER


@pytest.mark.parametrize("missing", [[2], [0], [1, 2, 3]])
def test_incomplete_opening_range_gives_no_entry(missing) -> None:
    out = run(bars={**OR_BARS, 10: (99.0, 101.5, 98.9, 101.0)}, drop=missing)
    assert out.code == ORB_INCOMPLETE and out.fill is None


def test_premarket_and_close_bars_are_outside_the_session() -> None:
    s = make_session(SUMMER, OR_BARS, premarket=True, close_bar=True)
    assert len(s) == 392 and int(s["in_session"].sum()) == 390
    o, c = session_bounds(SUMMER)
    out = simulate_orb_day(sig(), session_bars(s), o, c)
    assert out.orb_high == TRIGGER  # the 118.8 pre-market high is not in the range
    rep = session_report("SYN", SUMMER, s, pages=1, fetched_at=pd.Timestamp.now("UTC"))
    assert rep.n_raw == 392 and rep.n_session == 390 and rep.expected == 390
    assert rep.has_close_bar is True and rep.sparse_session is False


# --- fill, gap, stop ---


def test_fill_boundaries_and_initial_stop_from_bars_before_fill() -> None:
    bars = {**OR_BARS, 5: (100.0, TRIGGER, 99.0, 100.5), 6: (100.5, TRIGGER + 0.01, 99.5, 100.7)}
    out = run(bars=bars)
    assert out.code == FILL_CANDIDATE and out.fill_time == session_bounds(SUMMER)[0] + timedelta(
        minutes=6
    )
    assert out.fill.fill_price == pytest.approx(TRIGGER * (1 + SLIP))
    assert out.fill.stop_price == pytest.approx(STOP_EXPECTED) and out.fill.gap_fill is False
    assert out.fill.ambiguous_same_day is False and out.stopped_intraday is False
    assert (
        out.plan.fill_est == pytest.approx(TRIGGER * (1 + SLIP))
        and out.plan.stop_plan == STOP_EXPECTED
    )
    # Fill at 10:12 with lower lows after the range: stop = min(low 09:30 .. 10:11).
    later = {**OR_BARS, 20: (99.0, 99.5, 97.9, 99.0), 42: (100.0, 101.5, 99.5, 101.0)}
    out2 = run(bars=later)
    assert out2.fill.stop_price == pytest.approx(97.9)
    # The fill bar's own low never defines the stop: a deep low there means ambiguous.
    out3 = run(bars={**OR_BARS, 5: (100.0, 101.5, 97.0, 101.0)})
    assert out3.fill.stop_price == pytest.approx(STOP_EXPECTED) and out3.fill.ambiguous_same_day


def test_gap_fill_at_bar_open_and_first_bar_gap_rule() -> None:
    out = run(bars={**OR_BARS, 7: (101.5, 102.0, 101.0, 101.8)})
    assert out.fill.gap_fill and out.fill.fill_price == pytest.approx(101.5 * (1 + SLIP))
    at_limit = {**OR_BARS, 0: (105.0, 106.0, 104.5, 105.5), 8: (106.0, 107.0, 105.5, 106.5)}
    assert run(bars=at_limit).code == FILL_CANDIDATE  # open == level × 1.05 allowed
    over = {**at_limit, 0: (105.01, 106.0, 104.5, 105.5)}
    assert run(bars=over).code == GAP_TOO_LARGE


def test_same_bar_ambiguity_versus_later_unambiguous_stop() -> None:
    fill_then_stop = {**OR_BARS, 5: (100.0, 101.2, 99.8, 101.0), 9: (99.0, 99.2, 98.4, 98.6)}
    out = run(bars=fill_then_stop)
    assert out.fill.ambiguous_same_day is False and out.stopped_intraday is True
    assert out.stop_time == session_bounds(SUMMER)[0] + timedelta(minutes=9)
    assert out.stop_ref == pytest.approx(STOP_EXPECTED)  # touched: fills at the stop
    gap_down = {**OR_BARS, 5: (100.0, 101.2, 99.8, 101.0), 9: (98.0, 98.3, 97.5, 97.8)}
    assert run(bars=gap_down).stop_ref == 98.0  # bar opens below the stop: fills at its open
    # Nothing after an ambiguous fill bar is read (bar 9 would be a second stop otherwise).
    amb = run(bars={**fill_then_stop, 5: (100.0, 101.2, 98.0, 101.0)})
    assert amb.fill.ambiguous_same_day and amb.stopped_intraday is False and amb.bars_seen == 6


def test_lapse_at_latest_entry_time_and_half_day() -> None:
    o, c = session_bounds(SUMMER)
    latest = int((c - timedelta(minutes=30) - o).total_seconds() // 60)  # 15:30 ET
    assert run(bars={**OR_BARS, latest: (100.0, 102.0, 99.5, 101.5)}).code == LAPSED
    assert run(bars={**OR_BARS, latest - 1: (100.0, 102.0, 99.5, 101.5)}).code == FILL_CANDIDATE
    oh, ch = session_bounds(HALF_DAY)
    assert (
        expected_session_minutes(HALF_DAY) == 210 and ch.tz_convert("America/New_York").hour == 13
    )
    half_latest = 180  # 12:30 ET
    s = make_session(
        HALF_DAY, {**OR_BARS, half_latest: (100.0, 102.0, 99.5, 101.5)}, close_bar=True
    )
    assert simulate_orb_day(sig(), session_bars(s), oh, ch).code == LAPSED
    s2 = make_session(HALF_DAY, {**OR_BARS, half_latest - 1: (100.0, 102.0, 99.5, 101.5)})
    out = simulate_orb_day(sig(), session_bars(s2), oh, ch)
    assert out.code == FILL_CANDIDATE and out.fill_time.tz_convert("America/New_York").hour == 12


@pytest.mark.parametrize("day", [SUMMER, WINTER, DST_MONDAY])
def test_same_scenario_in_summer_winter_and_on_dst_shift(day) -> None:
    bars = {**OR_BARS, 6: (100.5, 101.2, 99.5, 100.7), 30: (99.0, 99.3, 98.2, 98.4)}
    out = run(session_date=day, bars=bars)
    o, _ = session_bounds(day)
    assert o.tz_convert("America/New_York").hour == 9 and o.hour in (13, 14)
    assert out.fill.fill_price == pytest.approx(TRIGGER * (1 + SLIP))
    assert out.fill.stop_price == pytest.approx(STOP_EXPECTED)
    assert (out.fill_time - o) == timedelta(minutes=6) and (out.stop_time - o) == timedelta(
        minutes=30
    )


def test_future_bars_change_the_intraday_stop_but_not_the_fill() -> None:
    bars = {**OR_BARS, 6: (100.5, 101.2, 99.5, 100.7)}
    base = run(bars=bars)
    mutated = run(bars={**bars, **{k: (50.0, 60.0, 40.0, 45.0) for k in range(7, 390)}})
    assert (base.fill, base.plan, base.trigger, base.fill_time) == (
        mutated.fill,
        mutated.plan,
        mutated.trigger,
        mutated.fill_time,
    )
    assert base.stopped_intraday is False and mutated.stopped_intraday is True


def test_empty_session_and_missing_minutes_are_reported() -> None:
    assert run(bars={}, drop=range(390)).code == NO_MINUTE_BARS
    s = make_session(SUMMER, OR_BARS, drop=range(100, 130))  # 30 minutes missing mid-day
    rep = session_report("SYN", SUMMER, s, pages=1, fetched_at=pd.Timestamp.now("UTC"))
    assert rep.n_session == 360 and rep.sparse_session is True
    o, c = session_bounds(SUMMER)
    assert simulate_orb_day(sig(), session_bars(s), o, c).code == LAPSED  # still evaluated


# --- integration: run_ticker with minute bars, registry, approver, daily exits from T+1 ---


def _loader(frames: dict[date, pd.DataFrame]):
    return lambda ticker, d: frames.get(d)


def _orb_day(level: float, session_date: date, *, fill_min=6, stop_min=None, ambiguous=False):
    """Session for the synthetic daily series: opening range below the level, breakout
    through max(orb_high, level) = level at ``fill_min``, optional unambiguous stop later."""
    f = level
    bars = {k: (0.985 * f, 0.99 * f, 0.98 * f, 0.985 * f) for k in range(5)}
    lo = 0.95 * f if ambiguous else 0.984 * f
    bars[fill_min] = (0.99 * f, 1.01 * f, lo, 1.005 * f)
    if stop_min:
        bars[stop_min] = (0.99 * f, 0.995 * f, 0.975 * f, 0.98 * f)
    return make_session(session_date, bars, base=0.985 * f, band=0.002 * f)


def test_run_ticker_orb_fill_is_sized_on_trigger_and_traced() -> None:
    df, cands, t0, level = _series_with_breakout()
    frames = {t0: _orb_day(level, t0)}
    approver = RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED)
    trades, rej, reg = run_ticker(
        df, cands, approver, costs=TIERED, trigger=TP, minute_bars=_loader(frames)
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_model == "orb_entry" and t.entry_date == t0
    assert t.entry_fill == pytest.approx(level * (1 + SLIP))  # orb_high < level -> trigger = level
    assert t.initial_stop == pytest.approx(0.98 * level)  # opening-range low
    assert t.intraday["trigger"] == pytest.approx(level) and t.intraday["orb_low"] == pytest.approx(
        0.98 * level
    )
    d = approver.decisions[0]
    assert d.approved and getattr(d, "shares", 0) == t.shares
    # Sized on the ORB plan (2 % stop) rather than the daily low_prev stop.
    assert d.risk_amount / (level * (1 + SLIP) - 0.98 * level) >= t.shares
    row = t.to_row()
    assert row["entry_model"] == "orb_entry" and row["orb_trigger"] == pytest.approx(level)
    # Later candidate days without minute bars are reported, not crashed, and the key is used.
    assert {r.reason for r in rej if r.date > t0} <= {"key_consumed", NO_MINUTE_BARS}
    assert reg.status(("SET", t.peak_date)) in (Status.TRIGGERED, Status.CLOSED)
    assert summarize(trades, rej)["entry_models"] == {"orb_entry": 1}


def test_run_ticker_orb_risk_rejection_keeps_key_armed() -> None:
    df, cands, t0, level = _series_with_breakout()
    approver = RiskApprover(acct(), RISK_HALF, TIERED)  # 10 000 SEK, 0.5 %: cost_to_risk
    trades, rej, reg = run_ticker(
        df,
        cands,
        approver,
        costs=TIERED,
        trigger=TP,
        minute_bars=_loader({t0: _orb_day(level, t0)}),
    )
    assert trades == [] and [r.reason for r in rej if r.date == t0] == [COST_TO_RISK]
    assert reg.status(("SET", rej[0].peak_date)) == Status.ARMED and reg.consumed == {}
    # Days without minute bars: no_minute_bars, still armed, approver never called.
    assert sum(r.reason == NO_MINUTE_BARS for r in rej) >= 1 and len(approver.decisions) == 1


def test_run_ticker_orb_same_day_stops_close_registry_before_next_session() -> None:
    df, cands, t0, level = _series_with_breakout()
    approver = RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED)
    # Unambiguous intraday stop at minute 40.
    trades, _, reg = run_ticker(
        df,
        cands,
        approver,
        costs=TIERED,
        trigger=TP,
        minute_bars=_loader({t0: _orb_day(level, t0, stop_min=40)}),
    )
    t = trades[0]
    assert t.exit_reason == STOP_INTRADAY and t.exit_date == t0 and not t.ambiguous_same_day
    assert t.exit_price == pytest.approx(0.98 * level * (1 - SLIP))
    log = reg.log_frame()
    assert (log["to_status"] == "CLOSED").sum() == 1 and reg.open_positions == {}
    assert -1.6 < t.r_multiple < -1.0
    # Ambiguous same bar: 3c's path, one close, flagged.
    trades2, _, reg2 = run_ticker(
        df,
        cands,
        RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED),
        costs=TIERED,
        trigger=TP,
        minute_bars=_loader({t0: _orb_day(level, t0, ambiguous=True)}),
    )
    t2 = trades2[0]
    assert t2.ambiguous_same_day and t2.exit_reason == "ambiguous_same_day_stop"
    assert (reg2.log_frame()["to_status"] == "CLOSED").sum() == 1


def test_run_ticker_orb_position_continues_with_daily_exits() -> None:
    df, cands, t0, level = _series_with_breakout()
    i0 = int(df.index[df["date"] == t0][0])
    df.loc[i0 + 1, ["open", "high", "low", "close", "adj_close"]] = [
        0.9 * level
    ] * 5  # gap under stop
    cands = cands  # unchanged rows up to T0; later rows irrelevant (key consumed)
    approver = RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED)
    trades, _, _ = run_ticker(
        df,
        cands,
        approver,
        costs=TIERED,
        trigger=TP,
        minute_bars=_loader({t0: _orb_day(level, t0)}),
    )
    t = trades[0]
    assert t.entry_model == "orb_entry" and t.exit_reason == STOP_GAP and t.bars_held == 1
    assert t.exit_price == pytest.approx(0.9 * level * (1 - SLIP))


def test_split_on_entry_day_scales_orb_decisions() -> None:
    df, cands, t0, level = _series_with_breakout()
    i0 = int(df.index[df["date"] == t0][0])
    split = apply_split(df, at=i0, ratio=4.0)
    mk = lambda lvl: _orb_day(lvl, t0, stop_min=40)  # noqa: E731
    a = run_ticker(
        df,
        cands,
        RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED),
        costs=TIERED,
        trigger=TP,
        minute_bars=_loader({t0: mk(level)}),
    )[0][0]
    b = run_ticker(
        split,
        cands,
        RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED),
        costs=TIERED,
        trigger=TP,
        minute_bars=_loader({t0: mk(level / 4)}),
    )[0][0]
    assert b.entry_fill == pytest.approx(a.entry_fill / 4) and b.initial_stop == pytest.approx(
        a.initial_stop / 4
    )
    assert b.intraday["trigger"] == pytest.approx(a.intraday["trigger"] / 4)
    assert abs(b.shares - 4 * a.shares) <= 1  # 25 % cap rounds on post-split prices
    assert b.r_multiple == pytest.approx(a.r_multiple, rel=0.05)  # per-share commission differs


def test_daily_and_orb_models_are_comparable_on_the_same_candidate_day() -> None:
    df, cands, t0, level = _series_with_breakout()
    mk = lambda: RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED)  # noqa: E731
    daily = run_ticker(df, cands, mk(), costs=TIERED, trigger=TP)[0][0]
    orb = run_ticker(
        df, cands, mk(), costs=TIERED, trigger=TP, minute_bars=_loader({t0: _orb_day(level, t0)})
    )[0][0]
    assert daily.entry_date == orb.entry_date == t0 and daily.peak_date == orb.peak_date
    assert daily.entry_model == "daily_stop_entry" and orb.entry_model == "orb_entry"
    assert daily.entry_fill == pytest.approx(orb.entry_fill)  # both fill at the level here
    assert daily.initial_stop != pytest.approx(orb.initial_stop)  # low[T−1] vs opening-range low
    assert daily.intraday == {} and orb.intraday["fill_time"] is not None


# --- data layer: normalization, store, revisions, adapter ---


def _raw_day(session_date: date, start_hour_utc: int, n: int) -> pd.DataFrame:
    t0 = pd.Timestamp(session_date, tz="UTC") + timedelta(hours=start_hour_utc)
    ts = [t0 + timedelta(minutes=k) for k in range(n)]
    return pd.DataFrame(
        {
            "t": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in ts],
            "o": 10.0,
            "h": 10.1,
            "l": 9.9,
            "c": 10.0,
            "v": 100,
            "n": 5,
            "vw": 10.0,
        }
    )


def test_normalize_flags_session_for_whole_utc_day_dumps() -> None:
    sivb_like = normalize_minute(_raw_day(date(2023, 3, 9), 0, 1440), date(2023, 3, 9))
    assert len(sivb_like) == 1440 and int(sivb_like["in_session"].sum()) == 390
    first_in = sivb_like[sivb_like["in_session"]].iloc[0]["ts_utc"]
    assert first_in == pd.Timestamp("2023-03-09 14:30", tz="UTC")
    aug = normalize_minute(_raw_day(date(2020, 8, 28), 13, 420), date(2020, 8, 28))
    assert aug[aug["in_session"]].iloc[0]["ts_utc"].hour == 13  # EDT
    assert int(aug["in_session"].sum()) == 390 and len(session_bars(aug)) == 390
    with pytest.raises(ValueError, match="not an XNYS session"):
        normalize_minute(_raw_day(date(2024, 7, 4), 13, 10), date(2024, 7, 4))


def test_store_is_append_only_and_detects_revisions(tmp_path) -> None:
    store = IntradayStore(tmp_path)
    a = normalize_minute(_raw_day(SUMMER, 13, 430), SUMMER)
    rep_a = session_report("SYN", SUMMER, a, pages=1, fetched_at=pd.Timestamp("2026-01-01T10:00Z"))
    store.write(rep_a, a)
    b = a.copy()
    b.loc[50, "high"] = 10.5
    b = b.drop(index=[60]).reset_index(drop=True)
    rep_b = session_report("SYN", SUMMER, b, pages=1, fetched_at=pd.Timestamp("2026-01-08T10:00Z"))
    store.write(rep_b, b)
    assert len(store.fetches("SYN", SUMMER)) == 2 and store.sessions("SYN") == [SUMMER]
    latest = store.read_latest("SYN", SUMMER)
    assert len(latest) == 429
    diff = compare_fetches(a, latest)
    assert diff["revised"] and len(diff["changed"]) == 1 and len(diff["only_in_old"]) == 1
    assert store.read_report(store.fetches("SYN", SUMMER)[0])["n_session"] == 390
    assert compare_fetches(a, a)["revised"] is False


class _RespH(_Resp):
    headers = {"X-RateLimit-Limit": "200", "X-RateLimit-Remaining": "199"}


def test_fetch_minute_request_window_pagination_and_rate_limit(monkeypatch) -> None:
    from tradingbot.data.providers import alpaca as mod

    page1 = {"bars": _raw_day(date(2020, 8, 28), 13, 3).to_dict("records"), "next_page_token": "T2"}
    page2 = {"bars": _raw_day(date(2020, 8, 28), 14, 2).to_dict("records"), "next_page_token": None}
    fake = FakeHTTP([page1, page2])
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=0: _wrap(fake, req))
    p = AlpacaProvider(
        "k", "s", sleep=lambda s: None, now=lambda: pd.Timestamp("2026-01-05T12:00Z")
    )
    raw = p.fetch_minute("aapl", date(2020, 8, 28))
    q = fake.requests[0]["query"]
    assert q["timeframe"] == "1Min" and q["start"] == "2020-08-28T13:30:00Z"
    assert q["end"] == "2020-08-28T20:01:00Z" and q["feed"] == "sip" and q["adjustment"] == "raw"
    assert fake.requests[1]["query"]["page_token"] == "T2" and len(raw.frame) == 5
    assert "pages=2" in raw.notes and p.last_rate_limit["X-RateLimit-Remaining"] == "199"
    assert "k" not in raw.provider_version.split() and "secret" not in raw.notes
    with pytest.raises(ValueError, match="not an XNYS session"):
        p.fetch_minute("AAPL", date(2020, 8, 29))
    with pytest.raises(AlpacaError, match="not available yet"):
        p.fetch_minute("AAPL", date(2026, 1, 5))


def _wrap(fake: FakeHTTP, req):
    resp = fake(req)
    out = _RespH(resp.getvalue())
    return out


def test_fetch_minute_retries_on_429(monkeypatch) -> None:
    from tradingbot.data.providers import alpaca as mod

    page = {"bars": _raw_day(date(2020, 8, 28), 13, 2).to_dict("records"), "next_page_token": None}
    fake = FakeHTTP([(429, b"slow down"), page])
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    p = AlpacaProvider(
        "k", "s", sleep=lambda s: None, now=lambda: pd.Timestamp("2026-01-05T12:00Z")
    )
    assert len(p.fetch_minute("AAPL", date(2020, 8, 28)).frame) == 2 and len(fake.requests) == 2


def test_json_roundtrip_of_outcome_detail_keeps_no_secrets() -> None:
    out = run(bars={**OR_BARS, 6: (100.5, 101.2, 99.5, 100.7)})
    text = json.dumps(out.intraday_fields(), default=str)
    assert "trigger" in text and "secret" not in text
    assert replace(out, detail="x").detail == "x"
    assert np.isfinite(out.first_open)
    io.StringIO(text).read()  # noqa: F841 - sanity
