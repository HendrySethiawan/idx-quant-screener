import pandas as pd
import pytest
from unittest.mock import MagicMock

from fetchers.data_fetcher import DataFetcher, is_index


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
