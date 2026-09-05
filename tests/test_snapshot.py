"""
Opening the app must not mean fetching.

Every launch used to call `full_run`, and `fetch_fundamentals` had no cache at all
-- 49 `yf.Ticker().info` calls, every single time, for data that moves quarterly.
Opening the terminal to record a trade at lunch cost forty seconds of network.

So the screen is saved and reopened, and fetching is what the Update data button is
for. The test that carries this is test_a_launch_never_touches_the_network: the
fetcher is replaced with one that raises, and the page must still be produced.
"""
from pathlib import Path

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


# ------------------------------------------------------- the exits' price tail
# `price_data` is still left out of the snapshot -- 49 OHLCV frames would be the
# largest thing in the file. But a stop compares today's close against an entry
# made weeks ago and a trailing stop needs the high since that entry, and neither
# survives in `df`, which holds one row per ticker. So a trimmed Close/High panel
# goes in, and `render` stays off the network where the whole split requires it.

def _risk_panel():
    idx = pd.bdate_range("2026-08-03", periods=25)
    close = pd.DataFrame({"BBRI.JK": range(4000, 4025),
                          "TLKM.JK": range(2600, 2625)}, index=idx).astype(float)
    return {"Close": close, "High": close * 1.01}


def test_the_price_tail_survives_a_round_trip(settings):
    panel = _risk_panel()
    save_snapshot(_ctx(settings, risk_panel=panel))
    back = load_snapshot(settings, args=None)

    assert set(back.risk_panel) == {"Close", "High"}
    pd.testing.assert_frame_equal(back.risk_panel["Close"], panel["Close"])
    pd.testing.assert_frame_equal(back.risk_panel["High"], panel["High"])


def test_an_older_snapshot_is_ignored_rather_than_read_without_it(settings, tmp_path):
    """
    A v1 file carries no price series, so every position would come back with no
    stop and no trailing level -- the exit panel would read "cannot measure" for a
    book that is perfectly measurable. Refetching once is the right answer;
    rendering a confident wrong one is not.
    """
    import joblib

    import runner

    save_snapshot(_ctx(settings, risk_panel=_risk_panel()))
    path = tmp_path / "run.joblib"
    blob = joblib.load(path)
    blob["version"] = runner.SNAPSHOT_VERSION - 1
    blob.pop("risk_panel", None)
    joblib.dump(blob, path)

    assert load_snapshot(settings, args=None) is None


def test_a_snapshot_without_a_price_tail_still_loads(settings):
    """Degrade to no exit plans, never to a crash."""
    save_snapshot(_ctx(settings))
    back = load_snapshot(settings, args=None)
    assert back is not None
    assert back.risk_panel == {}


def test_risk_panel_keeps_only_what_the_exits_read():
    from fetchers.data_fetcher import risk_panel

    idx = pd.bdate_range("2025-01-01", periods=400)
    frame = pd.DataFrame({
        "Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 1e6,
    }, index=idx)

    out = risk_panel({"BBRI.JK": frame}, sessions=300)
    assert set(out) == {"Close", "High"}          # Open, Low and Volume dropped
    assert len(out["Close"]) == 300               # trimmed to the recent window
    assert out["Close"].index[-1] == idx[-1]


# ------------------------------------ a snapshot is stale when the CODE moved too
# `SNAPSHOT_VERSION` catches a change somebody remembered to declare. The dividend
# fix altered what `undervaluation_score` MEANS without touching a single field
# name, so the saved screen stayed perfectly loadable and went on showing scores
# computed under a bug -- the shipped exe rendered BREN at a 12% yield on a stock
# that pays Rp0, from a file written by the previous binary. Only pressing Update
# would have cleared it, and nothing on the page suggested it needed clearing.

def test_a_snapshot_written_before_a_code_change_is_refused(settings, tmp_path,
                                                            monkeypatch):
    import os
    import time

    save_snapshot(_ctx(settings))
    path = tmp_path / "run.joblib"
    assert load_snapshot(settings, args=None) is not None, "sanity: it loads first"

    # Age the snapshot so every source file is newer than it.
    old = time.time() - 3600
    os.utime(path, (old, old))

    assert load_snapshot(settings, args=None) is None


def test_an_up_to_date_snapshot_still_loads(settings):
    """The guard must not make the saved screen useless -- that is its whole point."""
    save_snapshot(_ctx(settings))
    assert load_snapshot(settings, args=None) is not None


def test_a_frozen_build_compares_against_its_own_executable(tmp_path, monkeypatch):
    """
    A new binary is new logic by definition, so its timestamp is the right clock
    when there is no source tree to look at.
    """
    import os
    import sys
    import time

    import runner

    exe = tmp_path / "IDX Terminal.exe"
    exe.write_bytes(b"x")
    snap = tmp_path / "run.joblib"
    snap.write_bytes(b"y")

    monkeypatch.setattr("core.paths.is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)

    old = time.time() - 3600
    os.utime(snap, (old, old))
    assert runner._code_changed_since(snap) is True

    os.utime(exe, (old - 60, old - 60))
    assert runner._code_changed_since(snap) is False


def test_an_unreadable_timestamp_does_not_refuse_the_snapshot():
    """
    Refetching costs a minute; refusing to refetch when the maths moved costs the
    wrong trade. But a missing file is not evidence either way, and crashing the
    launch over it would be worse than both.
    """
    import runner
    assert runner._code_changed_since(Path("does/not/exist.joblib")) is False


def test_the_version_was_bumped_for_the_scoring_change():
    """
    Belt as well as braces. The timestamp guard is automatic; the version is the
    declaration, and this pins that someone made it.
    """
    import runner
    assert runner.SNAPSHOT_VERSION >= 3
