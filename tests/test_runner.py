"""
Tests for the pipeline split.

The point of splitting `full_run` from `render` is that recording a trade should not
cost a network round-trip. `test_render_never_touches_the_network` is what makes that
a guarantee rather than an intention -- without it, one lazily-added fetch inside a
render path would put forty seconds behind every button and nobody would notice until
they were offline.
"""
import sys

import numpy as np
import pandas as pd
import pytest

from market.regime import Regime, Signal
from runner import RunContext, render


@pytest.fixture
def ctx(settings_mock, tmp_path):
    """A context as `full_run` would leave it, without any fetching."""
    tickers = list(settings_mock.stock_tickers)
    rows = []
    for i, ticker in enumerate(tickers):
        rows.append({
            "ticker": ticker, "name": ticker, "sector": settings_mock.sectors[ticker],
            "undervaluation_score": 1.0 - i * 0.1, "composite_score": 1.0 - i * 0.1,
            "raw_score": 1.0 - i * 0.1,
            "last_close": 1000.0 + i * 100, "median_daily_value_rp": 5e9,
            "imputed_factors": "", "pe_ratio": 10.0 + i, "price_to_book": 1.0 + i * 0.1,
            "roe": 0.15, "unclipped_pe_ratio": 10.0 + i,
            "unclipped_price_to_book": 1.0 + i * 0.1,
        })
    df = pd.DataFrame(rows)

    idx = pd.date_range("2025-01-01", periods=300, freq="D")
    bench = pd.DataFrame({
        "Open": np.linspace(6000, 6500, 300), "High": np.linspace(6050, 6550, 300),
        "Low": np.linspace(5950, 6450, 300), "Close": np.linspace(6000, 6500, 300),
        "Volume": np.full(300, 1e9),
    }, index=idx)

    settings_mock.output_dir = tmp_path / "out"
    settings_mock.output_dir.mkdir(parents=True, exist_ok=True)
    settings_mock.account = {
        **settings_mock.account,
        "journal_path": str(tmp_path / "journal.csv"),
        "marks_path": str(tmp_path / "marks.csv"),
        "holdings_path": str(tmp_path / "holdings.yaml"),
        # Every file the API writes belongs under tmp_path. Leaving cash_path at
        # its default sent these tests at the repo's own data/cash.csv, where they
        # accumulated across the whole run and across each other.
        "cash_path": str(tmp_path / "cash.csv"),
        "dividends_path": str(tmp_path / "dividends.csv"),
        "snapshot_path": str(tmp_path / "run.joblib"),
        "capital_rp": 10_000_000,
    }
    settings_mock.events_path = str(tmp_path / "events.yaml")

    class Args:
        browser = True          # keeps the CLI fallback, no pywebview needed
        png = False

    return RunContext(
        settings=settings_mock, args=Args(),
        df=df, price_data={}, benchmark_data={"^JKSE": bench},
        regime=Regime([Signal("IHSG trend", "^JKSE", True, "above trend")],
                      1.0, "RISK-ON", "G", "Deploy 100%."),
    )


# --------------------------------------------------- the guarantee of the split
def test_render_never_touches_the_network(ctx, monkeypatch):
    """
    Recording a trade must not cost a fetch. Any DataFetcher constructed during a
    render is a forty-second regression hiding behind a button.
    """
    import fetchers.data_fetcher as fetcher_mod

    class Tripwire:
        def __init__(self, *a, **k):
            raise AssertionError("render() fetched something")

    monkeypatch.setattr(fetcher_mod, "DataFetcher", Tripwire)
    assert render(ctx).exists()


def test_render_writes_a_page_and_a_ticket(ctx):
    path = render(ctx)
    assert path.exists()
    assert (ctx.settings.output_dir / "ticket.csv").exists()
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_render_records_what_it_produced_on_the_context(ctx):
    """The console summary and the API read this instead of recomputing it."""
    render(ctx)
    assert ctx.plan is not None and ctx.perf is not None
    assert ctx.brief_path is not None and ctx.brief_path.exists()


def test_rendering_twice_is_stable(ctx):
    first = render(ctx).read_text(encoding="utf-8")
    second = render(ctx).read_text(encoding="utf-8")
    # Only the timestamp should differ.
    assert len(first) == pytest.approx(len(second), abs=200)


# ------------------------------------------------------- a rebuild sees changes
def test_a_recorded_trade_shows_up_after_a_rebuild(ctx):
    """The whole reason Rebuild exists."""
    from portfolio.fees import FeeConfig
    from portfolio.journal import append_trade, build_trade

    before = render(ctx).read_text(encoding="utf-8")
    assert "ZZZZ.JK" not in before

    trade = build_trade("BUY", "ZZZZ", 3, 4150, FeeConfig(), on_date="2026-08-20")
    append_trade(trade, ctx.settings.account["journal_path"])

    after = render(ctx).read_text(encoding="utf-8")
    assert "ZZZZ.JK" in after, "the ledger did not pick up the new trade"


def test_a_noted_event_shows_up_after_a_rebuild(ctx):
    from market.events import add_event

    add_event("BBRI", "earnings", "2099-01-01", ctx.settings.events_path, "later")
    add_event("BBRI", "earnings", pd.Timestamp.today() + pd.Timedelta(days=3),
              ctx.settings.events_path, "soon")

    assert "earnings" in render(ctx).read_text(encoding="utf-8")


