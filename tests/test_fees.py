"""
Indopremier: 0.19% buy, 0.29% sell, Rp10,000 stamp per DAY containing a sell.

The per-day structure is the point. At the user's stated cadence (4-8 actions a
month) batching sells into one day is worth ~Rp30,000/month on a Rp10 juta
account, which is a larger effect than most factor tweaks.
"""
import pytest

from portfolio.fees import (FeeConfig, breakeven_move_pct, estimate_fees,
                            round_trip_cost)

CFG = FeeConfig()
CAPITAL = 10_000_000


def _orders(n_buys, n_sells, value=2_500_000):
    return (
        [{"action": "BUY", "rupiah": value} for _ in range(n_buys)]
        + [{"action": "SELL", "rupiah": value} for _ in range(n_sells)]
    )


def test_buy_and_sell_rates():
    fees = estimate_fees([{"action": "BUY", "rupiah": 1_000_000}], CFG)
    assert fees.buy_fee == pytest.approx(1900)

    fees = estimate_fees([{"action": "SELL", "rupiah": 1_000_000}], CFG)
    assert fees.sell_fee == pytest.approx(2900)


def test_stamp_is_charged_once_per_day_not_per_order():
    batched = estimate_fees(_orders(0, 4), CFG, sell_days=1)
    assert batched.stamp_duty == 10_000


def test_spreading_sells_multiplies_the_stamp():
    spread = estimate_fees(_orders(0, 4), CFG, sell_days=4)
    assert spread.stamp_duty == 40_000


def test_batching_saving_is_reported_in_rupiah():
    batched = estimate_fees(_orders(4, 4), CFG, sell_days=1)
    assert batched.stamp_saving_if_batched == 30_000
    assert any("SAME DAY" in n for n in batched.notes)


def test_no_batching_note_for_a_single_sell():
    fees = estimate_fees(_orders(1, 1), CFG, sell_days=1)
    assert fees.notes == []
    assert fees.stamp_saving_if_batched == 0


def test_no_stamp_when_there_are_no_sells():
    fees = estimate_fees(_orders(3, 0), CFG)
    assert fees.stamp_duty == 0
    assert fees.n_buys == 3


def test_full_month_matches_the_audit_arithmetic():
    """
    The numbers quoted in the plan: 8 actions/month at Rp2.5jt each.
    Spread across days costs Rp88,000; batched costs Rp58,000.
    """
    spread = estimate_fees(_orders(4, 4), CFG, sell_days=4)
    batched = estimate_fees(_orders(4, 4), CFG, sell_days=1)

    assert spread.total == pytest.approx(88_000)
    assert batched.total == pytest.approx(58_000)
    assert spread.pct_of(CAPITAL) == pytest.approx(0.88)
    assert batched.pct_of(CAPITAL) == pytest.approx(0.58)


def test_sell_days_cannot_exceed_sell_count():
    fees = estimate_fees(_orders(0, 2), CFG, sell_days=99)
    assert fees.stamp_duty == 20_000


def test_zero_and_negative_values_are_ignored():
    fees = estimate_fees(
        [{"action": "BUY", "rupiah": 0}, {"action": "SELL", "rupiah": -5}], CFG
    )
    assert fees.total == 0
    assert fees.n_buys == 0 and fees.n_sells == 0


def test_round_trip_cost_includes_the_stamp():
    assert round_trip_cost(2_500_000, CFG) == pytest.approx(2_500_000 * 0.0048 + 10_000)


def test_breakeven_move_is_larger_for_smaller_positions():
    """The fixed stamp hurts small trades disproportionately -- worth surfacing."""
    small = breakeven_move_pct(500_000, CFG)
    large = breakeven_move_pct(5_000_000, CFG)
    assert small > large
    assert small == pytest.approx(0.48 + 2.0, abs=0.01)  # 0.48% rates + Rp10k/Rp500k


def test_config_reads_from_settings(settings_mock):
    cfg = FeeConfig.from_settings(settings_mock)
    assert cfg.buy_fee == 0.0019
    assert cfg.sell_fee == 0.0029
    assert cfg.stamp_duty_rp == 10_000
    assert cfg.lot_size == 100


def test_as_dict_exposes_display_fields():
    d = estimate_fees(_orders(2, 2), CFG, sell_days=1).as_dict(CAPITAL)
    for key in ("buy_fee", "sell_fee", "stamp_duty", "total",
                "pct_of_capital", "stamp_saving_if_batched"):
        assert key in d
