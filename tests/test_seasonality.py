import numpy as np
import pandas as pd
import pytest

from market.seasonality import (MIN_OBSERVATIONS, describe, for_month,
                                monthly_returns, monthly_seasonality)


def _daily(years: int, seed: int = 0) -> pd.Series:
    idx = pd.date_range("1996-01-01", periods=years * 365, freq="D")
    rng = np.random.default_rng(seed)
    return pd.Series(1000 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(idx))), index=idx)


def test_twelve_rows_one_per_month():
    table = monthly_seasonality(_daily(30))
    assert len(table) == 12
    assert list(table["month"]) == list(range(1, 13))


def test_sample_size_is_always_reported():
    """Without n, a hit rate is unreadable -- 55% of 2 years is not 55% of 30."""
    table = monthly_seasonality(_daily(30))
    assert (table["n"] > 0).all()
    assert table["n"].max() >= 25


def test_two_years_of_history_is_visibly_thin():
    """
    The trap this module exists to avoid: 2 years gives ~2 observations a month.
    The output must expose that rather than presenting a confident percentage.
    """
    table = monthly_seasonality(_daily(2))
    assert table["n"].max() <= 3
    assert "too few years" in describe(for_month(table, 8))


def test_long_history_still_carries_a_caveat():
    table = monthly_seasonality(_daily(31))
    text = describe(for_month(table, 8))
    assert "weak evidence" in text
    assert "n=" in text


def test_hit_rate_is_a_percentage():
    table = monthly_seasonality(_daily(30))
    assert table["hit_rate"].between(0, 100).all()


def test_known_up_month_shows_a_high_hit_rate():
    """Force every December positive and check it is detected."""
    idx = pd.date_range("1996-01-01", periods=30 * 365, freq="D")
    values = np.full(len(idx), 1000.0)
    series = pd.Series(values, index=idx)
    # step the price up on the last day of each December
    for year in range(1996, 2026):
        mask = series.index >= pd.Timestamp(f"{year}-12-25")
        series[mask] = series[mask] * 1.05

    table = monthly_seasonality(series)
    assert for_month(table, 12)["hit_rate"] > 90


def test_empty_and_short_series_are_safe():
    assert monthly_seasonality(pd.Series(dtype=float)).empty
    assert monthly_seasonality(None).empty
    assert monthly_returns(pd.Series([100.0])).empty


def test_for_month_missing_returns_none():
    assert for_month(pd.DataFrame(), 8) is None
    assert describe(None) == "No seasonality data available."


def test_describe_names_the_month():
    table = monthly_seasonality(_daily(30))
    assert "August" in describe(for_month(table, 8))


def test_nan_prices_are_dropped():
    series = _daily(5)
    series.iloc[100:150] = np.nan
    assert not monthly_seasonality(series).empty
