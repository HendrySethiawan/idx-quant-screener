import numpy as np
import pandas as pd
import pytest

from market.liquidity import LiquidityConfig, assess
from market.regime import Regime, above_sma, assess_regime
from portfolio.holdings import (Holding, diff_to_target, load_holdings,
                                portfolio_value, save_holdings)

CFG = LiquidityConfig()


# ------------------------------------------------------------------- liquidity
def test_zero_volume_name_is_rejected():
    """WIKA showed a median daily traded value of Rp 0."""
    v = assess("WIKA.JK", 0.0, 2_500_000, CFG)
    assert not v.ok
    assert "no trading volume" in v.reason


def test_thin_name_is_rejected_with_its_number():
    """BBSI trades about Rp 4.2 juta a day."""
    v = assess("BBSI.JK", 4_200_000, 2_500_000, CFG)
    assert not v.ok
    assert "4,200,000" in v.reason


def test_borderline_name_below_floor_is_rejected():
    """BNLI at about Rp 119 juta/day sits under the Rp 250 juta floor."""
    v = assess("BNLI.JK", 119_000_000, 2_500_000, CFG)
    assert not v.ok


def test_liquid_name_passes():
    v = assess("BBRI.JK", 500_000_000_000, 2_500_000, CFG)
    assert v.ok
    assert v.label == "ok"


def test_position_capped_at_one_percent_of_daily_value():
    """Passes the floor but the position is too large relative to volume."""
    v = assess("X.JK", 300_000_000, 20_000_000, CFG)
    assert not v.ok
    assert "more than" in v.reason


def test_pct_of_daily_value_is_reported():
    v = assess("BBRI.JK", 1_000_000_000, 2_500_000, CFG)
    assert v.pct_of_daily_value == pytest.approx(0.25)


def test_config_from_settings(settings_mock):
    cfg = LiquidityConfig.from_settings(settings_mock)
    assert cfg.min_median_daily_value_rp == 250_000_000


# ---------------------------------------------------------------------- regime
def _series(values):
    idx = pd.date_range("2023-01-02", periods=len(values), freq="B")
    return pd.Series(values, index=idx)


def test_above_sma_true_for_rising_series():
    assert above_sma(_series(np.linspace(100, 200, 300)), 200) is True


def test_above_sma_false_for_falling_series():
    assert above_sma(_series(np.linspace(200, 100, 300)), 200) is False


def test_above_sma_none_when_history_too_short():
    assert above_sma(_series(np.linspace(100, 200, 50)), 200) is None


def test_both_signals_on_gives_full_deployment():
    rising = _series(np.linspace(100, 200, 300))     # IHSG up
    falling_fx = _series(np.linspace(17000, 16000, 300))  # rupiah strengthening
    r = assess_regime(rising, falling_fx)
    assert r.label == "RISK-ON"
    assert r.deploy_pct == 1.00


def test_both_signals_off_reduces_deployment():
    falling = _series(np.linspace(200, 100, 300))
    rising_fx = _series(np.linspace(16000, 18000, 300))   # rupiah weakening
    r = assess_regime(falling, rising_fx)
    assert r.label == "RISK-OFF"
    assert r.deploy_pct == 0.30


def test_mixed_signals_sit_in_the_middle():
    rising = _series(np.linspace(100, 200, 300))
    rising_fx = _series(np.linspace(16000, 18000, 300))
    r = assess_regime(rising, rising_fx)
    assert r.label == "MIXED"
    assert r.deploy_pct == 0.60


def test_weakening_rupiah_is_risk_off():
    """A rising USD/IDR must count as a headwind, not a tailwind."""
    r = assess_regime(_series(np.linspace(100, 200, 300)),
                      _series(np.linspace(16000, 18000, 300)))
    fx_signal = next(s for s in r.signals if s.ticker == "IDR=X")
    assert fx_signal.risk_on is False


def test_no_data_defaults_to_cautious():
    r = assess_regime(None, None)
    assert r.label == "UNKNOWN"
    assert r.deploy_pct == 0.60


# -------------------------------------------------------------------- holdings
def test_roundtrip_lots_and_cost(tmp_path):
    path = tmp_path / "h.yaml"
    save_holdings([Holding("BBRI.JK", lots=7, avg_price=4100.0)], path)
    loaded = load_holdings(path)
    assert loaded[0].ticker == "BBRI.JK"
    assert loaded[0].lots == 7
    assert loaded[0].avg_price == 4100.0
    assert loaded[0].shares == 700


def test_legacy_ticker_list_still_loads(tmp_path):
    path = tmp_path / "h.yaml"
    path.write_text("tickers: [BBRI.JK, BMRI.JK]\n", encoding="utf-8")
    loaded = load_holdings(path)
    assert [h.ticker for h in loaded] == ["BBRI.JK", "BMRI.JK"]
    assert loaded[0].avg_price is None


def test_missing_file_returns_empty(tmp_path):
    assert load_holdings(tmp_path / "nope.yaml") == []


def test_malformed_file_degrades_quietly(tmp_path):
    path = tmp_path / "h.yaml"
    path.write_text("this: [is: broken\n", encoding="utf-8")
    assert load_holdings(path) == []


def test_unrealized_pnl():
    h = Holding("BBRI.JK", lots=7, avg_price=4000.0)
    assert h.cost_basis == 2_800_000
    assert h.unrealized(4150.0) == pytest.approx(105_000)
    assert h.unrealized_pct(4150.0) == pytest.approx(3.75)


def test_unrealized_is_none_without_cost_basis():
    assert Holding("BBRI.JK", lots=7).unrealized(4150.0) is None


def test_portfolio_value_skips_unpriced_names():
    holdings = [Holding("A.JK", 1, 100.0), Holding("B.JK", 2, 100.0)]
    assert portfolio_value(holdings, {"A.JK": 200.0}) == 20_000


def test_diff_to_target():
    holdings = [Holding("BBRI.JK", 5), Holding("WIKA.JK", 3)]
    d = diff_to_target(holdings, ["BBRI.JK", "TLKM.JK"])
    assert d["buy"] == ["TLKM.JK"]
    assert d["sell"] == ["WIKA.JK"]
    assert d["hold"] == ["BBRI.JK"]
