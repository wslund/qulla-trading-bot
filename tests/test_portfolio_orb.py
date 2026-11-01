"""P5: ORB as entry model in the portfolio engine (PORTFOLIO_DESIGN §4; D-016)."""

from dataclasses import replace

import pytest

from tradingbot.backtesting.ledger import check_identities
from tradingbot.backtesting.portfolio import PortfolioParams, run_portfolio
from tradingbot.backtesting.report import build_report, render_text
from tradingbot.backtesting.trades import STOP_GAP, STOP_INTRADAY, run_ticker
from tradingbot.risk.limits import LimitParams
from tradingbot.risk.sizing import RiskApprover, RiskParams
from tradingbot.signals.orb import NO_MINUTE_BARS, ORB_INCOMPLETE
from tradingbot.signals.state import Status

from .test_features import apply_split
from .test_orb import _orb_day, make_session
from .test_portfolio import TP, params, universe
from .test_risk import RISK_ONE, TIERED, acct
from .test_trades import RISE_THEN_FADE

SLIP = 0.001


def orb_params(**kw) -> PortfolioParams:
    return replace(params(**kw), entry_model="orb")


def loader(frames: dict):
    """frames: {(ticker, date): minute frame}; missing -> None (no_minute_bars)."""
    return lambda t, d: frames.get((t, d))


# --- equivalence with run_ticker ---


def test_orb_single_ticker_matches_run_ticker_when_limits_do_not_bind() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["SET"])
    level = f / (1 + SLIP)
    frames = {("SET", t0): _orb_day(level, t0, stop_min=40)}
    ref = run_ticker(
        series["SET"],
        cands["SET"],
        RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED),
        costs=TIERED,
        trigger=TP,
        minute_bars=loader(frames),
    )
    res = run_portfolio(series, cands, orb_params(), minute_bars=loader(frames))
    assert len(ref[0]) == len(res.trades) == 1
    a, b = ref[0][0], res.trades[0]
    for field in (
        "entry_model",
        "entry_date",
        "entry_fill",
        "shares",
        "shares_ordered",
        "initial_stop",
        "initial_risk_usd",
        "exit_date",
        "exit_price",
        "exit_reason",
        "bars_held",
        "pnl_gross_usd",
        "pnl_net_usd",
        "r_multiple",
        "cost_actual",
        "intraday",
    ):
        assert getattr(a, field) == getattr(b, field), field
    assert a.entry_model == "orb_entry" and a.exit_reason == STOP_INTRADAY
    assert {(r.date, r.reason) for r in ref[1]} == {(r.date, r.reason) for r in res.rejections}
    assert res.registry.status(("SET", b.peak_date)) == Status.CLOSED
    assert res.account.realized_usd == pytest.approx(b.pnl_net_usd) and res.account.holdings == {}
    check_identities(res.account)
    # The order was reserved on the daily plan and sized at 09:35 on the ORB plan.
    row = [r for r in res.reservations if r.get("kind") == "row"][0]
    assert row["stop_plan"] != pytest.approx(b.initial_stop)  # low[T−1] plan vs opening-range low
    assert (
        res.decisions[0]["shares"] == b.shares
        and res.decisions[0]["reservation_usd"] >= res.decisions[0]["capital_required"]
    )


def test_orb_position_continues_with_daily_exits_and_same_bar_ambiguity() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    level = f / (1 + SLIP)
    s = series["AAA"]
    i0 = int(s.index[s["date"] == t0][0])
    s.loc[i0 + 1, ["open", "high", "low", "close", "adj_close"]] = [
        0.9 * level
    ] * 5  # gap under stop
    frames = {("AAA", t0): _orb_day(level, t0), ("BBB", t0): _orb_day(level, t0, ambiguous=True)}
    res = run_portfolio(series, cands, orb_params(), minute_bars=loader(frames))
    by = {t.ticker: t for t in res.trades}
    assert by["AAA"].exit_reason == STOP_GAP and by["AAA"].bars_held == 1
    assert by["BBB"].ambiguous_same_day and by["BBB"].exit_reason == "ambiguous_same_day_stop"
    log = res.registry.log_frame()
    assert (log["to_status"] == "CLOSED").sum() == 2 and res.account.holdings == {}
    check_identities(res.account)


# --- reservation on the daily plan, ORB plan dearer -> reservation_exceeded (D-016) ---


