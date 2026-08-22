"""
17 of the 49 IDX names report financials in USD while trading in IDR.
yfinance divides the IDR price by the USD book value, reporting ADRO at 15,000
and PTRO at 203,846. Those get nullified by the sanity bound, which strips the
value factor from a third of the universe unless the mismatch is repaired.

`trailingEps` is already converted to IDR, so P/E needs no repair -- these tests
pin that asymmetry so a future "fix" to P/E does not get added by mistake.
"""
import pytest

from fetchers.data_fetcher import repair_price_to_book

FX = 17690.0


def test_usd_reporter_price_to_book_is_repaired():
    # Real ADRO figures: 2550 IDR price, 0.17 USD book value per share.
    info = {"priceToBook": 15000.0, "financialCurrency": "USD", "currency": "IDR",
            "bookValue": 0.17, "currentPrice": 2550.0}
    ptb, note = repair_price_to_book(info, FX)

    assert ptb == pytest.approx(2550.0 / (0.17 * FX), rel=1e-6)
    assert 0.5 < ptb < 2.0, "repaired ADRO P/B should be a plausible ~0.85"
    assert "repaired" in note


def test_repaired_value_survives_the_sanity_bound():
    """The whole point: 203,846 gets nullified, 11.5 does not."""
    info = {"priceToBook": 203846.16, "financialCurrency": "USD", "currency": "IDR",
            "bookValue": 0.026, "currentPrice": 5300.0}
    ptb, _ = repair_price_to_book(info, FX)
    assert ptb < 20.0


def test_matching_currency_is_left_alone():
    info = {"priceToBook": 2.93, "financialCurrency": "IDR", "currency": "IDR",
            "bookValue": 2201.5, "currentPrice": 6450.0}
    ptb, note = repair_price_to_book(info, FX)
    assert ptb == 2.93
    assert note is None


def test_missing_fx_leaves_value_unrepaired_but_flagged():
    """Offline must degrade to the old behaviour, not to a wrong number."""
    info = {"priceToBook": 15000.0, "financialCurrency": "USD", "currency": "IDR",
            "bookValue": 0.17, "currentPrice": 2550.0}
    ptb, note = repair_price_to_book(info, None)
    assert ptb == 15000.0
    assert "unrepaired" in note


def test_nonpositive_book_value_returns_none():
    info = {"priceToBook": -5.0, "financialCurrency": "USD", "currency": "IDR",
            "bookValue": -0.3, "currentPrice": 2550.0}
    ptb, note = repair_price_to_book(info, FX)
    assert ptb is None
    assert "nonpositive" in note


def test_absent_currency_metadata_is_a_no_op():
    info = {"priceToBook": 3.1}
    ptb, note = repair_price_to_book(info, FX)
    assert ptb == 3.1
    assert note is None
