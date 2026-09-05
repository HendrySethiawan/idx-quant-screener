# src/portfolio/exits.py
"""
When to get out, and in how many pieces.

Until this module existed the tool decided what to BUY and never decided what to
SELL. `build_orders` emitted a sale for exactly one reason -- the name fell out of
the target book on a re-rank -- so a position could halve without the terminal
saying a word about it.

Three numbers per position, and one plain verdict:

  * **a stop**, `entry - k x ATR`, which is where the trade is wrong;
  * **a ladder**, trims at multiples of that same risk, in whole lots;
  * **a trailing level** for what is left after the first trim.

Everything here is pure: no fetching, no files, no clock beyond what the caller
hands in. Same shape as sizing.py and fees.py, and for the same reason -- these
are the numbers a decision rests on, and they must be testable without a browser,
a network or a fixed date.

**Why ATR and not a percentage.** Measured across the universe, a 2.5 x ATR stop
runs from 3.0% (BBSI) to 16.8% (INET). One percentage cannot serve both: it is a
normal fortnight for one name and a coin flip for the other. Scaling by the name's
own ATR is also the whole of the "adapt to the market" requirement -- when
volatility rises the ATR rises and every stop widens by itself, with no regime
knob to mis-set.

**Why the runner trails and the rest does not.** Over 1,705 simulated entries on a
42-session horizon, a trailing stop on the whole position fired on 66-82% of them
(median hold 13-20 sessions) against 39-51% for a stop fixed at entry. At Rp10
juta each firing costs about Rp22,000 once the stamp, the sell fee and the buy fee
to get back in are counted -- close to Rp700,000 a year across four slots, 7% of
the account. Trimming first and trailing only the remainder is what makes a
trailing stop affordable here: after stage 1 the worst case is a scratch, so the
churn stops being a loss.

**What this does not claim.** From a random entry over two months, +1R arrived
38.6% of the time and the stop first 37.4% -- near a coin flip, which is what a
random walk implies. The ladder narrows the distribution; it does not create
return. Whether ranked entries tilt that race is a question for `--backtest`, and
the panel says so rather than implying an edge nobody has measured.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import pandas as pd

from portfolio.fees import FeeConfig, round_trip_cost

# What `action` can be. Kept as constants because three separate modules branch on
# them and a typo in a string comparison fails silently.
HOLD = "HOLD"
TRIM = "TRIM"
EXIT = "EXIT"
NO_STOP = "NO STOP"
# The entry price itself is not believable, so nothing computed from it is. Kept
# apart from NO_STOP: that one means the PRICE SERIES cannot support a stop, this
# one means the record of what you paid cannot.
CHECK_ENTRY = "CHECK ENTRY"

# Why the BOOK sold something the position's own plan was content to hold. These
# are not exit reasons -- no stop was hit and no target was reached -- and telling
# the reader the same thing for both was how a rank-4 holding and a rank-33 one
# came to carry the identical note "no longer in the target book".
DERISK = "de-risk"      # the regime cut the budget; the book has to be smaller
ROTATE = "rotate"       # a better-ranked name takes the slot


@dataclass(frozen=True)
class ExitConfig:
    """Every number the exit rules use. Defaults match configs/default.yaml."""
    atr_window: int = 14
    k_atr: float = 2.5
    ladder: Sequence[float] = (1.0, 2.0)
    ladder_fractions: Sequence[float] = (0.4, 0.3)
    trail_after_stage: int = 1
    trigger: str = "close"
    max_stop_pct: float = 15.0
    max_trim_cost_pct: float = 2.5
    max_position_risk_pct: float = 2.0
    cooldown_sessions: int = 10

    @classmethod
    def from_settings(cls, settings) -> "ExitConfig":
        cfg = getattr(settings, "risk", None) or {}
        return cls(
            atr_window=int(cfg.get("atr_window", 14)),
            k_atr=float(cfg.get("k_atr", 2.5)),
            ladder=tuple(float(x) for x in cfg.get("ladder", (1.0, 2.0))),
            ladder_fractions=tuple(
                float(x) for x in cfg.get("ladder_fractions", (0.4, 0.3))),
            trail_after_stage=int(cfg.get("trail_after_stage", 1)),
            trigger=str(cfg.get("trigger", "close")).lower(),
            max_stop_pct=float(cfg.get("max_stop_pct", 15.0)),
            max_trim_cost_pct=float(cfg.get("max_trim_cost_pct", 2.5)),
            max_position_risk_pct=float(cfg.get("max_position_risk_pct", 2.0)),
            cooldown_sessions=int(cfg.get("cooldown_sessions", 10)),
        )


@dataclass
class Stage:
    """One rung of the ladder: a price, a lot count, and what it costs to take."""
    n: int
    r_multiple: float
    level_rp: float
    lots: int
    shares: int
    proceeds_rp: float
    # The stamp is charged once per DAY containing a sell, not per order, so the
    # same trim costs Rp2,410 if it shares a day with another sale and Rp12,410 if
    # it is alone. Both are carried: quoting only one of them would either hide the
    # cost or overstate it, and the difference is the batching advice.
    cost_batched_rp: float
    cost_alone_rp: float
    done: bool = False

    @property
    def cost_alone_pct(self) -> float:
        return (self.cost_alone_rp / self.proceeds_rp * 100.0) if self.proceeds_rp else 0.0


@dataclass
class ExitPlan:
    ticker: str
    lots: int
    shares: int
    entry_rp: float
    # What the position started at, so the ladder's rungs do not move when it is
    # trimmed. Equal to `lots` for a position nothing has left yet.
    original_lots: int = 0
    price_rp: Optional[float] = None
    high_since_entry_rp: Optional[float] = None
    atr_rp: Optional[float] = None
    atr_pct: Optional[float] = None

    initial_stop_rp: Optional[float] = None
    stop_rp: Optional[float] = None
    stop_kind: str = "initial"          # initial | break-even | trailing

    risk_per_share_rp: Optional[float] = None
    risk_rp: Optional[float] = None
    risk_pct_of_capital: Optional[float] = None

    stages: List[Stage] = field(default_factory=list)
    stages_done: int = 0
    stop_capped: bool = False
    # Why the recorded entry price cannot be a real fill, or "" when it can.
    # Set from `ledger.implausible_entries`; when present nothing else here is
    # computed, because everything else is measured from that price.
    entry_note: str = ""

    action: str = HOLD
    action_lots: int = 0
    reason: str = ""
    to_stop_pct: Optional[float] = None
    to_next_pct: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    # What the BOOK decided, when it overrode this plan's own action -- a de-risk
    # or a rotation, neither of which is an exit. Empty when `action` is already
    # what you should do.
    #
    # This exists because the ticket and the exit panel used to answer the same
    # question independently and disagree in public: "SELL ADRO.JK - no longer in
    # the target book" beside "HOLD - stop 2,502, sell at 2,908", with nothing on
    # the page saying which to follow. Both now render `final_action`, so they
    # cannot drift apart again.
    book_action: str = ""       # "" | SELL | HOLD
    book_reason: str = ""
    book_cause: str = ""        # "" | DERISK | ROTATE

    @property
    def final_action(self) -> str:
        """The one verdict for today. Never read `action` for display."""
        return self.book_action or self.action

    @property
    def final_reason(self) -> str:
        return self.book_reason or self.reason

    @property
    def staged(self) -> bool:
        """
        True when the plan can be acted on in pieces.

        The test is whether anything is left running after every rung, not how
        many rungs there are: one trim plus a runner is a genuine staged exit, and
        a single stage covering the whole position is one all-or-nothing call
        wearing a ladder's clothes.
        """
        return bool(self.stages) and sum(s.lots for s in self.stages) < self.original_lots

    @property
    def runner_lots(self) -> int:
        """Lots left to the trailing stop once every rung has been taken."""
        return max(0, self.original_lots - sum(s.lots for s in self.stages))

    @property
    def next_stage(self) -> Optional[Stage]:
        return next((s for s in self.stages if not s.done), None)

    @property
    def unrealized_pct(self) -> Optional[float]:
        if not self.price_rp or not self.entry_rp:
            return None
        return (self.price_rp / self.entry_rp - 1) * 100.0


# --------------------------------------------------------------------- the stop
def stop_level(entry_rp: float, atr_rp: float, cfg: ExitConfig,
               fee_cfg: FeeConfig, value_rp: float) -> Optional[float]:
    """
    `entry - k x ATR`, clamped at both ends. None when the ATR cannot support one.

    **Never tighter than the round trip plus one ATR.** A stop inside your own
    transaction cost is a guaranteed loss: the position has to move that far just
    to be square, so a stop closer than that fires on the fee, not on being wrong.
    At Rp10 juta the round trip is roughly 0.9%, and one ATR is the smallest move
    that is not noise.

    **Never wider than `max_stop_pct`.** Past that a stop is not protection, and
    the honest answer -- which `plan_for` gives -- is that the name is too wild for
    one slot at this account size, not a level 30% away that will never be used.
    """
    if not entry_rp or entry_rp <= 0:
        return None
    if atr_rp is None or not pd.notna(atr_rp) or atr_rp <= 0:
        return None

    distance = float(cfg.k_atr) * float(atr_rp)

    floor_pct = round_trip_cost(value_rp, fee_cfg) / value_rp if value_rp > 0 else 0.0
    min_distance = entry_rp * floor_pct + float(atr_rp)
    distance = max(distance, min_distance)

    max_distance = entry_rp * float(cfg.max_stop_pct) / 100.0
    if max_distance > 0:
        distance = min(distance, max_distance)

    stop = entry_rp - distance
    return stop if stop > 0 else None


def stop_capped(entry_rp: float, atr_rp: Optional[float], cfg: ExitConfig) -> bool:
    """
    True when `max_stop_pct`, rather than the ATR, decided the distance.

    Worth its own function because the consequence is counter-intuitive and would
    otherwise be invisible: a capped stop makes the position's reported risk
    SMALLER, so the wildest names in the universe come out looking like the safest
    ones. INET moves 6.7% on an ordinary day, so a 15% stop is barely two days of
    normal movement -- the number is real, but it is not the whole risk, and every
    surface that shows the risk has to say so.
    """
    if not entry_rp or entry_rp <= 0:
        return False
    if atr_rp is None or not pd.notna(atr_rp) or atr_rp <= 0:
        return False
    return float(cfg.k_atr) * float(atr_rp) > entry_rp * float(cfg.max_stop_pct) / 100.0


def break_even_level(entry_rp: float, shares: int, fee_cfg: FeeConfig) -> float:
    """
    The price at which selling the remainder gets the money back, fees included.

    Not the entry price. Entry already carries the buy fee; getting out also pays
    0.29% and the stamp. For a 6-lot remainder of a position bought at Rp1,938.68
    that is Rp1,965, and stopping out at Rp1,939 would be a small loss dressed up
    as break-even.
    """
    if shares <= 0 or entry_rp <= 0:
        return entry_rp
    value = shares * entry_rp
    return entry_rp + round_trip_cost(value, fee_cfg) / shares


def trailing_level(high_rp: float, atr_rp: float, cfg: ExitConfig) -> Optional[float]:
    """`high since entry - k x ATR`. The caller is responsible for the ratchet."""
    if high_rp is None or atr_rp is None or not pd.notna(high_rp) or not pd.notna(atr_rp):
        return None
    level = float(high_rp) - float(cfg.k_atr) * float(atr_rp)
    return level if level > 0 else None


# ------------------------------------------------------------------- the ladder
def build_ladder(lots: int, entry_rp: float, risk_per_share_rp: float,
                 cfg: ExitConfig, fee_cfg: FeeConfig,
                 sold_lots: int = 0) -> List[Stage]:
    """
    Trims at multiples of R, in whole lots, each one able to pay for itself.

    **`lots` is the ORIGINAL position, not what is left.** The ladder has to be
    anchored to the size the position started at, or it moves under its own feet:
    trim 4 of 10, and a ladder rebuilt from the surviving 6 would put its first
    rung at 2 lots and immediately ask for a trim the price has already been
    through. `sold_lots` is how much of that original has already gone, and it is
    the only thing that marks a rung done.

    That also makes the whole plan **stateless and self-correcting**. Nothing is
    remembered between runs: the levels come from the entry price, the progress
    comes from the journal's own lot counts, and if you trim by hand the next run
    simply agrees with you.

    Three constraints, in order, each of which removed a stage that looked fine:

      1. **Whole lots.** IDX sells 100 shares at a time. A "sell a third" that
         works out at 1.67 lots is not an instruction.
      2. **The trim must clear its own cost.** The stamp sets the floor at roughly
         Rp452,000 a trim; below that a rung is dropped and its lots roll forward
         into the next one, so nothing is silently lost from the position.
      3. **Something has to be left to run**, when the configured fractions leave
         room for it. A ladder whose rounding happens to consume the whole
         position is a full exit in instalments paying a stamp per instalment.

    A position that cannot carry a rung *and* a runner comes back as one stage
    covering everything, which `ExitPlan.staged` reports as False so the panel can
    say "this is one decision, not a ladder" instead of printing a plan that
    cannot be followed.
    """
    lots = int(lots)
    if lots <= 0 or risk_per_share_rp is None or risk_per_share_rp <= 0:
        return []

    lot_size = fee_cfg.lot_size
    levels = list(cfg.ladder)
    fractions = list(cfg.ladder_fractions)
    # An empty ladder is a real configuration, not a degenerate one: it means
    # "stop only, no profit-taking", which is the variant the backtest compares
    # the ladder against. It must not fall through to the single-stage default,
    # which would sell the whole position at the first target.
    if not levels:
        return []
    wants_runner = sum(fractions[:len(levels)]) < 0.999

    stages: List[Stage] = []
    assigned = 0
    carried = 0            # lots a dropped stage handed to the next one

    for i, r in enumerate(levels):
        fraction = fractions[i] if i < len(fractions) else 0.0
        want = int(round(lots * float(fraction))) + carried
        want = min(want, lots - assigned)
        if want <= 0:
            carried = 0
            continue

        level = entry_rp + float(r) * risk_per_share_rp
        shares = want * lot_size
        proceeds = shares * level
        batched = proceeds * fee_cfg.sell_fee
        alone = batched + fee_cfg.stamp_duty_rp

        # Constraint 2. Rolled forward rather than dropped outright: the lots are
        # still yours, they just leave at the next rung instead of this one.
        if proceeds > 0 and (alone / proceeds * 100.0) > cfg.max_trim_cost_pct:
            carried = want
            continue

        carried = 0
        assigned += want
        stages.append(Stage(
            n=len(stages) + 1, r_multiple=float(r), level_rp=level,
            lots=want, shares=shares, proceeds_rp=proceeds,
            cost_batched_rp=batched, cost_alone_rp=alone,
        ))

    # Constraint 3, only when the configuration asked for a runner. Fractions the
    # user deliberately set to sum to 1.0 mean "stage all the way out", and that
    # is their call to make.
    if wants_runner and stages and assigned >= lots:
        assigned -= stages[-1].lots
        stages.pop()
        for n, s in enumerate(stages, start=1):
            s.n = n

    if not stages or assigned >= lots:
        # One decision. Priced at the FIRST ladder level so the panel still has a
        # number to quote -- "get out at +1R" is a plan; "get out sometime" is not.
        level = entry_rp + float(levels[0] if levels else 1.0) * risk_per_share_rp
        shares = lots * lot_size
        proceeds = shares * level
        return [Stage(
            n=1, r_multiple=float(levels[0] if levels else 1.0), level_rp=level,
            lots=lots, shares=shares, proceeds_rp=proceeds,
            cost_batched_rp=proceeds * fee_cfg.sell_fee,
            cost_alone_rp=proceeds * fee_cfg.sell_fee + fee_cfg.stamp_duty_rp,
        )]

    # A rung is done when its lots have actually left the account, never because
    # the price passed through it. Those are different facts: a level reached last
    # Tuesday and not acted on is still a level you can act on today, and marking
    # it done would silently retire an instruction you never followed.
    remaining_sold = max(0, int(sold_lots))
    for s in stages:
        if remaining_sold >= s.lots:
            s.done = True
            remaining_sold -= s.lots

    return stages


# ------------------------------------------------------------------- the verdict
def plan_for(
    ticker: str,
    lots: int,
    entry_rp: float,
    close: Optional[pd.Series],
    cfg: ExitConfig,
    fee_cfg: FeeConfig,
    *,
    atr_rp: Optional[float] = None,
    entry_date=None,
    high: Optional[pd.Series] = None,
    capital_rp: float = 0.0,
    price_note: str = "",
    original_lots: Optional[int] = None,
    entry_note: str = "",
) -> ExitPlan:
    """
    One position, one verdict: HOLD, TRIM n lots, EXIT, or NO STOP.

    `close` is the daily close series; `high` the daily high, used only for the
    high-water mark. The trigger is the CLOSE, deliberately -- this tool has no
    live feed and cannot observe an intraday print, so a rule that fires on an
    intraday low would be describing something it never sees. It also churns less:
    2.0 x ATR survived 13 sessions on closes against 10 on lows.

    `price_note` carries `technical.unmeasurable_factors`' reason straight through.
    A name pinned at the Rp50 floor or suspended has an ATR near zero, and a stop
    computed from that sits on top of the entry price and sells on the first tick.
    """
    plan = ExitPlan(ticker=ticker, lots=int(lots), shares=int(lots) * fee_cfg.lot_size,
                    entry_rp=float(entry_rp))

    clean = close.dropna() if close is not None else None
    if clean is not None and len(clean):
        plan.price_rp = float(clean.iloc[-1])

    if atr_rp is None and clean is not None:
        atr_rp = _atr_from_close(clean, cfg.atr_window)
    plan.atr_rp = None if atr_rp is None or not pd.notna(atr_rp) else float(atr_rp)
    if plan.atr_rp and plan.price_rp:
        plan.atr_pct = plan.atr_rp / plan.price_rp * 100.0

    plan.high_since_entry_rp = _high_since(high if high is not None else close,
                                           entry_date, plan.price_rp)

    # Before any level is computed. Every number below -- the stop, the ladder, the
    # rupiah at risk, the verdict -- is derived from the entry price, so an entry
    # that cannot be a real fill produces a whole page of confident nonsense: an
    # AMRT recorded at Rp50 against a Rp1,310 market gave a stop 2,976% away and a
    # SELL in the ticket that existed only because of the typo.
    if entry_note:
        plan.entry_note = entry_note
        plan.action = CHECK_ENTRY
        plan.reason = f"the entry price is not believable — {entry_note}"
        plan.notes.append(
            "No stop, target or exit is set for this position: every one of them "
            "is measured from the entry price, and this one cannot be right. Fix "
            "the row in your ledger and the plan comes back."
        )
        return plan

    value = plan.shares * plan.entry_rp
    plan.initial_stop_rp = stop_level(plan.entry_rp, plan.atr_rp, cfg, fee_cfg, value)

    if plan.initial_stop_rp is None:
        plan.action = NO_STOP
        plan.reason = (
            f"no usable stop — {price_note}" if price_note else
            "no usable stop — this price has not moved enough to measure one"
        )
        plan.notes.append(
            "A stop needs a distance, and this name has not supplied one. Decide "
            "this position by hand, or do not hold it."
        )
        return plan

    plan.risk_per_share_rp = plan.entry_rp - plan.initial_stop_rp
    plan.risk_rp = plan.risk_per_share_rp * plan.shares
    if capital_rp:
        plan.risk_pct_of_capital = plan.risk_rp / capital_rp * 100.0

    plan.stop_capped = stop_capped(plan.entry_rp, plan.atr_rp, cfg)
    if plan.stop_capped:
        plan.notes.append(
            f"The stop is capped at {cfg.max_stop_pct:.0f}%. This name moves "
            f"{plan.atr_pct:.1f}% on an ordinary day, so {cfg.k_atr:g} x ATR would "
            f"sit further away than that — the risk figure below is the cap, not "
            f"the whole downside."
        )

    plan.original_lots = int(original_lots) if original_lots else plan.lots
    plan.original_lots = max(plan.original_lots, plan.lots)
    plan.stages = build_ladder(plan.original_lots, plan.entry_rp,
                               plan.risk_per_share_rp, cfg, fee_cfg,
                               sold_lots=plan.original_lots - plan.lots)
    plan.stages_done = sum(1 for s in plan.stages if s.done)

    # The stop ratchets in one direction only, and each step needs the one before
    # it: break-even is earned by banking a trim, and the trail is earned by
    # break-even. `max` rather than assignment, so a falling ATR can never loosen
    # a level the position has already reached.
    plan.stop_rp = plan.initial_stop_rp
    plan.stop_kind = "initial"

    if plan.stages_done >= cfg.trail_after_stage and plan.staged:
        be = break_even_level(plan.entry_rp, plan.shares, fee_cfg)
        if be > plan.stop_rp:
            plan.stop_rp, plan.stop_kind = be, "break-even"

        trail = trailing_level(plan.high_since_entry_rp, plan.atr_rp, cfg)
        if trail is not None and trail > plan.stop_rp:
            plan.stop_rp, plan.stop_kind = trail, "trailing"

    if plan.price_rp:
        plan.to_stop_pct = (plan.price_rp / plan.stop_rp - 1) * 100.0
        nxt = plan.next_stage
        if nxt is not None:
            plan.to_next_pct = (nxt.level_rp / plan.price_rp - 1) * 100.0

    _decide(plan, cfg, fee_cfg)
    return plan


def _target_lots(plan: ExitPlan, price: float) -> int:
    """
    How many lots the ladder says to be holding at this price.

    Every rung the price is at or above has been earned, whether it was acted on
    or not, so the target is the original size minus all of them. A gap that
    clears both rungs in one session therefore takes both, rather than acting on
    the first and waiting a day.
    """
    cumulative = 0
    for stage in plan.stages:
        if price >= stage.level_rp:
            cumulative += stage.lots
        else:
            break
    return max(0, plan.original_lots - cumulative)


def _decide(plan: ExitPlan, cfg: ExitConfig, fee_cfg: FeeConfig) -> None:
    """Fill `action`, `action_lots` and `reason` from the levels already computed."""
    price = plan.price_rp
    if price is None:
        plan.action, plan.reason = HOLD, "no price — nothing to compare the stop against"
        return

    # The stop is checked first and wins. A position past its stop is a decision
    # that has already been made; a target above it is one that has not.
    if price <= plan.stop_rp:
        plan.action, plan.action_lots = EXIT, plan.lots
        kind = {"initial": "your stop", "break-even": "your break-even stop",
                "trailing": "your trailing stop"}[plan.stop_kind]
        plan.reason = (f"{price:,.0f} is at or below {kind} of "
                       f"{plan.stop_rp:,.0f} — close the position")
        return

    # The ladder states a TARGET holding at this price, and the action is the
    # difference. Saying "trim the next rung's lots" instead looks equivalent and
    # is not: sell 2 lots by hand and the plan would still ask for the full 4, and
    # a price that gapped through both rungs in one session would only ever act on
    # the first. A target is self-correcting -- trim by hand and the next run
    # simply agrees with you.
    target = _target_lots(plan, price)
    take = plan.lots - target
    if take > 0:
        reached = [s for s in plan.stages if price >= s.level_rp]
        top = reached[-1]
        if target <= 0:
            plan.action, plan.action_lots = EXIT, plan.lots
            plan.reason = (
                f"{price:,.0f} has reached {top.level_rp:,.0f} and "
                f"{plan.lots} lot is too small to trim — one decision"
                if not plan.staged else
                f"{price:,.0f} has reached {top.level_rp:,.0f}, the last rung — "
                f"close what is left")
            return
        after = ("and move the stop to break-even" if top.n == 1
                 else "and let the rest run")
        plan.action, plan.action_lots = TRIM, take
        plan.reason = (f"{price:,.0f} has reached the +{top.r_multiple:.0f}R level of "
                       f"{top.level_rp:,.0f} — take {take} lot off {after}")
        return

    nxt = plan.next_stage

    plan.action = HOLD
    bits = [f"stop {plan.stop_rp:,.0f} ({plan.to_stop_pct:+.1f}%)"]
    if nxt is not None:
        # "trim" only when there is something to trim. On a 1-lot position the
        # single stage is a full exit, and calling it a trim gave an instruction
        # that cannot be followed -- the same slip the Portfolio page made.
        what = "next trim" if plan.staged else "sell at"
        bits.append(f"{what} {nxt.level_rp:,.0f} ({plan.to_next_pct:+.1f}%)")
    plan.reason = ", ".join(bits)


# ------------------------------------------------------------------- the book
def plans_for(
    positions: pd.DataFrame,
    closes: Optional[pd.DataFrame],
    cfg: ExitConfig,
    fee_cfg: FeeConfig,
    *,
    highs: Optional[pd.DataFrame] = None,
    history: Optional[Dict[str, Dict[str, object]]] = None,
    atr: Optional[Dict[str, float]] = None,
    price_notes: Optional[Dict[str, str]] = None,
    entry_notes: Optional[Dict[str, str]] = None,
    capital_rp: float = 0.0,
) -> Dict[str, ExitPlan]:
    """
    An `ExitPlan` per open position.

    `positions` is `ledger.open_positions` output -- FIFO shares at fee-inclusive
    cost, which is the right entry price: it is what the position has to beat to be
    genuinely ahead, and it is the number the round-trip maths already uses.

    `history` is `position_history` output, supplying the original size and the
    entry date. Without it every position is treated as untouched since entry,
    which is right for a fresh book and wrong for one that has been trimmed.
    """
    out: Dict[str, ExitPlan] = {}
    if positions is None or getattr(positions, "empty", True):
        return out

    history = history or {}
    atr = atr or {}
    price_notes = price_notes or {}
    entry_notes = entry_notes or {}

    for _, row in positions.iterrows():
        ticker = str(row["ticker"])
        lots = int(row["lots"])
        if lots <= 0:
            continue
        past = history.get(ticker) or {}
        out[ticker] = plan_for(
            ticker, lots, float(row["avg_cost"]),
            _column(closes, ticker), cfg, fee_cfg,
            atr_rp=atr.get(ticker),
            entry_date=past.get("opened"),
            original_lots=past.get("original_lots"),
            high=_column(highs, ticker),
            capital_rp=capital_rp,
            price_note=price_notes.get(ticker, ""),
            entry_note=entry_notes.get(ticker, ""),
        )
    return out


def open_risk(plans: Dict[str, ExitPlan], capital_rp: float) -> Dict[str, float]:
    """
    What the whole book loses if every stop fills at its level.

    Per-position risk is the number that sizes a trade; this is the one that keeps
    you solvent. Four positions each risking a comfortable 1.5% is 6% of capital
    on one bad week, and nothing on the page said so before.
    """
    at_risk = [p.risk_rp for p in plans.values() if p.risk_rp is not None]
    total = float(sum(at_risk))
    return {
        "total_rp": total,
        "pct_of_capital": (total / capital_rp * 100.0) if capital_rp else 0.0,
        "n_positions": len(at_risk),
        "n_without_stop": sum(1 for p in plans.values() if p.risk_rp is None),
    }


def entry_risk(price_rp: float, atr_rp: Optional[float], shares: int,
               cfg: ExitConfig, fee_cfg: FeeConfig,
               capital_rp: float = 0.0) -> Dict[str, Optional[float]]:
    """
    The same arithmetic for a position not yet opened.

    This is what turns the ticket's lot count into a decision you can refuse: a
    proposal is not just "Rp1.3 juta of INET", it is "Rp220,000 at risk, 2.2% of
    everything you have". Reported, never enforced -- the sizer's job is deploying
    the budget in whole lots, and it is the code the backtest validates.
    """
    value = shares * price_rp
    stop = stop_level(price_rp, atr_rp, cfg, fee_cfg, value)
    if stop is None:
        return {"stop_rp": None, "risk_rp": None, "risk_pct": None,
                "over": False, "capped": False}
    risk = (price_rp - stop) * shares
    pct = (risk / capital_rp * 100.0) if capital_rp else None
    return {
        "stop_rp": stop,
        "risk_rp": risk,
        "risk_pct": pct,
        "over": bool(pct is not None and pct > cfg.max_position_risk_pct),
        # A capped stop UNDERSTATES the risk, so a name flagged here can look
        # comfortably inside the budget while being the wildest thing on the page.
        "capped": stop_capped(price_rp, atr_rp, cfg),
    }


# ------------------------------------------------------- where the plan is up to
def position_history(journal: Optional[pd.DataFrame],
                     lot_size: int = 100) -> Dict[str, Dict[str, object]]:
    """
    Per open ticker: when the current position opened, how big it got, what is left.

    The ladder needs the ORIGINAL size to keep its rungs still, and the trailing
    stop needs the entry date to know where the high-water mark starts. Both come
    from the same replay, so they cannot disagree about when the position began.

    **The counter resets whenever a name goes flat.** Buy 10, sell all 10, buy 5
    again next month: that is a new position with a new entry date, not a 15-lot
    one that is two thirds sold. Without the reset a re-entry would inherit the
    old plan and be told to trim lots it never bought.

    Reads the journal and nothing else -- no new file, no new column, nothing that
    can drift away from the trades it describes.
    """
    out: Dict[str, Dict[str, object]] = {}
    if journal is None or getattr(journal, "empty", True):
        return out

    df = journal.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date", kind="stable")

    for ticker, grp in df.groupby("ticker"):
        held = 0
        peak = 0
        opened = None
        for _, row in grp.iterrows():
            shares = int(row["shares"])
            if str(row["action"]).upper() == "BUY":
                if held <= 0:
                    opened, peak = row["date"], 0
                held += shares
                peak = max(peak, held)
            else:
                held = max(0, held - shares)
                if held == 0:
                    opened, peak = None, 0
        if held > 0:
            out[str(ticker)] = {
                "opened": opened,
                "original_lots": peak // lot_size,
                "lots_now": held // lot_size,
                "sold_lots": (peak - held) // lot_size,
            }
    return out


# ------------------------------------------------------------------- cooldown
def cooldown(journal: Optional[pd.DataFrame], cfg: ExitConfig,
             today=None, sessions: Optional[pd.DatetimeIndex] = None) -> Dict[str, int]:
    """
    Names sold too recently to buy back, and how many sessions are left.

    Without this the loop closes on itself: the stop sells today, tomorrow's
    re-rank puts the same name back in the target book, and you pay 0.29% out,
    Rp10,000 stamp and 0.19% back in to end up exactly where you started. At one
    to two trades a week that is not a theoretical risk, it is the default outcome.

    Read from the journal's sell dates and nothing else -- no new file, no new
    column, no state that can drift from the trades it describes. It cannot tell a
    stop-out from a rebalance, and does not try to: at this fee schedule, buying
    back anything sold within two weeks is a bad trade whatever the reason was.

    `sessions` is the trading calendar when one is available, so "10 sessions"
    means ten sessions rather than ten days across a long weekend.
    """
    if journal is None or getattr(journal, "empty", True):
        return {}
    if cfg.cooldown_sessions <= 0:
        return {}

    df = journal.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    sells = df[(df["action"].astype(str).str.upper() == "SELL") & df["date"].notna()]
    if sells.empty:
        return {}

    now = pd.Timestamp(today) if today is not None else pd.Timestamp.today()
    now = now.normalize()

    out: Dict[str, int] = {}
    for ticker, grp in sells.groupby("ticker"):
        last = pd.Timestamp(grp["date"].max()).normalize()
        elapsed = _sessions_between(last, now, sessions)
        left = int(cfg.cooldown_sessions) - elapsed
        if left > 0:
            out[str(ticker)] = left
    return out


def _sessions_between(start: pd.Timestamp, end: pd.Timestamp,
                      sessions: Optional[pd.DatetimeIndex]) -> int:
    if end <= start:
        return 0
    if sessions is not None and len(sessions):
        idx = pd.DatetimeIndex(sessions).normalize()
        return int(((idx > start) & (idx <= end)).sum())
    # No calendar to hand: business days, which over-counts holidays slightly and
    # therefore ends the cooldown a little early rather than a little late.
    return int(len(pd.bdate_range(start, end)) - 1)


# -------------------------------------------------------------------- helpers
def _column(frame: Optional[pd.DataFrame], ticker: str) -> Optional[pd.Series]:
    if frame is None or getattr(frame, "empty", True) or ticker not in frame.columns:
        return None
    return frame[ticker]


def _high_since(series: Optional[pd.Series], entry_date,
                fallback: Optional[float]) -> Optional[float]:
    """
    The high-water mark since entry, or the current price when there is no history.

    Falling back to the price rather than to None keeps the trailing stop defined
    for a position opened today: high == price, so the trail sits exactly k x ATR
    below, which is the initial stop. The rule degrades into itself instead of
    disappearing.
    """
    if series is None:
        return fallback
    clean = series.dropna()
    if clean.empty:
        return fallback
    if entry_date is not None:
        try:
            clean = clean[clean.index >= pd.Timestamp(entry_date).normalize()]
        except (TypeError, ValueError):
            pass
    if clean.empty:
        return fallback
    return float(clean.max())


def _atr_from_close(close: pd.Series, window: int) -> Optional[float]:
    """
    Last-resort ATR when only closes are available.

    True range collapses to |close - previous close| without High and Low, which
    understates the real range. A stop built on it is therefore TIGHTER than one
    built on the full bar -- the safe direction to be wrong in, and the caller
    normally passes `atr_rp` from the OHLC frame anyway.
    """
    clean = close.dropna()
    if len(clean) < 3:
        return None
    tr = clean.diff().abs()
    value = tr.ewm(alpha=1 / max(1, int(window)), adjust=False).mean().iloc[-1]
    return float(value) if pd.notna(value) and value > 0 else None