def test_orb_plan_above_daily_reservation_is_reservation_exceeded() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    level = f / (1 + SLIP)
    # Opening range 3–4 % above the level with a tight range: trigger = orb_high (4 % dearer
    # than the daily plan) and stop = orb_low (1 % under it), so the position cap gives
    # 32 shares at the dearer price – more capital than the 33 × level reservation.
    hi = 1.04 * level
    bars = {k: (1.035 * level, hi, 1.03 * level, 1.035 * level) for k in range(5)}
    bars[6] = (1.035 * level, 1.05 * level, 1.032 * level, 1.045 * level)
    frames = {("AAA", t0): make_session(t0, bars, base=1.02 * level, band=0.002 * level)}
    wide = RiskParams(risk_per_trade=0.01, max_position_pct=0.3)  # cap binds for both plans
    ref = run_ticker(
        series["AAA"],
        cands["AAA"],
        RiskApprover(acct(sek=200_000.0), wide, TIERED),
        costs=TIERED,
        trigger=TP,
        minute_bars=loader(frames),
    )
    assert len(ref[0]) == 1 and ref[0][0].intraday["trigger"] == pytest.approx(hi)
    res = run_portfolio(series, cands, orb_params(risk=wide), minute_bars=loader(frames))
    assert [t for t in res.trades if t.entry_date == t0] == []
    day0 = [r for r in res.rejections if r.date == t0]
    assert day0[0].reason == "reservation_exceeded" and day0[0].stage == "risk"
    d = res.decisions[0]
    assert d["capital_required"] > d["reservation_usd"] and d["shares_ordered"] > 0
    assert res.registry.status(("AAA", day0[0].peak_date)) == Status.ARMED
    assert res.account.holdings == {} and res.account.reservations == {}
    rep = build_report(res)["summary"]
    assert (
        rep["limits"]["reservation_blocked_fills"] == 1
        and rep["limits"]["reservation_blocked_usd"] > 0
    )


# --- incomplete opening range and missing minute data are reported, never filled in ---


def test_orb_incomplete_and_no_minute_bars_ratios_per_ticker_year() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB", "CCC"])
    level = f / (1 + SLIP)
    full = _orb_day(level, t0)
    broken = full[full["ts_utc"] != full["ts_utc"].iloc[2]].reset_index(drop=True)  # 09:32 missing
    frames = {("AAA", t0): full, ("BBB", t0): broken}  # CCC: no minute data at all
    res = run_portfolio(series, cands, orb_params(), minute_bars=loader(frames))
    day0 = {r.ticker: r.reason for r in res.rejections if r.date == t0}
    assert day0 == {"BBB": ORB_INCOMPLETE, "CCC": NO_MINUTE_BARS}
    assert [t.ticker for t in res.trades if t.entry_date == t0] == ["AAA"]
    for t in ("BBB", "CCC"):
        assert res.registry.status((t, res.trades[0].peak_date)) == Status.ARMED
    orb = res.summary()["orb"]
    y = t0.year
    # BBB and CCC stay armed and are reserved again on later candidate days (no frames then).
    assert orb["attempts"] == len(res.orb_attempts) >= 3
    assert orb["outcomes"]["fill_candidate"] == 1 and orb["outcomes"][ORB_INCOMPLETE] == 1
    bbb, ccc, aaa = (orb["by_ticker_year"][f"{t}:{y}"] for t in ("BBB", "CCC", "AAA"))
    assert bbb["orb_incomplete"] == 1 and bbb["orb_incomplete_ratio"] == 1 / bbb["attempts"]
    assert aaa["orb_incomplete_ratio"] == 0.0 and aaa["fill_candidate"] == 1
    assert ccc["no_minute_bars"] == ccc["attempts"] >= 1 and ccc["orb_incomplete"] == 0
    text = render_text(build_report(res)["summary"])
    assert f"ORB: {orb['attempts']} försök" in text and f"BBB:{y}" in text


# --- split day: positions adjusted before intraday, plans never twice ---


def test_orb_split_on_entry_day_uses_t_units_once() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    level = f / (1 + SLIP)
    s = series["AAA"]
    i0 = int(s.index[s["date"] == t0][0])
    split = {"AAA": apply_split(s, at=i0, ratio=4.0)}  # 4:1 on the entry day
    frames_a = {("AAA", t0): _orb_day(level, t0, stop_min=40)}
    frames_b = {("AAA", t0): _orb_day(level / 4, t0, stop_min=40)}  # minute bars in new units
    a = run_portfolio(series, cands, orb_params(), minute_bars=loader(frames_a)).trades[0]
    res_b = run_portfolio(split, cands, orb_params(), minute_bars=loader(frames_b))
    b = res_b.trades[0]
    assert b.entry_fill == pytest.approx(a.entry_fill / 4) and b.initial_stop == pytest.approx(
        a.initial_stop / 4
    )
    assert b.intraday["trigger"] == pytest.approx(a.intraday["trigger"] / 4)
    assert abs(b.shares - 4 * a.shares) <= 1 and b.r_multiple == pytest.approx(
        a.r_multiple, rel=0.05
    )
    row = [r for r in res_b.reservations if r.get("kind") == "row"][0]
    assert row["fill_est"] == pytest.approx(
        level / 4 * (1 + SLIP)
    )  # reservation already in T units
    ev = res_b.account.ledger.events_frame()
    assert "split_reservation" not in set(ev["kind"]) and "split" not in set(
        ev["kind"]
    )  # no holding yet
    check_identities(res_b.account)


# --- order invariance and the daily model unchanged ---


