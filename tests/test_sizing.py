"""
Sizing must never emit an order the user cannot fill.

The concrete failure this guards: UNTR ranked #4 in the real screener output, but
one lot costs Rp 2,292,500 against a Rp 2 juta slot.
"""
import pytest

from portfolio.sizing import (Allocation, affordable_lots, choose_allocation,
                              lot_price)

CAPITAL = 10_000_000

# Real prices pulled from the cache.
CANDIDATES = [
    {"ticker": "BBRI.JK", "price": 4150.0},
    {"ticker": "BMRI.JK", "price": 4780.0},
    {"ticker": "UNTR.JK", "price": 22925.0},   # 1 lot = Rp2,292,500
    {"ticker": "TLKM.JK", "price": 2610.0},
    {"ticker": "ASII.JK", "price": 4900.0},
    {"ticker": "ITMG.JK", "price": 22100.0},   # 1 lot = Rp2,210,000
    {"ticker": "TAPG.JK", "price": 1085.0},
]


def test_every_order_is_a_whole_lot():
    alloc = choose_allocation(CANDIDATES, CAPITAL)
    assert alloc.positions
    for p in alloc.positions:
        assert p.shares % 100 == 0
        assert p.lots >= 1
        assert p.rupiah == pytest.approx(p.lots * p.price * 100)


def test_never_spends_more_than_budget():
    alloc = choose_allocation(CANDIDATES, CAPITAL)
    assert alloc.invested <= CAPITAL
    assert alloc.cash_left >= 0


def test_unaffordable_lot_is_rejected_with_a_reason():
    """
    At 5 positions the slot is Rp2,000,000 and UNTR's Rp2,292,500 lot cannot fit,
    which is the exact case where the old screener still recommended it.

    Note the rule is relative: at 4 positions the slot is Rp2.5 juta and UNTR is
    holdable, so the sizer is allowed to include it there. The bug was never
    "UNTR is too expensive", it was "nobody checked the slot".
    """
    alloc = choose_allocation(CANDIDATES, CAPITAL, min_positions=5, max_positions=5)
    assert alloc.n_positions == 5
    assert "UNTR.JK" not in alloc.tickers()
    assert "ITMG.JK" not in alloc.tickers()
    assert "2,292,500" in alloc.rejected["UNTR.JK"]


def test_no_position_can_exceed_its_slot_by_more_than_the_tolerance():
    alloc = choose_allocation(CANDIDATES, CAPITAL)
    slot = alloc.budget / alloc.n_positions
    for p in alloc.positions:
        assert p.lot_price <= slot * 1.0 + 1e-6


def test_leftover_goes_to_the_most_underweight_name():
    """
    Spending the change on the top-ranked name would buy full deployment at the
    cost of a lopsided book, so the sizer tops up the furthest-behind position.
    """
    alloc = choose_allocation(CANDIDATES, CAPITAL, min_positions=4, max_positions=4)
    slot = alloc.budget / alloc.n_positions
    top = alloc.positions[0]
    assert top.rupiah <= slot * 1.5


def test_expensive_name_is_allowed_when_capital_is_large():
    """The rule is relative to slot size, not an absolute price blacklist."""
    alloc = choose_allocation(CANDIDATES, 200_000_000)
    assert "UNTR.JK" in alloc.tickers()


def test_deploy_pct_limits_the_budget():
    full = choose_allocation(CANDIDATES, CAPITAL, deploy_pct=1.0)
    half = choose_allocation(CANDIDATES, CAPITAL, deploy_pct=0.5)
    assert half.invested < full.invested
    assert half.invested <= CAPITAL * 0.5


def test_risk_off_deploys_nothing():
    alloc = choose_allocation(CANDIDATES, CAPITAL, deploy_pct=0.0)
    assert alloc.positions == []
    assert alloc.invested == 0


def test_position_count_is_chosen_within_bounds():
    alloc = choose_allocation(CANDIDATES, CAPITAL, min_positions=3, max_positions=6)
    assert 3 <= alloc.n_positions <= 6


