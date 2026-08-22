import pandas as pd
import pytest
from unittest.mock import MagicMock

from fetchers.data_fetcher import DataFetcher, is_index


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