def test_a_changed_capital_resizes_the_ticket(ctx):
    small = render(ctx).read_text(encoding="utf-8")
    ctx.settings.account = {**ctx.settings.account, "capital_rp": 80_000_000}
    big = render(ctx).read_text(encoding="utf-8")
    assert "Rp80,000,000" in big and "Rp80,000,000" not in small


# ------------------------------------------------------------------- resilience
def test_a_failing_section_does_not_lose_the_page(ctx, monkeypatch):
    """A broken Screener view must still leave a usable ticket."""
    import report.advanced as adv
    monkeypatch.setattr(adv, "render_advanced",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = render(ctx).read_text(encoding="utf-8")
    assert "Do this today" in out


def test_the_placeholder_banner_follows_the_setting(ctx):
    from first_run import PLACEHOLDER_CAPITAL

    assert "placeholder capital" not in render(ctx).read_text(encoding="utf-8")
    ctx.settings.account = {**ctx.settings.account, "capital_rp": PLACEHOLDER_CAPITAL}
    assert "placeholder capital" in render(ctx).read_text(encoding="utf-8")


# ------------------------------------------------- the branch that crashed
def test_the_summary_survives_an_empty_journal(ctx):
    from runner import console_summary, render

    render(ctx)
    text = console_summary(ctx)
    assert "DO THIS TODAY" in text
    assert "Analyzed" in text


def test_the_summary_survives_a_journal_with_holdings(ctx):
    """
    The exact crash a reader hit: `console_block` was reached only once something
    was held, so every test with an empty journal skipped it and the missing import
    shipped. This test owns something.
    """
    from portfolio.fees import FeeConfig
    from portfolio.journal import append_trade, build_trade
    from runner import console_summary, render

    for ticker in list(ctx.settings.stock_tickers)[:2]:
        append_trade(
            build_trade("BUY", ticker, 3, 1000, FeeConfig(), on_date="2026-08-01"),
            ctx.settings.account["journal_path"],
        )

    render(ctx)
    assert ctx.perf.position_value > 0, "the fixture did not actually create a holding"

    text = console_summary(ctx)
    assert "HOW YOU'RE DOING" in text, "the performance block did not render"
    assert "Portfolio value" in text


def test_the_summary_survives_a_closed_round_trip(ctx):
    from portfolio.fees import FeeConfig
    from portfolio.journal import append_trade, build_trade
    from runner import console_summary, render

    ticker = list(ctx.settings.stock_tickers)[0]
    path = ctx.settings.account["journal_path"]
    append_trade(build_trade("BUY", ticker, 3, 1000, FeeConfig(), on_date="2026-08-01"), path)
    append_trade(build_trade("SELL", ticker, 3, 1200, FeeConfig(), on_date="2026-08-10"), path)

    render(ctx)
    assert ctx.perf.n_closed > 0
    assert "Closed trades" in console_summary(ctx)


# ------------------------------------------------------- exits, still offline
# The exit plans need a price SERIES where the rest of the render needs one row,
# so a price tail rides along in the snapshot. If that ever stopped working the
# obvious fix would be to fetch it -- which is the forty seconds the whole split
# exists to avoid. These pin both halves.

def _with_position(ctx):
    """One open position and the price history the exit rules read."""
    from portfolio.journal import append_trade, build_trade
    from portfolio.fees import FeeConfig

    ticker = ctx.df["ticker"].iloc[0]
    price = float(ctx.df["last_close"].iloc[0])
    journal_path = ctx.settings.account["journal_path"]
    append_trade(
        build_trade("BUY", ticker.replace(".JK", ""), 5, price * 1.20,
                    FeeConfig(), on_date="2026-08-03"),
        journal_path)

    idx = pd.bdate_range("2026-08-03", periods=25)
    close = pd.DataFrame(
        {t: np.linspace(float(ctx.df["last_close"].iloc[i]) * 1.2,
                        float(ctx.df["last_close"].iloc[i]), len(idx))
         for i, t in enumerate(ctx.df["ticker"])}, index=idx)
    ctx.risk_panel = {"Close": close, "High": close * 1.01}
    return ctx


def test_render_builds_exit_plans_from_the_snapshot(ctx):
    _with_position(ctx)
    render(ctx)

    plans = ctx.plan["exit_plans"]
    assert plans, "an open position must get an exit plan"
    plan = next(iter(plans.values()))
    assert plan.stop_rp and plan.stop_rp > 0
    assert plan.risk_rp and plan.risk_rp > 0
    assert ctx.plan["open_risk"]["n_positions"] == 1


def test_the_exit_plans_cost_no_network_either(ctx, monkeypatch):
    """
    The same tripwire as the launch test, with a position open. A stop needs price
    history, and reaching for it over the wire is exactly the regression that
    would hide behind this feature.
    """
    import fetchers.data_fetcher as fetcher_mod

    class Tripwire:
        def __init__(self, *a, **k):
            raise AssertionError("render() fetched something")

    _with_position(ctx)
    monkeypatch.setattr(fetcher_mod, "DataFetcher", Tripwire)
    assert render(ctx).exists()
    assert ctx.plan["exit_plans"]


def test_no_price_tail_degrades_to_no_plan_not_to_a_crash(ctx):
    """A snapshot written before exits existed must still render a page."""
    _with_position(ctx)
    ctx.risk_panel = {}
    assert render(ctx).exists()


def test_the_decision_trail_records_the_exit_stage(ctx):
    """Getting out is a decision, so the Why page has to show it like the others."""
    _with_position(ctx)
    render(ctx)
    keys = [s.key for s in ctx.plan["trail"].stages]
    assert "exits" in keys