def test_chosen_allocation_beats_neighbours_on_score():
    """The selected N must actually maximise deployed - penalty * deviation."""
    from portfolio.sizing import _allocate_for_n
    chosen = choose_allocation(CANDIDATES, CAPITAL, deviation_penalty=0.5)
    chosen_score = chosen.deployed_pct - 0.5 * chosen.max_weight_error

    for n in range(3, 7):
        alt = _allocate_for_n(CANDIDATES, CAPITAL, n, 100, 1.0)
        if alt is not None:
            assert chosen_score >= alt.deployed_pct - 0.5 * alt.max_weight_error - 1e-9


def test_weights_sum_to_one():
    alloc = choose_allocation(CANDIDATES, CAPITAL)
    assert sum(p.weight for p in alloc.positions) == pytest.approx(1.0)


def test_weight_error_is_reported_not_hidden():
    """Lot granularity is a real cost; the caller must be able to see it."""
    alloc = choose_allocation(CANDIDATES, CAPITAL)
    assert alloc.max_weight_error >= 0
    assert all(hasattr(p, "weight_error") for p in alloc.positions)


def test_leftover_cash_is_put_to_work():
    alloc = choose_allocation(CANDIDATES, CAPITAL)
    cheapest_lot = min(p.lot_price for p in alloc.positions)
    assert alloc.cash_left < cheapest_lot, "leftover could still have bought a lot"


def test_tiny_capital_falls_back_to_one_position():
    alloc = choose_allocation(CANDIDATES, 200_000)
    assert len(alloc.positions) == 1
    assert alloc.positions[0].shares % 100 == 0


def test_capital_too_small_for_any_lot():
    alloc = choose_allocation([{"ticker": "X.JK", "price": 9000.0}], 50_000)
    assert alloc.positions == []
    assert alloc.cash_left == 50_000


def test_settings_drive_the_bounds(settings_mock):
    settings_mock.account = {**settings_mock.account, "min_positions": 2, "max_positions": 2}
    alloc = choose_allocation(CANDIDATES, CAPITAL, settings=settings_mock)
    assert alloc.n_positions == 2


@pytest.mark.parametrize("price,budget,expected", [
    (4150.0, 2_000_000, 4),
    (22925.0, 2_000_000, 0),
    (1085.0, 2_000_000, 18),
])
def test_affordable_lots(price, budget, expected):
    assert affordable_lots(budget, price) == expected


def test_lot_price():
    assert lot_price(22925.0) == 2_292_500


def test_tiny_slots_are_refused_so_fees_do_not_dominate():
    """
    A Rp3 juta risk-off budget must not become 6 positions of Rp500,000.
    The Rp10,000 stamp alone is 2% of a Rp500,000 position.
    """
    alloc = choose_allocation(CANDIDATES, 10_000_000, deploy_pct=0.30,
                              min_position_rp=1_000_000)
    assert alloc.n_positions <= 3
    for p in alloc.positions:
        assert p.rupiah >= 400_000


def test_min_position_can_be_disabled():
    alloc = choose_allocation(CANDIDATES, 10_000_000, deploy_pct=0.30,
                              min_position_rp=0)
    assert alloc.n_positions >= 3


# ======================================================================
# Equal RISK, not equal rupiah.
#
# Every position used to get budget/n, so the wildest name in the book carried
# the most risk purely because it moved most. On the live book that spread
# per-position risk from 0.5% to 0.9% of capital across four names that were all
# meant to be the same size. This does not reduce risk -- it stops the book
# concentrating by accident in whichever name happened to be most volatile.
# ======================================================================
def _mixed(atrs, price=1000.0):
    """Same price, wildly different volatility."""
    return [{"ticker": f"{k}.JK", "price": price, "atr_rp": a}
            for k, a in atrs.items()]


def _risk_spread(alloc, cands, k_atr=2.5):
    """Rupiah at risk per position, as a fraction of the largest."""
    atr = {c["ticker"]: c["atr_rp"] for c in cands}
    risk = [p.shares * k_atr * atr[p.ticker] for p in alloc.positions]
    return max(risk) / min(risk)


def test_a_calm_name_gets_more_money_than_a_wild_one():
    cands = _mixed({"CALM": 10.0, "MID": 30.0, "WILD": 90.0})
    alloc = choose_allocation(cands, 60_000_000, 1.0, min_positions=3,
                              max_positions=3, min_position_rp=0.0)
    by = {p.ticker: p.rupiah for p in alloc.positions}
    assert by["CALM.JK"] > by["MID.JK"] > by["WILD.JK"]


