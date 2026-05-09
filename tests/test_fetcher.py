import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from src.core.config import Settings
from src.fetchers.data_fetcher import DataFetcher

@pytest.mark.parametrize("ticker,name", [("BBCA.JK", "Bank Central Asia"), ("TLKM.JK", "Telkomsel")])
def test_fetcher_validates_data(sample_fundamental_data, sample_technical_data, settings_mock, mocker):
    mocker.patch("src.fetchers.data_fetcher.yf.download", return_value=pd.DataFrame({"Close": [100, 102], "Open": [99, 101], "Volume": [1000, 1100]}))
    mocker.patch("src.fetchers.data_fetcher.yf.Ticker", return_value=MagicMock(info={"marketCap": 1e12, "trailingPE": 15.0, "priceToBook": 2.5, "dividendYield": 0.02, "beta": 0.9}))
    
    fetcher = DataFetcher(settings_mock)
    df = fetcher.fetch_all()
    
    assert not df.empty
    assert "undervaluation_score" in df.columns
    assert df["ticker"].nunique() == 2
