import pytest
import pandas as pd
import numpy as np
from pathlib import Path

@pytest.fixture
def sample_fundamental_data():
    return [
        {"ticker": "BBCA.JK", "name": "Bank Central Asia", "pe_ratio": 12.5, "price_to_book": 3.2, "dividend_yield": 0.02, "beta": 0.85},
        {"ticker": "TLKM.JK", "name": "Telkomsel", "pe_ratio": 18.1, "price_to_book": 2.9, "dividend_yield": 0.01, "beta": 1.1},
        {"ticker": "ASII.JK", "name": "Astra International", "pe_ratio": 9.8, "price_to_book": 1.5, "dividend_yield": 0.03, "beta": 1.2},
    ]

@pytest.fixture
def sample_technical_data():
    return [
        {"ticker": "BBCA.JK", "current_price": 9500, "price_change_pct": 1.2, "rsi_14": 45.0, "ma5_slope": 0.5, "ma20_slope": -0.3, "volume_ratio": 1.1},
        {"ticker": "TLKM.JK", "current_price": 3800, "price_change_pct": -0.8, "rsi_14": 32.0, "ma5_slope": -1.2, "ma20_slope": -0.5, "volume_ratio": 0.9},
        {"ticker": "ASII.JK", "current_price": 5200, "price_change_pct": 2.5, "rsi_14": 68.0, "ma5_slope": 1.8, "ma20_slope": 0.7, "volume_ratio": 1.4},
    ]

@pytest.fixture
def settings_mock():
    from src.core.config import Settings
    return Settings(
        stock_tickers={"BBCA.JK": "Bank Central Asia", "TLKM.JK": "Telkomsel", "ASII.JK": "Astra International"},
        technical={"ma_periods": [5, 20], "rsi_window": 14, "bb_window": 20},
        ml={"features": ["pe_ratio", "price_to_book", "dividend_yield", "rsi_14", "ma5_slope", "ma20_slope"], "scoring_method": "zscore_normalized", "walk_forward_splits": 2}
    )