def test_orb_portfolio_is_order_invariant_and_daily_default_untouched() -> None:
    tickers = ["CCC", "AAA", "BBB"]
    series, cands, t0, f = universe(RISE_THEN_FADE, tickers)
    level = f / (1 + SLIP)
    frames = {(t, t0): _orb_day(level, t0, stop_min=40 if t == "BBB" else None) for t in tickers}
    lim = LimitParams(max_open_positions=2)
    r1 = run_portfolio(series, cands, orb_params(limits=lim), minute_bars=loader(frames))
    r2 = run_portfolio(
        {t: series[t] for t in reversed(tickers)},
        {t: cands[t] for t in reversed(tickers)},
        orb_params(limits=lim),
        minute_bars=loader(frames),
    )
    assert r1.account.ledger.events_frame().equals(r2.account.ledger.events_frame())
    assert [t.to_row() for t in r1.trades] == [t.to_row() for t in r2.trades]
    assert sorted(t.ticker for t in r1.trades if t.entry_date == t0) == [
        "AAA",
        "BBB",
    ]  # D-015 baseline
    assert {r.ticker: r.reason for r in r1.rejections if r.date == t0} == {"CCC": "max_positions"}
    # Daily model: explicit "daily" equals the default, and ignores the minute loader.
    d1 = run_portfolio(series, cands, params(limits=lim))
    d2 = run_portfolio(
        series, cands, replace(params(limits=lim), entry_model="daily"), minute_bars=loader(frames)
    )
    assert d1.ledger_frame().equals(d2.ledger_frame()) and [t.to_row() for t in d1.trades] == [
        t.to_row() for t in d2.trades
    ]
    assert (
        all(t.entry_model == "daily_stop_entry" for t in d1.trades)
        and d1.summary()["orb"]["attempts"] == 0
    )
    with pytest.raises(ValueError, match="minute_bars"):
        run_portfolio(series, cands, orb_params())
    with pytest.raises(ValueError, match="entry_model"):
        replace(params(), entry_model="vwap")


def test_candidate_day_definitions_are_reported_separately() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    res = run_portfolio(series, cands, params())
    c = build_report(res)["summary"]["candidates"]
    rows = sum(int(cf["is_candidate"].astype(bool).sum()) for cf in cands.values())
    assert c["candidate_rows"] == rows and c["rows_off_calendar"] == 0
    # Two tickers share every candidate session: distinct days = rows / 2.
    assert c["candidate_days_distinct"] == rows // 2
    # Reservable days exclude sessions where both rows failed at the registry (key consumed).
    assert c["days_with_reservable_candidates"] <= c["candidate_days_distinct"]
    assert c["days_with_reservable_candidates"] == 1  # only T0 before the keys are consumed


def test_cli_entry_model_orb_reads_stored_minute_bars(tmp_path) -> None:
    import json
    from datetime import UTC, datetime

    import pandas as pd

    from tradingbot.data.intraday import IntradayStore, session_report
    from tradingbot.data.schema import FetchMeta
    from tradingbot.data.store import ParquetStore
    from tradingbot.scanner.pipeline import build_candidates, build_features

    from .test_report import _load_cli

    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    level = f / (1 + SLIP)
    store = ParquetStore(tmp_path)
    s = series["AAA"]
    meta = FetchMeta(
        source="test",
        ticker="AAA",
        requested_start=s["date"].iloc[0],
        requested_end=s["date"].iloc[-1],
        fetched_at=datetime.now(UTC),
        provider_version="x",
        adjustment_method="none",
        actual_start=s["date"].iloc[0],
        actual_end=s["date"].iloc[-1],
        rows=len(s),
        fetch_rows=len(s),
    )
    meta.quality_status = "OK"
    store.write_processed(meta, s)
    build_features(store, "test", "AAA")
    build_candidates(store, "test", "AAA")
    frame = _orb_day(level, t0, stop_min=40)
    rep = session_report("AAA", t0, frame, pages=1, fetched_at=pd.Timestamp.now("UTC"))
    IntradayStore(tmp_path).write(rep, frame)
    cli = _load_cli()
    out = cli.main(
        [
            "--source",
            "test",
            "--tickers",
            "AAA",
            "--equity-sek",
            "200000",
            "--risk",
            "0.01",
            "--slippage",
            str(TP.slippage_bps),
            "--entry-model",
            "orb",
            "--run-id",
            "orb1",
            "--data-dir",
            str(tmp_path),
        ]
    )
    summary = json.loads((out / "summary.json").read_text())
    assert summary["entry_model"] == "orb" and summary["performance"]["all_trades"]["n"] == 1
    assert summary["orb"]["attempts"] >= 1 and summary["orb"]["outcomes"]["fill_candidate"] == 1
    trades = pd.read_parquet(out / "trades.parquet")
    assert (
        trades.loc[0, "entry_model"] == "orb_entry"
        and trades.loc[0, "exit_reason"] == STOP_INTRADAY
    )
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["params"]["entry_model"] == "orb" and cfg["args"]["entry_model"] == "orb"
