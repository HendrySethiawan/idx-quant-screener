import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _never_touch_the_real_user_config(tmp_path, monkeypatch):
    """
    No test may write to the reader's own configs/user.yaml.

    `save_setting` goes through `core.config.save_user_overrides`, whose default
    path is the real file. A test asserting that capital is editable therefore
    wrote its fixture value -- Rp7,500,000 -- into an actual person's capital, and
    the next run sized every lot count against it. Autouse, because the next
    function to write there will not think to opt in.

    Kept at `configs/user.yaml` under tmp_path rather than a bare filename: tests
    that chdir into tmp_path and then read the relative path must still find what
    was written.
    """
    from core import config

    # Not created here: `save_user_overrides` makes the directory itself, and
    # several tests create it too -- without `exist_ok`.
    monkeypatch.setattr(config, "USER_CONFIG_PATH",
                        str(tmp_path / "configs" / "user.yaml"), raising=False)


@pytest.fixture
def settings_mock():
    from core.config import Settings
    return Settings(
        stock_tickers={
            "BBCA.JK": "Bank Central Asia",
            "BBRI.JK": "Bank Rakyat Indonesia",
            "TLKM.JK": "Telkom Indonesia",
            "ASII.JK": "Astra International",
        },
        sectors={
            "BBCA.JK": "Financials",
            "BBRI.JK": "Financials",
            "TLKM.JK": "Infrastructure",
            "ASII.JK": "Industrials",
        },
        benchmarks={"^JKSE": "Jakarta Composite Index"},
    )


@pytest.fixture
def sample_fundamental_data():
    """Deliberately imperfect: TLKM is missing dividend_yield, ASII is missing roe."""
    return [
        {"ticker": "BBCA.JK", "name": "Bank Central Asia", "pe_ratio": 20.0,
         "price_to_book": 4.2, "dividend_yield": 0.025, "beta": 0.9,
         "roe": 0.18, "gross_margin": 0.55, "debt_to_equity": 20.0, "market_cap": 1.1e15},
        {"ticker": "BBRI.JK", "name": "Bank Rakyat Indonesia", "pe_ratio": 7.5,
         "price_to_book": 1.3, "dividend_yield": 0.08, "beta": 1.0,
         "roe": 0.16, "gross_margin": 0.50, "debt_to_equity": 25.0, "market_cap": 4.4e14},
        {"ticker": "TLKM.JK", "name": "Telkom Indonesia", "pe_ratio": 12.0,
         "price_to_book": 2.1, "dividend_yield": None, "beta": 0.8,
         "roe": 0.14, "gross_margin": 0.40, "debt_to_equity": 60.0, "market_cap": 3.0e14},
        {"ticker": "ASII.JK", "name": "Astra International", "pe_ratio": 6.4,
         "price_to_book": 0.86, "dividend_yield": 0.078, "beta": 1.1,
         "roe": None, "gross_margin": 0.22, "debt_to_equity": 75.0, "market_cap": 2.0e14},
    ]


@pytest.fixture
def price_frame():
    """400 business days of a steadily rising series with volume."""
    idx = pd.date_range("2023-01-02", periods=400, freq="B")
    close = pd.Series(np.linspace(1000, 2000, 400), index=idx)
    return pd.DataFrame({
        "Open": close * 0.99,
        "High": close * 1.01,
        "Low": close * 0.98,
        "Close": close,
        "Volume": pd.Series(np.full(400, 1_000_000.0), index=idx),
    })
