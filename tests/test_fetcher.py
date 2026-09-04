import pandas as pd
import pytest
import yfinance as yf
from unittest.mock import MagicMock

from fetchers.data_fetcher import DataFetcher, is_index, session_report


@pytest.fixture(autouse=True)
def _own_cache(tmp_path, monkeypatch):
    """
    Every test here gets an empty cache directory.

    `DataFetcher` resolves `data/cache` against the working directory, so without
    this these tests read the repository's real cache -- and a mocked yfinance
    reply would be quietly ignored in favour of whatever was last fetched for that
    ticker. It went unnoticed only because a timezone bug meant the cache never hit.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def frame():
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    return pd.DataFrame(
        {"Open": [1.0] * 5, "High": [1.0] * 5, "Low": [1.0] * 5,
         "Close": [100.0, 101.0, 102.0, 103.0, 104.0], "Volume": [1000.0] * 5},
        index=idx,
    )


def test_cache_key_includes_the_window(settings_mock, tmp_path, mocker, frame):
    """
    The old key was f"{ticker}.pkl". Widening history then kept serving the stale
    30-bar frame forever, silently degrading every downstream factor.
    """
    mocker.patch("fetchers.data_fetcher.yf.download", return_value=frame)
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path

    fetcher._fetch_single("BBCA.JK", period="2y")
    fetcher._fetch_single("BBCA.JK", period="5y")

    names = sorted(p.name for p in tmp_path.glob("*.pkl"))
    assert names == ["BBCA.JK__2y.pkl", "BBCA.JK__5y.pkl"]


def test_uses_history_period_not_retention_days(settings_mock, tmp_path, mocker, frame):
    download = mocker.patch("fetchers.data_fetcher.yf.download", return_value=frame)
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path

    fetcher._fetch_single("BBCA.JK")

    assert download.call_args.kwargs["period"] == settings_mock.history_period
    assert download.call_args.kwargs["period"] != f"{settings_mock.data_retention_days}d"


def test_corrupt_cache_triggers_refetch(settings_mock, tmp_path, mocker, frame):
    download = mocker.patch("fetchers.data_fetcher.yf.download", return_value=frame)
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path
    (tmp_path / "BBCA.JK__2y.pkl").write_bytes(b"not a pickle")

    out = fetcher._fetch_single("BBCA.JK", period="2y")

    assert download.call_count == 1
    assert len(out) == 5


def test_empty_download_raises(settings_mock, tmp_path, mocker):
    mocker.patch("fetchers.data_fetcher.yf.download", return_value=pd.DataFrame())
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path
    with pytest.raises(ValueError):
        fetcher._fetch_single("NOPE.JK", period="2y")


def test_index_tickers_are_skipped_for_fundamentals(settings_mock, mocker):
    ticker = mocker.patch("fetchers.data_fetcher.yf.Ticker")
    fetcher = DataFetcher(settings_mock)

    records = fetcher.fetch_fundamentals({"^JKSE": "Composite"})

    assert records == []
    ticker.assert_not_called()


def test_quality_metrics_are_mapped(settings_mock, mocker):
    mocker.patch(
        "fetchers.data_fetcher.yf.Ticker",
        return_value=MagicMock(info={
            "marketCap": 1e12, "trailingPE": 15.0, "priceToBook": 2.5,
            "dividendYield": 0.02, "beta": 0.9,
            "returnOnEquity": 0.18, "grossMargins": 0.55, "debtToEquity": 42.0,
        }),
    )
    records = DataFetcher(settings_mock).fetch_fundamentals({"BBCA.JK": "BCA"})

    assert records[0]["roe"] == 0.18
    assert records[0]["gross_margin"] == 0.55
    assert records[0]["debt_to_equity"] == 42.0


def test_failed_ticker_is_skipped_not_fatal(settings_mock, tmp_path, mocker):
    mocker.patch("fetchers.data_fetcher.yf.download", side_effect=ValueError("boom"))
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path
    assert fetcher.fetch_technical_data({"BAD.JK": "Bad"}) == {}


@pytest.mark.parametrize("ticker,expected", [
    ("^JKSE", True), ("BBCA.JK", False), (".KS11", True),
])
def test_is_index(ticker, expected):
    assert is_index(ticker) is expected


# ------------------------------------------------------------- the cache clock
# The cache silently never hit. `pd.Timestamp.now().timestamp()` converts a naive
# timestamp as if it were UTC, while `st_mtime` is a true epoch value -- so on a
# UTC+7 machine every file read as seven hours old and all 49 tickers were refetched
# on every single run, which is most of what made launching the app slow.
def test_a_file_just_written_is_fresh(settings_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fetcher = DataFetcher(settings_mock)

    path = fetcher._cache_path("BBRI.JK", "2y")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    assert fetcher._fresh(path), (
        "a file written a moment ago read as stale - the cache clock is wrong")


def test_a_file_older_than_the_ttl_is_stale(settings_mock, tmp_path, monkeypatch):
    import os
    import time as _time

    monkeypatch.chdir(tmp_path)
    settings_mock.cache_ttl_minutes = 60
    fetcher = DataFetcher(settings_mock)

    path = fetcher._cache_path("BBRI.JK", "2y")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    old = _time.time() - 2 * 3600
    os.utime(path, (old, old))

    assert not fetcher._fresh(path)


def test_freshness_does_not_depend_on_the_local_timezone(settings_mock, tmp_path,
                                                         monkeypatch):
    """The bug in one line: the answer must not change with TZ."""
    monkeypatch.chdir(tmp_path)
    fetcher = DataFetcher(settings_mock)
    path = fetcher._cache_path("BBRI.JK", "2y")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    answers = set()
    for tz in ("UTC", "Asia/Jakarta", "America/New_York"):
        monkeypatch.setenv("TZ", tz)
        answers.add(fetcher._fresh(path))
    assert answers == {True}


def test_a_missing_file_is_never_fresh(settings_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fetcher = DataFetcher(settings_mock)
    assert not fetcher._fresh(tmp_path / "nothing.pkl")


# ----------------------------------------------------- the session that vanished
# Yahoo's daily feed served the 24 Aug IDX close, then withdrew it. Its intraday
# feed still had it, and aggregating 60m bars reproduced the daily bar exactly --
# verified digit-for-digit against Yahoo's own 20 and 21 Aug bars, and against the
# broker's screen for 24 Aug. Two defences came out of that: rebuild what the daily
# feed is missing, and never let a refetch delete a session we already hold.
def _intraday(rows):
    """rows: (timestamp, open, high, low, close)"""
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz="UTC") for t, *_ in rows])
    return pd.DataFrame(
        {"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows],
         "Volume": [1000] * len(rows)},
        index=idx)


def _daily(dates, closes):
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": [5_000_000] * len(closes)},
        index=pd.DatetimeIndex([pd.Timestamp(d) for d in dates]))


def test_intraday_bars_aggregate_into_one_daily_bar(settings_mock):
    """First = open, max = high, min = low, last = close."""
    bars = _intraday([
        ("2026-08-24 02:00", 6544.0, 6548.0, 6540.0, 6546.0),   # 09:00 Jakarta
        ("2026-08-24 05:00", 6546.0, 6551.0, 6474.57, 6480.0),
        ("2026-08-24 09:00", 6480.0, 6510.0, 6478.0, 6501.67),  # 16:00 Jakarta
    ])
    out = DataFetcher(settings_mock)._sessions_from_intraday(bars, "Asia/Jakarta")

    assert len(out) == 1
    row = out.iloc[0]
    assert (row["Open"], row["High"], row["Low"], row["Close"]) == (
        6544.0, 6551.0, 6474.57, 6501.67)


def test_sessions_are_grouped_in_the_exchanges_day_not_ours(settings_mock):
    """The batched download comes back in UTC; the session is a local question."""
    bars = _intraday([("2026-08-24 02:00", 1.0, 1.0, 1.0, 1.0),
                      ("2026-08-24 09:00", 2.0, 2.0, 2.0, 2.0)])
    out = DataFetcher(settings_mock)._sessions_from_intraday(bars, "Asia/Jakarta")
    assert [str(d.date()) for d in out.index] == ["2026-08-24"]


def test_a_rebuilt_bar_carries_no_volume(settings_mock, monkeypatch, tmp_path):
    """
    Intraday sums to roughly 60% of the official daily figure, because the auction
    and off-book prints are missing. A volume 40% short feeding a liquidity floor
    is worse than none: the floor decides whether a name can be exited at all.
    """
    monkeypatch.chdir(tmp_path)
    fetcher = DataFetcher(settings_mock)
    data = {"BBRI.JK": _daily(["2026-08-20", "2026-08-21"], [3140.0, 3230.0])}

    monkeypatch.setattr(fetcher, "_latest_market_session",
                        lambda *_a, **_k: pd.Timestamp("2026-08-24"))
    monkeypatch.setattr(
        yf, "download",
        lambda *a, **k: _intraday([("2026-08-24 09:00", 3200.0, 3240.0, 3170.0, 3180.0)]))

    out = fetcher.top_up_last_session(data)["BBRI.JK"]

    assert str(out.index[-1].date()) == "2026-08-24"
    assert float(out["Close"].iloc[-1]) == 3180.0
    assert pd.isna(out["Volume"].iloc[-1]), "a derived bar must not claim a volume"
    assert bool(out["synthetic"].iloc[-1]) is True
    assert bool(out["synthetic"].iloc[0]) is False


def test_nothing_is_rebuilt_when_the_data_already_reaches_the_market(settings_mock,
                                                                    monkeypatch,
                                                                    tmp_path):
    monkeypatch.chdir(tmp_path)
    fetcher = DataFetcher(settings_mock)
    data = {"BBRI.JK": _daily(["2026-08-21", "2026-08-24"], [3230.0, 3180.0])}

    monkeypatch.setattr(fetcher, "_latest_market_session",
                        lambda *_a, **_k: pd.Timestamp("2026-08-24"))

    def boom(*a, **k):
        raise AssertionError("nothing to rebuild, so nothing should be fetched")

    monkeypatch.setattr(yf, "download", boom)
    assert len(fetcher.top_up_last_session(data)["BBRI.JK"]) == 2


def test_an_unknown_market_session_leaves_the_data_alone(settings_mock, monkeypatch,
                                                        tmp_path):
    """Not knowing must never be treated as being up to date."""
    monkeypatch.chdir(tmp_path)
    fetcher = DataFetcher(settings_mock)
    data = {"BBRI.JK": _daily(["2026-08-21"], [3230.0])}
    monkeypatch.setattr(fetcher, "_latest_market_session", lambda *_a, **_k: None)

    assert fetcher.top_up_last_session(data)["BBRI.JK"].equals(data["BBRI.JK"])


def test_a_failed_intraday_fetch_leaves_the_data_alone(settings_mock, monkeypatch,
                                                       tmp_path):
    monkeypatch.chdir(tmp_path)
    fetcher = DataFetcher(settings_mock)
    original = _daily(["2026-08-21"], [3230.0])
    data = {"BBRI.JK": original.copy()}

    monkeypatch.setattr(fetcher, "_latest_market_session",
                        lambda *_a, **_k: pd.Timestamp("2026-08-24"))

    def boom(*a, **k):
        raise RuntimeError("network gone")

    monkeypatch.setattr(yf, "download", boom)
    assert fetcher.top_up_last_session(data)["BBRI.JK"].equals(original)


def test_the_probe_asks_the_index_not_whichever_ticker_is_first(settings_mock,
                                                                monkeypatch,
                                                                tmp_path):
    """
    A thinly traded name that did not print on the latest session would report the
    market as older than it is, and the top-up would decline to run for everyone.
    """
    monkeypatch.chdir(tmp_path)
    settings_mock.regime = {**(settings_mock.regime or {}), "benchmark": "^JKSE"}
    fetcher = DataFetcher(settings_mock)

    asked = []
    monkeypatch.setattr(fetcher, "_latest_market_session",
                        lambda t: asked.append(t) or pd.Timestamp("2026-08-24"))
    monkeypatch.setattr(yf, "download", lambda *a, **k: pd.DataFrame())

    fetcher.top_up_last_session({"ADHI.JK": _daily(["2026-08-21"], [200.0])})
    assert asked == ["^JKSE"]


# ------------------------------------------------- a session is never given back
def _always_refetch(fetcher, monkeypatch):
    """These tests are about what a refetch merges, not about the TTL."""
    monkeypatch.setattr(fetcher, "_fresh", lambda _p: False)


def test_a_refetch_keeps_a_session_the_vendor_stopped_serving(settings_mock, tmp_path,
                                                              mocker, monkeypatch):
    """
    The failure this exists for. Yahoo served the 24 Aug close, then withdrew it,
    and the next run wrote a shorter frame straight over the good cache. A session
    that traded did not un-trade.
    """
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path
    _always_refetch(fetcher, monkeypatch)

    mocker.patch("fetchers.data_fetcher.yf.download",
                 return_value=_daily(["2026-08-21", "2026-08-24"], [3230.0, 3180.0]))
    first = fetcher._fetch_single("BBRI.JK", period="2y")
    assert str(first.index[-1].date()) == "2026-08-24"

    # The vendor drops the newest session on the next call.
    mocker.patch("fetchers.data_fetcher.yf.download",
                 return_value=_daily(["2026-08-21"], [3230.0]))
    second = fetcher._fetch_single("BBRI.JK", period="2y")

    assert str(second.index[-1].date()) == "2026-08-24", "the session was thrown away"
    assert float(second["Close"].iloc[-1]) == 3180.0


def test_a_revision_to_a_session_we_hold_does_land(settings_mock, tmp_path, mocker,
                                                   monkeypatch):
    """Keeping old dates must not mean ignoring corrections to them."""
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path
    _always_refetch(fetcher, monkeypatch)

    mocker.patch("fetchers.data_fetcher.yf.download",
                 return_value=_daily(["2026-08-21"], [3230.0]))
    fetcher._fetch_single("BBRI.JK", period="2y")

    mocker.patch("fetchers.data_fetcher.yf.download",
                 return_value=_daily(["2026-08-21"], [3225.0]))
    out = fetcher._fetch_single("BBRI.JK", period="2y")

    assert float(out["Close"].iloc[-1]) == 3225.0


def test_the_official_bar_replaces_a_rebuilt_one(settings_mock, tmp_path, mocker,
                                                 monkeypatch):
    """Once the vendor publishes the real session, the derived one steps aside."""
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path
    _always_refetch(fetcher, monkeypatch)

    derived = _daily(["2026-08-21", "2026-08-24"], [3230.0, 3180.0])
    derived.loc[derived.index[-1], "Volume"] = float("nan")
    derived["synthetic"] = [False, True]
    import joblib
    joblib.dump(derived, fetcher._cache_path("BBRI.JK", "2y"))

    official = _daily(["2026-08-21", "2026-08-24"], [3230.0, 3180.0])
    mocker.patch("fetchers.data_fetcher.yf.download", return_value=official)
    out = fetcher._fetch_single("BBRI.JK", period="2y")

    assert float(out["Volume"].iloc[-1]) == 5_000_000, "still the derived bar"


# ---------------------------------------------------------- which session is this
def test_the_session_report_names_the_newest_bar():
    data = {"BBRI.JK": _daily(["2026-08-21", "2026-08-24"], [1.0, 2.0]),
            "TLKM.JK": _daily(["2026-08-21", "2026-08-24"], [1.0, 2.0])}
    out = session_report(data)

    assert str(out["session_date"].date()) == "2026-08-24"
    assert out["mixed"] is False
    assert out["laggards"] == []


def test_the_session_report_names_the_laggards():
    """
    Every score here is a z-score against peers, so names priced on different days
    are not actually being compared. Real data had 50 tickers on one session and 1
    on another.
    """
    data = {"BBRI.JK": _daily(["2026-08-24"], [1.0]),
            "ADHI.JK": _daily(["2026-08-21"], [1.0])}
    out = session_report(data)

    assert out["mixed"] is True
    assert out["laggards"] == [("ADHI.JK", "2026-08-21")]


def test_being_behind_is_only_claimed_when_the_market_session_is_known():
    """Not knowing must never render as being up to date."""
    data = {"BBRI.JK": _daily(["2026-08-21"], [1.0])}

    assert session_report(data)["behind"] is None
    assert session_report(data, pd.Timestamp("2026-08-24"))["behind"] is True
    assert session_report(data, pd.Timestamp("2026-08-21"))["behind"] is False


def test_the_session_report_survives_empty_data():
    assert session_report({})["session_date"] is None
    assert session_report({"X": pd.DataFrame()})["session_date"] is None


# ------------------------------- a name that could not be fetched is not silent
# `fetch_technical_data` logged the error and moved on, so the ticker vanished
# from price_data and from the run. Every score here is cross-sectional, so the
# survivors were then z-scored against a different peer group than the page named.
def test_a_failed_ticker_is_recorded_not_just_logged(settings_mock, tmp_path, mocker):
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path
    mocker.patch("fetchers.data_fetcher.yf.download", side_effect=ValueError("gone"))

    assert fetcher.fetch_technical_data(["WIKA.JK", "BBRI.JK"], period="2y") == {}
    assert fetcher.failed == ["BBRI.JK", "WIKA.JK"]


def test_a_clean_fetch_records_no_failures(settings_mock, tmp_path, mocker, frame):
    fetcher = DataFetcher(settings_mock)
    fetcher.cache_dir = tmp_path
    mocker.patch("fetchers.data_fetcher.yf.download", return_value=frame)

    fetcher.fetch_technical_data(["BBRI.JK"], period="2y")
    assert fetcher.failed == []


def test_the_session_report_carries_the_missing_names():
    data = {"BBRI.JK": _daily(["2026-09-04"], [3180.0])}
    out = session_report(data, failed=["WIKA.JK", "GOTO.JK"])

    assert out["missing"] == ["GOTO.JK", "WIKA.JK"]
    # Not a laggard: a laggard was priced on an older session, these were not
    # priced at all.
    assert out["laggards"] == []


def test_missing_is_empty_when_everything_arrived():
    data = {"BBRI.JK": _daily(["2026-09-04"], [3180.0])}
    assert session_report(data)["missing"] == []


def test_missing_survives_a_total_fetch_failure():
    """Nothing came back at all -- the names still have to be named."""
    assert session_report({}, failed=["WIKA.JK"])["missing"] == ["WIKA.JK"]