def test_risk_comes_out_far_more_even_than_the_money():
    """
    The point of the change, measured against the thing it replaced rather than
    against a threshold somebody picked. Equal rupiah on a book whose ATRs run
    10 / 30 / 90 spreads risk 9x by construction; whole lots keep equal risk from
    being exactly 1.0, so the honest claim is the ratio between the two.
    """
    atrs = {"CALM": 10.0, "MID": 30.0, "WILD": 90.0}
    cands = _mixed(atrs)
    budget = 60_000_000

    risk_sized = choose_allocation(cands, budget, 1.0, min_positions=3,
                                   max_positions=3, min_position_rp=0.0)
    # The same book with nothing to size on: the old equal-rupiah behaviour.
    flat = choose_allocation([{k: v for k, v in c.items() if k != "atr_rp"}
                              for c in cands],
                             budget, 1.0, min_positions=3, max_positions=3,
                             min_position_rp=0.0)

    assert _risk_spread(flat, cands) > 8.0, "fixture does not exercise the change"
    assert _risk_spread(risk_sized, cands) < _risk_spread(flat, cands) / 5


def test_the_budget_is_still_respected():
    cands = _mixed({"CALM": 10.0, "MID": 30.0, "WILD": 90.0})
    budget = 60_000_000
    alloc = choose_allocation(cands, budget, 1.0, min_positions=3,
                              max_positions=3, min_position_rp=0.0)
    assert sum(p.rupiah for p in alloc.positions) <= budget


def test_every_position_is_still_whole_lots():
    cands = _mixed({"CALM": 10.0, "MID": 30.0, "WILD": 90.0})
    alloc = choose_allocation(cands, 60_000_000, 1.0, min_positions=3,
                              max_positions=3, min_position_rp=0.0)
    for p in alloc.positions:
        assert p.shares == p.lots * 100
        assert p.lots >= 1


def test_a_name_with_no_atr_still_gets_a_position():
    """
    `stop_level` refuses a stop without a measurable range, so those names cannot
    be risk-sized. They take an ordinary slot rather than being dropped or handed
    the whole book by a divide-by-nothing.
    """
    cands = [{"ticker": "A.JK", "price": 1000.0, "atr_rp": 20.0},
             {"ticker": "B.JK", "price": 1000.0, "atr_rp": None},
             {"ticker": "C.JK", "price": 1000.0, "atr_rp": 0.0}]
    alloc = choose_allocation(cands, 60_000_000, 1.0, min_positions=3,
                              max_positions=3, min_position_rp=0.0)
    assert len(alloc.positions) == 3
    for p in alloc.positions:
        assert p.rupiah > 0


def test_with_no_atr_anywhere_it_degrades_to_equal_rupiah():
    """The behaviour it replaced, still reachable when there is nothing to size on."""
    cands = [{"ticker": f"{c}.JK", "price": 1000.0} for c in "ABC"]
    alloc = choose_allocation(cands, 60_000_000, 1.0, min_positions=3,
                              max_positions=3, min_position_rp=0.0)
    values = [p.rupiah for p in alloc.positions]
    assert max(values) == min(values)


def test_the_stop_cap_stops_a_wild_name_being_sized_to_nothing():
    """
    A name whose 2.5xATR exceeds `max_stop_pct` is stopped out at the cap, so it
    must be sized against the cap too -- otherwise the position is smaller than
    the risk it actually carries.
    """
    from portfolio.sizing import _risk_per_share

    # 2.5 x 100 = 250, but 15% of 1000 is 150.
    assert _risk_per_share({"price": 1000.0, "atr_rp": 100.0}, 2.5, 15.0) == 150.0
    # Under the cap, the ATR governs.
    assert _risk_per_share({"price": 1000.0, "atr_rp": 20.0}, 2.5, 15.0) == 50.0


def test_target_weight_is_the_risk_implied_one():
    """`max_weight_error` must keep measuring distance from the INTENDED book."""
    cands = _mixed({"CALM": 10.0, "WILD": 90.0})
    alloc = choose_allocation(cands, 60_000_000, 1.0, min_positions=2,
                              max_positions=2, min_position_rp=0.0)
    by = {p.ticker: p.target_weight for p in alloc.positions}
    assert by["CALM.JK"] > by["WILD.JK"]
    assert abs(sum(by.values()) - 1.0) < 0.02
