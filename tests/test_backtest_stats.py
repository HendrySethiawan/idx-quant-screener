"""
The tests that encode a specific mistake so it cannot be made again.

Removing the regime ladder measured +26.8pp of CAGR at t = 2.99 -- past every
usual bar -- and the entire effect sat in the second half of the window. It was
one market episode sampled weekly, and it nearly became a recommendation to
double a real person's market exposure.

So the first test here is not "does the arithmetic work". It is: an effect that
lives in one half must be reported unstable EVEN WHEN its t-statistic is
convincing. Everything else in this file supports that one.
"""
import numpy as np
import pandas as pd
import pytest

from backtest.stats import (Z95, lead_lag, paired_significance, split_half,
                            verdict)


def _curve(weekly_returns, start=100.0):
    idx = pd.bdate_range("2021-01-04", periods=len(weekly_returns), freq="W-MON")
    return pd.Series(start * np.cumprod(1 + np.asarray(weekly_returns, float)),
                     index=idx)


def _flat(n=260, r=0.002):
    return _curve([r] * n)


# ==================================================================== THE ONE
def test_an_effect_in_only_one_half_is_unstable_however_good_its_t():
    """
    THIS is the session's error, encoded. Nothing in the first half, a large
    steady edge in the second: a t-statistic finds it overwhelming, and it is one
    episode.
    """
    # Shaped like the real thing: a shade NEGATIVE in the first half, then a large
    # positive run. That was -0.6pp against +61.4pp.
    n = 260
    base = _flat(n)
    boosted = _curve([0.0019] * (n // 2) + [0.012] * (n - n // 2))

    sig = paired_significance(base, boosted)
    assert sig["distinguishable"] is True, "fixture must fool the t-test"
    assert abs(sig["t"]) > Z95

    half = split_half(base, boosted)
    assert half["sign_stable"] is False
    assert verdict(base, boosted)["verdict"].startswith("unstable")


def test_agreeing_in_sign_is_not_enough_to_be_called_stable():
    """
    The subtler half of the same lesson. +0.1 then +61.4 agrees in direction and
    is still one episode; sign alone would wave it through.
    """
    n = 260
    base = _flat(n, 0.002)
    lopsided = _curve([0.00201] * (n // 2) + [0.012] * (n - n // 2))

    half = split_half(base, lopsided)
    assert half["sign_stable"] is True          # direction agrees...
    assert half["concentration"] < 0.25         # ...and it is still one episode
    assert half["even"] is False
    assert "concentrated" in verdict(base, lopsided)["verdict"]


def test_a_steady_edge_in_both_halves_is_not_called_unstable():
    """The control: the same size of effect, spread evenly, must survive."""
    base, better = _flat(260, 0.002), _flat(260, 0.007)
    half = split_half(base, better)
    assert half["sign_stable"] is True
    assert verdict(base, better)["verdict"] == "distinguishable"


# ============================================================ known answers
def test_identical_curves_are_reported_as_never_binding():
    same = _flat()
    sig = paired_significance(same, same.copy())
    assert sig["gap_pp"] == pytest.approx(0.0, abs=1e-9)
    assert sig["distinguishable"] is False
    assert "identical" in sig["reason"]
    assert verdict(same, same.copy())["verdict"] == "never binds"


def test_the_gap_is_the_annualised_difference():
    """Checked against arithmetic done by hand, not against the function itself."""
    base, better = _flat(260, 0.000), _flat(260, 0.001)
    expected = ((1.001 ** 52) - 1) * 100          # ~5.33 pp
    assert paired_significance(base, better)["gap_pp"] == pytest.approx(expected, rel=1e-6)


def test_the_cadence_changes_the_annualisation():
    base, better = _flat(120, 0.000), _flat(120, 0.001)
    weekly = paired_significance(base, better, periods_per_year=52)["gap_pp"]
    monthly = paired_significance(base, better, periods_per_year=12)["gap_pp"]
    assert weekly > monthly


def test_a_null_result_reports_what_it_could_have_seen():
    """
    "No effect" and "not enough data" are different findings, and the report used
    to show neither. `mde_pp` is what separates them.
    """
    rng = np.random.default_rng(0)
    noise = rng.normal(0.002, 0.03, 260)
    base, other = _curve(noise), _curve(noise + rng.normal(0, 0.03, 260))
    sig = paired_significance(base, other)
    assert sig["distinguishable"] is False
    assert sig["mde_pp"] > 0, "a null result must say what it could have detected"


def test_paired_beats_unpaired():
    """
    The reason the test is paired at all: shared market movement cancels in the
    difference, so a correlated pair resolves a far smaller effect.
    """
    rng = np.random.default_rng(7)
    market = rng.normal(0.002, 0.03, 260)
    edge = 0.001

    correlated = paired_significance(_curve(market), _curve(market + edge))
    independent = paired_significance(
        _curve(rng.normal(0.002, 0.03, 260)),
        _curve(rng.normal(0.002 + edge, 0.03, 260)))
    assert correlated["mde_pp"] < independent["mde_pp"]


# ================================================================ degenerate
@pytest.mark.parametrize("a,b", [
    (None, None),
    (pd.Series(dtype=float), pd.Series(dtype=float)),
    (_flat(2), _flat(2)),
])
def test_degenerate_input_says_cannot_tell_rather_than_raising(a, b):
    sig = paired_significance(a, b)
    assert sig["gap_pp"] is None and sig["distinguishable"] is False
    assert verdict(a, b)["verdict"] == "cannot tell"


def test_curves_that_do_not_overlap_in_time_cannot_be_compared():
    a = _curve([0.002] * 60)
    b = _curve([0.002] * 60)
    b.index = b.index + pd.DateOffset(years=20)
    assert paired_significance(a, b)["gap_pp"] is None


def test_a_half_that_is_exactly_flat_is_not_a_disagreement():
    """Doing nothing in one half is not the same as pointing the other way."""
    n = 200
    base = _flat(n, 0.002)
    same_then_better = _curve([0.002] * (n // 2) + [0.002] * (n - n // 2))
    assert split_half(base, same_then_better)["sign_stable"] is True


# ======================================================== A DIFFERENT WAY TO BE FOOLED
# The same lesson, arriving through the clock rather than through the calendar.
#
# EIDO leads the IHSG next-day at about +0.15, and it survives controlling for the
# index's own move and the rupiah: an incremental t of +8.3 over 1,160 days, same sign
# in both halves. By every test above it is the most solid effect in this project.
#
# It is also untouchable. Jakarta closes hours before New York opens, so a date's ETF
# session happens after that date's index close; the information reaches EIDO while
# Jakarta is shut and Jakarta prices it into the OPENING PRINT. Measured on the real
# series: +0.26 in the overnight gap, +0.01 from the open onward. Only the second is
# something a person can place an order against.

def _market(gap_r, intra_r, start=1000.0):
    """Open and close series built from per-day gap and open-to-close returns."""
    closes, opens, c = [], [], start
    for g, i in zip(gap_r, intra_r):
        o = c * (1 + g)
        c = o * (1 + i)
        opens.append(o)
        closes.append(c)
    idx = pd.bdate_range("2021-01-04", periods=len(closes))
    return pd.Series(closes, index=idx), pd.Series(opens, index=idx)


def _leader(returns, start=100.0):
    idx = pd.bdate_range("2021-01-04", periods=len(returns))
    return pd.Series(start * np.cumprod(1 + np.asarray(returns, float)), index=idx)


def _shifted_into(lr, strength=0.8):
    """`out[k] = strength * lr[k-1]` -- yesterday's leader move, landing today."""
    out = np.zeros_like(lr)
    out[1:] = strength * lr[:-1]
    return out


# ==================================================================== THE ONE
def test_a_lead_that_lands_in_the_opening_print_is_not_capturable():
    """
    THIS is the EIDO result, encoded. The whole effect is in the gap between
    yesterday's close and today's open -- already priced by the time anyone can act.
    The close-to-close correlation is large and the answer is still no.
    """
    rng = np.random.default_rng(0)
    n = 600
    lr = rng.normal(0, 0.02, n)
    gap = _shifted_into(lr)                       # everything lands overnight
    intra = rng.normal(0, 0.004, n)               # and nothing after the open

    close, opens = _market(gap, intra)
    out = lead_lag(_leader(lr), close, opens)

    assert out["next_day"] > 0.5, "fixture must look like a strong lead"
    assert out["gap"] > 0.8
    assert abs(out["intraday"]) < 0.15
    assert out["capturable"] is False
    assert "opening print" in out["verdict"]


def test_a_lead_that_survives_the_open_is_capturable():
    """The control. Same construction, moved to the half of the day you can trade."""
    rng = np.random.default_rng(1)
    n = 600
    lr = rng.normal(0, 0.02, n)
    close, opens = _market(rng.normal(0, 0.004, n), _shifted_into(lr))
    out = lead_lag(_leader(lr), close, opens)

    assert out["intraday"] > 0.8
    assert out["capturable"] is True
    assert out["verdict"] == "capturable"


def test_an_intraday_edge_in_only_one_half_is_not_capturable():
    """
    The calendar lesson and the clock lesson at once: reachable is not enough, it
    also has to be there for the whole window.
    """
    rng = np.random.default_rng(2)
    n = 600
    lr = rng.normal(0, 0.02, n)
    intra = _shifted_into(lr)
    intra[n // 2:] = rng.normal(0, 0.004, n - n // 2)      # edge dies halfway

    close, opens = _market(rng.normal(0, 0.004, n), intra)
    out = lead_lag(_leader(lr), close, opens)

    assert out["intraday"] > 0, "the whole-window figure still looks positive"
    assert out["intraday_first"] > out["intraday_second"]
    assert out["capturable"] is False


def test_noise_is_not_mistaken_for_a_lead():
    rng = np.random.default_rng(3)
    n = 600
    close, opens = _market(rng.normal(0, 0.01, n), rng.normal(0, 0.01, n))
    out = lead_lag(_leader(rng.normal(0, 0.02, n)), close, opens)
    assert out["capturable"] is False


def test_without_opens_reachability_is_unknown_never_assumed():
    """
    An unmeasured question must not answer itself. `capturable` stays False and the
    verdict says why, rather than reporting a close-to-close lead as though it were
    something you could place an order against.
    """
    rng = np.random.default_rng(4)
    n = 400
    lr = rng.normal(0, 0.02, n)
    close, _ = _market(_shifted_into(lr), rng.normal(0, 0.004, n))

    out = lead_lag(_leader(lr), close)
    assert out["next_day"] > 0.5
    assert out["gap"] is None and out["intraday"] is None
    assert out["capturable"] is False
    assert out["verdict"] == "leads, but reachability was not measured"


@pytest.mark.parametrize("leader,target", [
    (None, None),
    (pd.Series(dtype=float), pd.Series(dtype=float)),
    (_flat(2), _flat(2)),
])
def test_lead_lag_degenerate_input_says_cannot_tell_rather_than_raising(leader, target):
    out = lead_lag(leader, target)
    assert out["verdict"] == "cannot tell" and out["capturable"] is False


def test_a_leader_that_never_overlaps_the_target_cannot_be_compared():
    lr = _flat(60)
    target = _flat(60)
    target.index = target.index + pd.DateOffset(years=20)
    assert lead_lag(lr, target)["same_day"] is None
