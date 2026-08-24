"""
Opening the app must not mean fetching.

Every launch used to call `full_run`, and `fetch_fundamentals` had no cache at all
-- 49 `yf.Ticker().info` calls, every single time, for data that moves quarterly.
Opening the terminal to record a trade at lunch cost forty seconds of network.

So the screen is saved and reopened, and fetching is what the Update data button is
for. The test that carries this is test_a_launch_never_touches_the_network: the
fetcher is replaced with one that raises, and the page must still be produced.
"""
import pandas as pd
import pytest

from runner import RunContext, load_snapshot, save_snapshot, universe_key


class _Regime:
    """Stand-in for market.regime.Regime -- plain data, which is the point."""

    def __init__(self, deploy_pct=0.6, label="RISK-ON"):
        self.deploy_pct = deploy_pct
        self.label = label
        self.signals = []
        self.emoji = "?"
        self.headline = ""

    def __eq__(self, other):
        return (self.deploy_pct, self.label) == (other.deploy_pct, other.label)


@pytest.fixture
def settings(settings_mock, tmp_path):
    settings_mock.account = {**(settings_mock.account or {}),
                             "snapshot_path": str(tmp_path / "run.joblib")}
    return settings_mock


def _ctx(settings, **over):
    base = dict(
        df=pd.DataFrame({"ticker": ["BBRI.JK", "TLKM.JK"], "score": [1.0, 0.5]}),
        benchmark_data={"^JKSE": pd.DataFrame({"Close": [6000.0, 6100.0]})},
        regime=_Regime(),
        season_table=pd.DataFrame({"month": [8], "avg": [-0.022]}),
        season_line="August: -2.2% average",
        fetched_at=pd.Timestamp("2026-08-24 07:15"),
        universe_key=universe_key(settings),
    )
    base.update(over)
    return RunContext(settings=settings, args=None, **base)


# ---------------------------------------------------------------- round trip
def test_what_a_render_needs_survives_the_round_trip(settings):
    save_snapshot(_ctx(settings))
    back = load_snapshot(settings, args=None)

    assert back is not None
    assert list(back.df["ticker"]) == ["BBRI.JK", "TLKM.JK"]
    assert back.regime.deploy_pct == 0.6
    assert back.season_line == "August: -2.2% average"
    assert back.fetched_at == pd.Timestamp("2026-08-24 07:15")
    assert "^JKSE" in back.benchmark_data


def test_the_price_panel_is_not_written(settings):
    """
    `full_run` fills `price_data` and nothing reads it -- not render, not the console
    summary, not the API. Forty-nine frames nobody opens would be the largest thing
    in the file.
    """
    ctx = _ctx(settings)
    ctx.price_data = {"BBRI.JK": pd.DataFrame({"Close": range(500)})}
    path = save_snapshot(ctx)

    import joblib
    assert "price_data" not in joblib.load(path)
    assert load_snapshot(settings, args=None).price_data == {}


# ------------------------------------------------------------- when to ignore
def test_a_missing_file_is_simply_no_snapshot(settings):
    assert load_snapshot(settings, args=None) is None


def test_a_corrupt_file_returns_none_rather_than_raising(settings):
    from pathlib import Path
    path = Path(settings.account["snapshot_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a pickle, not anything")

    assert load_snapshot(settings, args=None) is None


def test_a_changed_ticker_universe_invalidates_it(settings):
    """
    `df` holds cross-sectional scores. Rendering them against a different universe
    would show a ranking computed over names that are no longer in it.
    """
    save_snapshot(_ctx(settings))
    settings.stock_tickers = {**settings.stock_tickers, "GOTO.JK": "GoTo"}

    assert load_snapshot(settings, args=None) is None


def test_a_changed_history_period_invalidates_it(settings):
    save_snapshot(_ctx(settings))
    settings.history_period = "5y"
    assert load_snapshot(settings, args=None) is None


def test_an_older_format_is_ignored(settings):
    import joblib
    from pathlib import Path

    path = Path(settings.account["snapshot_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"version": 0, "df": pd.DataFrame({"a": [1]})}, path)

    assert load_snapshot(settings, args=None) is None


def test_a_snapshot_without_a_regime_is_ignored(settings):
    """`render` reads regime.deploy_pct, so this would fail further from the cause."""
    save_snapshot(_ctx(settings, regime=None))
    assert load_snapshot(settings, args=None) is None


def test_an_empty_frame_is_ignored(settings):
    save_snapshot(_ctx(settings, df=pd.DataFrame()))
    assert load_snapshot(settings, args=None) is None


def test_saving_somewhere_unwritable_does_not_raise(settings, monkeypatch):
    """
    A snapshot that cannot be written costs the next launch forty seconds. An
    exception here would cost this one everything after it.
    """
    import joblib

    def boom(*a, **k):
        raise OSError("read-only volume")

    monkeypatch.setattr(joblib, "dump", boom)
    assert save_snapshot(_ctx(settings)) is None


# -------------------------------------------------------- the launch is offline
def test_a_launch_never_touches_the_network(settings, monkeypatch):
    """
    The whole point. Replace the fetcher with one that raises, and reopening the
    saved screen must still work.
    """
    save_snapshot(_ctx(settings))

    import fetchers.data_fetcher as F

    def boom(*a, **k):
        raise AssertionError("opening the app must not fetch anything")

    monkeypatch.setattr(F.DataFetcher, "fetch_technical_data", boom)
    monkeypatch.setattr(F.DataFetcher, "fetch_fundamentals", boom)

    assert load_snapshot(settings, args=None) is not None


def test_the_universe_key_is_stable_across_calls(settings):
    assert universe_key(settings) == universe_key(settings)


def test_the_universe_key_ignores_ticker_order(settings):
    first = universe_key(settings)
    settings.stock_tickers = dict(reversed(list(settings.stock_tickers.items())))
    assert universe_key(settings) == first
