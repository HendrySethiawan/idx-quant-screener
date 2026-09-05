"""
The exit rules: a stop, a staged trim, and a trailing runner.

Pinned to the live position wherever possible. The journal holds one trade --
SRTG, 10 lots bought 26 Aug 2026 at Rp1,935, fee-inclusive cost Rp1,938.68, ATR
Rp55.42 -- and it was already through its stop at the 4 Sept close of Rp1,795.
A test that reproduces that arithmetic is checkable against the terminal by eye.
"""
import numpy as np
import pandas as pd
import pytest

from portfolio.exits import (EXIT, HOLD, NO_STOP, TRIM, ExitConfig, break_even_level,
                             build_ladder, cooldown, entry_risk, open_risk,
                             plan_for, plans_for, position_history, stop_level,
                             trailing_level)
from portfolio.fees import FeeConfig

FEES = FeeConfig()
CFG = ExitConfig()
CAPITAL = 10_000_000

# The real position, to the rupiah.
SRTG_ENTRY = 1938.6765
SRTG_ATR = 55.42
SRTG_LOTS = 10


def _series(values, start="2026-08-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series([float(v) for v in values], index=idx)


# ------------------------------------------------------------------- the stop
def test_stop_is_k_atr_below_entry():
    stop = stop_level(SRTG_ENTRY, SRTG_ATR, CFG, FEES, SRTG_LOTS * 100 * SRTG_ENTRY)
    assert stop == pytest.approx(SRTG_ENTRY - 2.5 * SRTG_ATR, abs=0.01)
    assert stop == pytest.approx(1800.13, abs=0.02)


def test_stop_is_never_inside_the_round_trip_cost():
    """
    A stop closer than the fees is a guaranteed loss: the position has to move
    that far just to be square, so the stop fires on the cost, not on being wrong.
    """
    value = 10 * 100 * 2000.0
    tiny_atr = 0.5                       # 0.025% of the price
    stop = stop_level(2000.0, tiny_atr, CFG, FEES, value)

    naive = 2000.0 - 2.5 * tiny_atr
    assert stop < naive                  # the clamp moved it further away
    round_trip_pct = (value * (FEES.buy_fee + FEES.sell_fee)
                      + FEES.stamp_duty_rp) / value
    assert (2000.0 - stop) >= 2000.0 * round_trip_pct + tiny_atr - 1e-9


def test_stop_is_never_wider_than_the_cap():
    """INET's 6.7% ATR would put a 2.5x stop 16.8% away. The cap holds it at 15%."""
    stop = stop_level(366.0, 24.51, CFG, FEES, 30 * 100 * 366.0)
    assert stop == pytest.approx(366.0 * 0.85, abs=0.01)


def test_no_stop_when_the_price_cannot_move():
    """WIKA's ATR is 0.00 and GOTO's 0.01. A stop from those sits on the entry."""
    for atr in (0.0, None, np.nan):
        assert stop_level(204.0, atr, CFG, FEES, 1_000_000) is None


def test_break_even_is_above_the_entry_price():
    """
    Entry already paid 0.19%; getting out pays 0.29% and the stamp. Stopping at
    the entry price is a small loss dressed up as break-even.
    """
    be = break_even_level(SRTG_ENTRY, 600, FEES)
    assert be > SRTG_ENTRY
    assert be == pytest.approx(1965.0, abs=1.0)


# ----------------------------------------------------------------- the ladder
def test_ladder_is_whole_lots_and_leaves_a_runner():
    risk = 2.5 * SRTG_ATR
    stages = build_ladder(SRTG_LOTS, SRTG_ENTRY, risk, CFG, FEES)

    assert len(stages) == 2
    assert [s.lots for s in stages] == [4, 3]
    assert sum(s.lots for s in stages) < SRTG_LOTS      # 3 lots run
    for s in stages:
        assert s.shares == s.lots * 100
    assert stages[0].level_rp == pytest.approx(SRTG_ENTRY + risk, abs=0.01)
    assert stages[1].level_rp == pytest.approx(SRTG_ENTRY + 2 * risk, abs=0.01)


def test_ladder_quotes_the_stamp_both_ways():
    """
    The stamp is per sell DAY, so the same trim costs Rp2,410 sharing a day and
    Rp12,410 alone. Both are carried; quoting one would hide or overstate it.
    """
    stages = build_ladder(SRTG_LOTS, SRTG_ENTRY, 2.5 * SRTG_ATR, CFG, FEES)
    s = stages[0]
    assert s.cost_alone_rp == pytest.approx(s.cost_batched_rp + FEES.stamp_duty_rp)
    assert s.cost_batched_rp == pytest.approx(s.proceeds_rp * FEES.sell_fee)


def test_every_trim_in_a_real_ladder_can_pay_for_itself():
    """
    The stamp sets the floor: 10,000 / (0.025 - 0.0029) makes the smallest viable
    trim about Rp452,000. A 20-lot Rp1,000 position clears it comfortably.
    """
    stages = build_ladder(20, 1000.0, 80.0, CFG, FEES)
    assert len(stages) > 1
    for s in stages:
        assert s.cost_alone_pct <= CFG.max_trim_cost_pct


def test_a_trim_that_cannot_pay_for_itself_is_rolled_forward():
    """
    A Rp340,000 position cannot be sliced: one lot returns Rp85,000 and costs
    Rp10,247 to sell alone, 12%. The lots roll forward rather than vanishing, and
    what comes back is one decision covering the whole position.
    """
    stages = build_ladder(4, 790.0, 60.0, CFG, FEES)
    assert len(stages) == 1
    assert stages[0].lots == 4          # nothing was lost on the way


def test_a_position_too_small_to_stage_is_one_decision():
    """
    A Rp340,000 position cannot be sliced at all: every rung is smaller than the
    Rp452,000 the stamp needs, so it comes back as one decision covering the lot.
    """
    stages = build_ladder(4, 790.0, 60.0, CFG, FEES)
    assert len(stages) == 1
    assert stages[0].lots == 4

    plan = plan_for("BUVA.JK", 4, 790.0, _series([790] * 40), CFG, FEES, atr_rp=45.0)
    assert plan.staged is False
    assert plan.runner_lots == 0


def test_one_trim_plus_a_runner_still_counts_as_staged():
    """
    EMAS is 2 lots in a Rp2.5 juta slot. One rung and one runner is a real staged
    exit -- the test for `staged` is whether anything is left running, not how
    many rungs there are.
    """
    plan = plan_for("EMAS.JK", 2, 8675.0, _series([8675] * 40), CFG, FEES,
                    atr_rp=437.0, capital_rp=CAPITAL)
    assert plan.staged is True
    assert plan.runner_lots == 1
    assert sum(s.lots for s in plan.stages) == 1


def test_ladder_never_sells_more_than_the_position():
    for lots in range(1, 40):
        stages = build_ladder(lots, 2000.0, 150.0, CFG, FEES)
        assert sum(s.lots for s in stages) <= lots


def test_a_rung_is_done_only_when_its_lots_have_actually_left():
    """
    Marked from the lot count, never from the price high. A level reached last
    Tuesday and not acted on is still a level you can act on today, and inferring
    "done" from the high would silently retire an instruction you never followed.
    """
    risk = 2.5 * SRTG_ATR
    untouched = build_ladder(SRTG_LOTS, SRTG_ENTRY, risk, CFG, FEES, sold_lots=0)
    assert [s.done for s in untouched] == [False, False]

    trimmed = build_ladder(SRTG_LOTS, SRTG_ENTRY, risk, CFG, FEES, sold_lots=4)
    assert [s.done for s in trimmed] == [True, False]


def test_the_ladder_does_not_move_when_the_position_is_trimmed():
    """
    The reason the ladder is anchored to the original size. Rebuilt from the
    surviving 6 lots it would put its first rung at 2 lots and ask for a trim the
    price has already been through.
    """
    risk = 2.5 * SRTG_ATR
    before = build_ladder(SRTG_LOTS, SRTG_ENTRY, risk, CFG, FEES, sold_lots=0)
    after = build_ladder(SRTG_LOTS, SRTG_ENTRY, risk, CFG, FEES, sold_lots=4)
    assert [s.level_rp for s in before] == [s.level_rp for s in after]
    assert [s.lots for s in before] == [s.lots for s in after]


# ---------------------------------------------------------------- the verdict
def test_the_live_srtg_position_says_get_out():
    """
    Entry 1,938.68, ATR 55.42, stop 1,800.13, last close 1,795. The terminal has
    been silent about this since 4 September.
    """
    closes = _series([1935, 1960, 1900, 1880, 1850, 1820, 1810, 1795],
                     start="2026-08-26")
    plan = plan_for("SRTG.JK", SRTG_LOTS, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26",
                    high=closes, capital_rp=CAPITAL)

    assert plan.action == EXIT
    assert plan.action_lots == SRTG_LOTS
    assert plan.stop_rp == pytest.approx(1800.13, abs=0.02)
    assert plan.risk_rp == pytest.approx(138_546, abs=50)
    assert plan.risk_pct_of_capital == pytest.approx(1.39, abs=0.02)
    assert "1,800" in plan.reason


def test_a_price_at_the_first_target_says_trim():
    risk = 2.5 * SRTG_ATR
    target = SRTG_ENTRY + risk
    closes = _series([SRTG_ENTRY, target + 5], start="2026-08-26")
    plan = plan_for("SRTG.JK", SRTG_LOTS, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    capital_rp=CAPITAL)

    assert plan.action == TRIM
    assert plan.action_lots == 4
    assert "break-even" in plan.reason


def test_a_hand_trimmed_position_is_only_asked_for_the_difference():
    """
    The ladder states a TARGET holding at each price, not "sell the next rung".
    Sell 2 lots by hand and the plan must ask for the remaining 2, not another 4.
    """
    risk = 2.5 * SRTG_ATR
    target = SRTG_ENTRY + risk
    closes = _series([SRTG_ENTRY, target + 5], start="2026-08-26")
    plan = plan_for("SRTG.JK", 8, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    original_lots=SRTG_LOTS, capital_rp=CAPITAL)

    assert plan.action == TRIM
    assert plan.action_lots == 2          # down to the 6 the ladder wants


def test_a_price_that_gaps_through_both_rungs_takes_both():
    """
    IDX gaps. Acting on one rung a day would leave the second waiting on a price
    that has already been and gone.
    """
    risk = 2.5 * SRTG_ATR
    closes = _series([SRTG_ENTRY, SRTG_ENTRY + 2.4 * risk], start="2026-08-26")
    plan = plan_for("SRTG.JK", SRTG_LOTS, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    capital_rp=CAPITAL)

    assert plan.action == TRIM
    assert plan.action_lots == 7          # both rungs, leaving the 3-lot runner


def test_a_position_already_at_its_target_holds():
    """After stage 1 was taken, the same price must not ask for it again."""
    risk = 2.5 * SRTG_ATR
    closes = _series([SRTG_ENTRY, SRTG_ENTRY + 1.2 * risk], start="2026-08-26")
    plan = plan_for("SRTG.JK", 6, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    original_lots=SRTG_LOTS, capital_rp=CAPITAL)

    assert plan.action == HOLD
    assert plan.action_lots == 0


def test_a_price_between_the_levels_says_hold_and_names_both():
    closes = _series([SRTG_ENTRY, SRTG_ENTRY + 20], start="2026-08-26")
    plan = plan_for("SRTG.JK", SRTG_LOTS, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    capital_rp=CAPITAL)

    assert plan.action == HOLD
    assert plan.to_stop_pct > 0 and plan.to_next_pct > 0
    assert "stop" in plan.reason and "next trim" in plan.reason


def test_the_stop_beats_a_reached_target():
    """
    A gap through both levels in one session is possible on IDX. The stop is a
    decision already made; the target is one that has not been acted on.
    """
    risk = 2.5 * SRTG_ATR
    closes = _series([SRTG_ENTRY, SRTG_ENTRY + 2.2 * risk, SRTG_ENTRY - 3 * risk],
                     start="2026-08-26")
    plan = plan_for("SRTG.JK", SRTG_LOTS, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    capital_rp=CAPITAL)
    assert plan.action == EXIT


def test_a_blocked_price_returns_no_stop_and_quotes_the_reason():
    """
    Reuses `technical.unmeasurable_factors`' wording rather than inventing a
    second explanation for the same fact.
    """
    note = "atr_14: at the Rp50 floor on 34% of the last 42 sessions"
    plan = plan_for("GOTO.JK", 100, 50.0, _series([50] * 40), CFG, FEES,
                    atr_rp=0.0, price_note=note, capital_rp=CAPITAL)

    assert plan.action == NO_STOP
    assert note in plan.reason
    assert plan.stop_rp is None and plan.risk_rp is None


# ----------------------------------------------------------------- the trail
def test_the_trail_does_not_engage_before_the_first_trim():
    """
    The measured reason this rule exists: trailing from entry stopped out 74% of
    entries within two months against 44% for a fixed stop.
    """
    risk = 2.5 * SRTG_ATR
    closes = _series([SRTG_ENTRY, SRTG_ENTRY + 0.9 * risk], start="2026-08-26")
    plan = plan_for("SRTG.JK", SRTG_LOTS, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    capital_rp=CAPITAL)

    assert plan.stages_done == 0
    assert plan.stop_kind == "initial"
    assert plan.stop_rp == pytest.approx(plan.initial_stop_rp)


def test_the_trail_engages_once_a_stage_is_banked():
    """Six lots left of ten: stage 1 has been taken, so the runner now trails."""
    risk = 2.5 * SRTG_ATR
    high = SRTG_ENTRY + 3.0 * risk
    closes = _series([SRTG_ENTRY, high, high - 10], start="2026-08-26")
    plan = plan_for("SRTG.JK", 6, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    original_lots=SRTG_LOTS, capital_rp=CAPITAL)

    assert plan.stages_done == 1
    assert plan.stop_kind == "trailing"
    assert plan.stop_rp == pytest.approx(high - 2.5 * SRTG_ATR, abs=0.01)


def test_the_stop_only_ever_ratchets_up():
    """A wide ATR must not loosen a level the position has already earned."""
    risk = 2.5 * SRTG_ATR
    high = SRTG_ENTRY + 1.1 * risk
    closes = _series([SRTG_ENTRY, high, high - 5], start="2026-08-26")

    loose = plan_for("SRTG.JK", 6, SRTG_ENTRY, closes, CFG, FEES,
                     atr_rp=SRTG_ATR * 4, entry_date="2026-08-26", high=closes,
                     original_lots=SRTG_LOTS, capital_rp=CAPITAL)
    assert loose.stop_rp >= loose.initial_stop_rp
    assert loose.stop_kind in ("initial", "break-even")


def test_break_even_takes_over_before_the_trail_catches_up():
    """
    Straight after stage 1 the high is barely above the trim level, so the trail
    still sits below the entry. Break-even holds the line until it does not.
    """
    risk = 2.5 * SRTG_ATR
    high = SRTG_ENTRY + 1.05 * risk
    closes = _series([SRTG_ENTRY, high], start="2026-08-26")
    plan = plan_for("SRTG.JK", 6, SRTG_ENTRY, closes, CFG, FEES,
                    atr_rp=SRTG_ATR, entry_date="2026-08-26", high=closes,
                    original_lots=SRTG_LOTS, capital_rp=CAPITAL)

    assert plan.stop_kind == "break-even"
    assert plan.stop_rp > SRTG_ENTRY


def test_trailing_level_is_the_high_minus_k_atr():
    assert trailing_level(2000.0, 50.0, CFG) == pytest.approx(2000.0 - 125.0)
    assert trailing_level(None, 50.0, CFG) is None


# ---------------------------------------------------------------- entry risk
def test_entry_risk_is_reported_in_rupiah_and_percent():
    r = entry_risk(1495.0, 55.61, 900, CFG, FEES, CAPITAL)
    assert r["stop_rp"] == pytest.approx(1495.0 - 2.5 * 55.61, abs=0.01)
    assert r["risk_rp"] == pytest.approx(2.5 * 55.61 * 900, abs=1)
    assert r["over"] is False


def test_a_wild_name_is_flagged_over_the_risk_budget():
    """INET: 6.7% ATR. A Rp1.46 juta slot risks 2.2% even with the stop capped."""
    r = entry_risk(366.0, 24.51, 4000, CFG, FEES, CAPITAL)
    assert r["risk_pct"] > CFG.max_position_risk_pct
    assert r["over"] is True


def test_a_capped_stop_is_flagged_because_it_understates_the_risk():
    """
    The counter-intuitive one. 2.5 x INET's ATR is 16.7%, past the 15% cap, so the
    reported risk is the cap rather than the name's real downside -- which would
    make the wildest stock on the page look like one of the safest.
    """
    wild = entry_risk(366.0, 24.51, 3600, CFG, FEES, CAPITAL)
    calm = entry_risk(4680.0, 56.42, 300, CFG, FEES, CAPITAL)

    assert wild["capped"] is True
    assert calm["capped"] is False
    assert wild["stop_rp"] == pytest.approx(366.0 * 0.85, abs=0.01)


def test_a_capped_stop_puts_a_note_on_the_plan():
    closes = _series([366.0] * 5, start="2026-08-26")
    plan = plan_for("INET.JK", 36, 366.0, closes, CFG, FEES, atr_rp=24.51,
                    entry_date="2026-08-26", high=closes, capital_rp=CAPITAL)
    assert plan.stop_capped is True
    assert any("capped" in n for n in plan.notes)


def test_open_risk_totals_the_whole_book():
    closes = _series([2000, 2010], start="2026-08-26")
    positions = pd.DataFrame([
        {"ticker": "A.JK", "lots": 5, "avg_cost": 2000.0},
        {"ticker": "B.JK", "lots": 5, "avg_cost": 2000.0},
    ])
    plans = plans_for(positions, pd.DataFrame({"A.JK": closes, "B.JK": closes}),
                      CFG, FEES, atr={"A.JK": 50.0, "B.JK": 50.0},
                      capital_rp=CAPITAL)
    book = open_risk(plans, CAPITAL)
    assert book["n_positions"] == 2
    assert book["total_rp"] == pytest.approx(2 * 2.5 * 50.0 * 500, abs=1)
    assert book["pct_of_capital"] == pytest.approx(book["total_rp"] / CAPITAL * 100)


# --------------------------------------------------------- position history
def _journal(rows):
    return pd.DataFrame(rows, columns=["date", "ticker", "action", "lots",
                                       "shares", "price"])


def test_position_history_reads_the_live_journal_row():
    j = _journal([["2026-08-26", "SRTG.JK", "BUY", 10, 1000, 1935.0]])
    past = position_history(j)["SRTG.JK"]
    assert past["original_lots"] == 10
    assert past["lots_now"] == 10
    assert past["sold_lots"] == 0
    assert pd.Timestamp(past["opened"]) == pd.Timestamp("2026-08-26")


def test_position_history_remembers_the_size_a_trim_came_out_of():
    j = _journal([
        ["2026-08-26", "SRTG.JK", "BUY", 10, 1000, 1935.0],
        ["2026-09-02", "SRTG.JK", "SELL", 4, 400, 2080.0],
    ])
    past = position_history(j)["SRTG.JK"]
    assert past["original_lots"] == 10
    assert past["lots_now"] == 6
    assert past["sold_lots"] == 4


def test_going_flat_resets_the_position():
    """
    Buy 10, sell 10, buy 5 next month is a NEW position, not a 15-lot one that is
    two thirds sold. Without the reset a re-entry inherits the old plan and gets
    told to trim lots it never bought.
    """
    j = _journal([
        ["2026-07-01", "SRTG.JK", "BUY", 10, 1000, 1800.0],
        ["2026-07-20", "SRTG.JK", "SELL", 10, 1000, 1900.0],
        ["2026-08-26", "SRTG.JK", "BUY", 5, 500, 1935.0],
    ])
    past = position_history(j)["SRTG.JK"]
    assert past["original_lots"] == 5
    assert past["sold_lots"] == 0
    assert pd.Timestamp(past["opened"]) == pd.Timestamp("2026-08-26")


def test_a_closed_position_is_absent_from_the_history():
    j = _journal([
        ["2026-07-01", "SRTG.JK", "BUY", 10, 1000, 1800.0],
        ["2026-07-20", "SRTG.JK", "SELL", 10, 1000, 1900.0],
    ])
    assert position_history(j) == {}


def test_topping_up_raises_the_original_size():
    j = _journal([
        ["2026-08-26", "SRTG.JK", "BUY", 6, 600, 1935.0],
        ["2026-09-01", "SRTG.JK", "BUY", 4, 400, 1900.0],
    ])
    past = position_history(j)["SRTG.JK"]
    assert past["original_lots"] == 10
    assert pd.Timestamp(past["opened"]) == pd.Timestamp("2026-08-26")


def test_plans_for_uses_the_history_it_is_given():
    closes = _series([SRTG_ENTRY, SRTG_ENTRY + 3 * 2.5 * SRTG_ATR],
                     start="2026-08-26")
    positions = pd.DataFrame([{"ticker": "SRTG.JK", "lots": 6,
                               "avg_cost": SRTG_ENTRY}])
    plans = plans_for(
        positions, pd.DataFrame({"SRTG.JK": closes}), CFG, FEES,
        highs=pd.DataFrame({"SRTG.JK": closes}),
        history={"SRTG.JK": {"opened": pd.Timestamp("2026-08-26"),
                             "original_lots": 10, "lots_now": 6, "sold_lots": 4}},
        atr={"SRTG.JK": SRTG_ATR}, capital_rp=CAPITAL,
    )
    plan = plans["SRTG.JK"]
    assert plan.original_lots == 10
    assert plan.stages_done == 1
    assert plan.stop_kind == "trailing"


# ------------------------------------------------------------------ cooldown


def test_a_recent_sale_blocks_a_re_buy():
    j = _journal([["2026-09-03", "SRTG.JK", "SELL", 10, 1000, 1795.0]])
    blocked = cooldown(j, CFG, today="2026-09-05")
    assert blocked["SRTG.JK"] > 0


def test_the_cooldown_expires():
    j = _journal([["2026-08-01", "SRTG.JK", "SELL", 10, 1000, 1795.0]])
    assert cooldown(j, CFG, today="2026-09-05") == {}


def test_a_buy_does_not_start_a_cooldown():
    j = _journal([["2026-09-03", "SRTG.JK", "BUY", 10, 1000, 1795.0]])
    assert cooldown(j, CFG, today="2026-09-05") == {}


def test_cooldown_counts_sessions_not_days_when_a_calendar_is_given():
    """Ten sessions must not expire over two long weekends."""
    j = _journal([["2026-08-24", "SRTG.JK", "SELL", 10, 1000, 1795.0]])
    sparse = pd.DatetimeIndex(["2026-08-25", "2026-09-01", "2026-09-04"])
    assert cooldown(j, CFG, today="2026-09-05", sessions=sparse)["SRTG.JK"] == 7


def test_cooldown_off_when_configured_to_zero():
    j = _journal([["2026-09-04", "SRTG.JK", "SELL", 10, 1000, 1795.0]])
    off = ExitConfig(cooldown_sessions=0)
    assert cooldown(j, off, today="2026-09-05") == {}


# ------------------------------------------------------------------- wiring
def test_config_reads_the_risk_block():
    class S:
        risk = {"k_atr": 3.0, "ladder": [1.5], "ladder_fractions": [0.5],
                "cooldown_sessions": 4}

    cfg = ExitConfig.from_settings(S())
    assert cfg.k_atr == 3.0
    assert cfg.ladder == (1.5,)
    assert cfg.cooldown_sessions == 4
    assert cfg.atr_window == 14        # untouched keys keep their default


def test_missing_risk_block_falls_back_to_defaults():
    class S:
        pass

    assert ExitConfig.from_settings(S()) == ExitConfig()


def test_plans_for_skips_an_empty_book():
    assert plans_for(pd.DataFrame(), None, CFG, FEES) == {}
