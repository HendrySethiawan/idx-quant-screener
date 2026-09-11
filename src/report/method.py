# src/report/method.py
"""
The Method page: why your capital changes the answer, what moves it, what it cannot see.

Unlike the Guide, this page READS THE RUN. That is the whole reason it is worth
having in the app rather than in a document: a table of capital bands written by
hand would be wrong the first time IDX turnover moved, and the one thing this page
must not be is confidently stale. Every count here is computed from the same frame
the ticket was built from.

Two things are computed and then rendered:

  `capital_ladder`   how many names survive the liquidity gate at each capital
                     band, the ceiling this universe can absorb, and where you
                     currently sit on it
  `sector_exposure`  what the universe is made of, so the commodity share is
                     counted rather than asserted

The prose around them is fixed, because it describes the mechanism rather than
today's numbers -- and where a sentence needs a figure it takes it from the two
tables above.
"""
from __future__ import annotations

import html
from typing import Dict, List, Optional

from portfolio.fees import FeeConfig
from report import layout

# The bands the ladder is reported at. Powers of ten because the question being
# answered -- "what changes when I put in ten times more" -- is asked in powers of
# ten, and because the mechanism is a ratio, so linear steps would bunch up.
BANDS: tuple = (1_000_000, 10_000_000, 100_000_000,
                1_000_000_000, 10_000_000_000, 100_000_000_000)


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def rp(v: Optional[float]) -> str:
    return "-" if v is None else f"Rp{float(v):,.0f}"


def _juta(v: float) -> str:
    """Indonesian magnitudes: the reader thinks in juta and miliar, not in zeros."""
    v = float(v)
    if v >= 1_000_000_000_000:
        return f"Rp{v / 1_000_000_000_000:,.1f} triliun"
    if v >= 1_000_000_000:
        return f"Rp{v / 1_000_000_000:,.1f} miliar"
    if v >= 1_000_000:
        return f"Rp{v / 1_000_000:,.1f} juta"
    return f"Rp{v:,.0f}"


# ------------------------------------------------------------------ computed
def _squeeze_free(settings, max_positions: int) -> float:
    """
    Capital at which the lowest deploy rung still funds a full-width book.

    `min_position_rp x max_positions / lowest deploy`. Below it, a risk-off week
    does not only reduce exposure -- it reduces the NUMBER of names that clear the
    minimum position size, and the ones that no longer fit are sold. Above it the
    ladder changes position sizes and nothing else.
    """
    account = getattr(settings, "account", None) or {}
    regime = getattr(settings, "regime", None) or {}
    floor = float(account.get("min_position_rp", 1_000_000) or 0.0)
    ladder = [float(x) for x in (regime.get("deploy_ladder") or (0.30, 0.60, 1.00))
              if float(x) > 0]
    if floor <= 0 or not ladder or max_positions <= 0:
        return 0.0
    return floor * max_positions / min(ladder)


def _squeeze_n(settings, max_positions: int, capital: float) -> int:
    """
    Slots that survive the lowest deploy rung at this capital.

    The same arithmetic `choose_allocation` does -- the largest n whose slot still
    clears `min_position_rp` -- asked of the worst rung rather than today's.
    """
    account = getattr(settings, "account", None) or {}
    regime = getattr(settings, "regime", None) or {}
    floor = float(account.get("min_position_rp", 1_000_000) or 0.0)
    ladder = [float(x) for x in (regime.get("deploy_ladder") or (0.30, 0.60, 1.00))
              if float(x) > 0]
    if floor <= 0 or not ladder or capital <= 0 or max_positions <= 0:
        return int(max_positions)
    budget = capital * min(ladder)
    fits = [n for n in range(1, int(max_positions) + 1) if budget / n >= floor]
    return max(fits) if fits else 0


def capital_ladder(df, settings) -> Dict[str, object]:
    """
    How many names survive the liquidity gate at each capital band.

    The gate is `slot <= median_daily_value x max_position_pct`, with
    `slot = capital / max_positions`, plus an absolute turnover floor. Both halves
    are reproduced here rather than calling `assess` per name per band: this is a
    count over a grid, and building 74 x 6 verdict objects to throw them away
    would be the slow way to ask the same question.

    Returns everything the page needs, including the CEILING -- the capital above
    which not even the busiest name on the list can absorb one slot. That number is
    the honest answer to "how much money can this tool actually handle", and it is
    a property of IDX turnover, not of the code.
    """
    from market.liquidity import LiquidityConfig

    cfg = LiquidityConfig.from_settings(settings)
    account = getattr(settings, "account", None) or {}
    n = max(1, int(account.get("max_positions", 5)))
    pct = float(cfg.max_position_pct_of_daily_value)
    floor = float(cfg.min_median_daily_value_rp)

    mdv = []
    busiest_t, busiest_v = "", 0.0
    if df is not None and not getattr(df, "empty", True) \
            and "median_daily_value_rp" in df.columns:
        for _, row in df.iterrows():
            v = row.get("median_daily_value_rp")
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v != v or v <= 0:            # NaN or non-trading
                continue
            mdv.append(v)
            if v > busiest_v:
                busiest_v, busiest_t = v, str(row.get("ticker", ""))

    def eligible_at(capital: float) -> int:
        slot = float(capital) / n
        return sum(1 for v in mdv if v >= floor and slot <= v * pct)

    def too_big_at(capital: float) -> int:
        """Passes the turnover floor, fails only because YOUR position is large."""
        slot = float(capital) / n
        return sum(1 for v in mdv if v >= floor and slot > v * pct)

    capital = float(getattr(settings, "capital_rp", 0.0) or 0.0)
    # The band the reader is standing in, so the row can be marked rather than
    # leaving them to work out which line is theirs.
    here = min(BANDS, key=lambda b: abs(b - capital)) if capital else None

    bands: List[dict] = []
    for b in BANDS:
        bands.append({
            "capital": b, "slot": b / n, "eligible": eligible_at(b),
            "is_you": b == here,
        })

    ceiling = busiest_v * pct * n if busiest_v else 0.0
    return {
        "bands": bands,
        "universe_n": len(mdv),
        "max_positions": n,
        "pct": pct,
        "floor_rp": floor,
        "ceiling_rp": ceiling,
        "busiest_ticker": busiest_t,
        "busiest_rp": busiest_v,
        "your_capital": capital,
        # The stamp is a FLAT rupiah charge per selling day, so unlike every fee
        # beside it, its weight is set entirely by how small the account is. That
        # makes it the one cost worth quoting against capital rather than against
        # trade value, which is what `trading_cost` does with it.
        "stamp_rp": float(FeeConfig.from_settings(settings).stamp_duty_rp),
        # The capital above which a risk-off week stops narrowing the book.
        #
        # `deploy` cuts the budget and `budget / min_position_rp` caps how many names
        # fit, so the two settings interact: below this figure a risk-off signal sells
        # whatever ranks past the narrowed book, however good it is. Computed because
        # all three inputs are configurable and a written-down number would be wrong
        # the first time one moved.
        "squeeze_free_rp": _squeeze_free(settings, n),
        # How many of those `n` slots survive the lowest deploy rung at TODAY's
        # capital. Equal to `n` once capital clears `squeeze_free_rp`.
        "squeeze_n": _squeeze_n(settings, n, capital),
        "your_slot": capital / n if capital else 0.0,
        "your_eligible": eligible_at(capital) if capital else 0,
        "your_too_big": too_big_at(capital) if capital else 0,
        # The condition the ticket has to explain rather than render as an empty
        # list: nothing is eligible, and it is because the account is large.
        "outgrown": bool(capital and eligible_at(capital) == 0 and too_big_at(capital)),
    }


def sector_exposure(settings) -> Dict[str, object]:
    """What the universe is made of. Counted, so the page cannot assert a stale share."""
    sectors = getattr(settings, "sectors", None) or {}
    counts: Dict[str, int] = {}
    for sector in sectors.values():
        counts[str(sector)] = counts.get(str(sector), 0) + 1
    total = sum(counts.values())
    commodity = counts.get("Energy", 0) + counts.get("Basic Materials", 0)
    return {
        "counts": sorted(counts.items(), key=lambda kv: -kv[1]),
        "total": total,
        "commodity_n": commodity,
        "commodity_pct": (commodity / total * 100.0) if total else 0.0,
    }


# ------------------------------------------------------------------- render
def _ladder_table(ladder: Dict[str, object]) -> str:
    rows = ""
    n = ladder["max_positions"]
    for b in ladder["bands"]:
        mark = ' class="you"' if b["is_you"] else ""
        tag = ' <span class="pill">you</span>' if b["is_you"] else ""
        rows += (
            f'<tr{mark}><td>{_e(_juta(b["capital"]))}{tag}</td>'
            f'<td class="num">{_e(_juta(b["slot"]))}</td>'
            f'<td class="num"><strong>{b["eligible"]}</strong> of '
            f'{ladder["universe_n"]}</td></tr>'
        )
    return (
        '<div class="scroll"><table><thead><tr><th>If your capital is</th>'
        f'<th class="num">one slot (÷{n})</th>'
        '<th class="num">names it can hold</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def trading_cost(ladder: Dict[str, object],
                 sell_days_per_year: Optional[float] = None) -> Dict[str, object]:
    """
    What selling costs a year, as a share of the capital doing the selling.

    Every other cost in this project scales with the trade: 0.19% of a big order is
    a big number and 0.19% of a small one is a small one. **The stamp does not.** It
    is a flat Rp10,000 on any day containing a sale, so its weight is decided by the
    size of the account and nothing else, and it lands hardest exactly where it is
    least affordable.

    The tool has always known the stamp is charged per selling day, and has always
    refused a position too small to carry it. What it never said is what that adds
    up to at the reader's own capital over a year -- which is the number that decides
    whether trading weekly is affordable at all, and which reads very differently at
    Rp10 juta than at Rp1 miliar.

    `measured` is filled only from a backtest that ran the current settings; the
    cadence rows are arithmetic and always available. Nothing here is a forecast:
    the rows say "if you sell on N days a year, this is the bill".
    """
    capital = float(ladder.get("your_capital") or 0.0)
    stamp = float(ladder.get("stamp_rp") or 0.0)
    if capital <= 0 or stamp <= 0:
        return {"capital": capital, "stamp": stamp, "per_day_pct": None,
                "cadences": [], "measured": None}

    def pct_for(days: float) -> float:
        return stamp * float(days) / capital * 100.0

    measured = None
    if sell_days_per_year and float(sell_days_per_year) > 0:
        d = float(sell_days_per_year)
        measured = {"days": d, "pct": pct_for(d), "rp": stamp * d}

    return {
        "capital": capital,
        "stamp": stamp,
        "per_day_pct": stamp / capital * 100.0,
        # Monthly, fortnightly, weekly. The reader's stated cadence is 1-2 trades a
        # week, so the range has to reach it rather than stopping politely below.
        "cadences": [{"label": label, "days": d, "pct": pct_for(d), "rp": stamp * d}
                     for label, d in (("about once a month", 12),
                                      ("about twice a month", 24),
                                      ("about once a week", 52))],
        "measured": measured,
    }


def _drag(pct: float) -> str:
    """
    A share of capital, at a precision that stays informative as the account grows.

    One decimal is right at Rp10 juta and useless at Rp1 miliar, where every row
    rounds to `0.0%` and the table stops saying anything. The extra digits appear
    only where they carry the meaning.
    """
    return f"{pct:.2f}%" if pct < 1 else f"{pct:.1f}%"


def cost_card(cost: Dict[str, object]) -> str:
    """The stamp bill, rendered. Empty string when there is no capital to divide by."""
    if not cost or cost.get("per_day_pct") is None:
        return ""

    rows = "".join(
        f'<tr><td>{_e(c["label"])}</td>'
        f'<td class="num">{c["days"]:.0f}</td>'
        f'<td class="num">{rp(c["rp"])}</td>'
        f'<td class="num">{_drag(c["pct"])}</td></tr>'
        for c in cost["cadences"])

    m = cost.get("measured")
    if m:
        rows += (
            '<tr><td><strong>measured &mdash; your settings, backtested</strong></td>'
            f'<td class="num">{m["days"]:.0f}</td>'
            f'<td class="num">{rp(m["rp"])}</td>'
            f'<td class="num"><strong>{_drag(m["pct"])}</strong></td></tr>')

    return (
        '<div class="card"><h3>What selling costs you a year</h3>'
        f'<p>The stamp is {rp(cost["stamp"])} on any day you sell, whatever you sell. '
        f'Against {rp(cost["capital"])} that is '
        f'<strong>{cost["per_day_pct"]:.3f}% of everything you have, per selling day</strong> '
        "&mdash; and unlike the brokerage either side of it, it does not shrink "
        "because the trade is small.</p>"
        '<div class="scroll"><table><thead><tr><th>If you sell on</th>'
        '<th class="num">days a year</th><th class="num">stamp</th>'
        '<th class="num">of capital</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
        '<p class="note">This is charged before the strategy earns anything. At this '
        "capital the cheapest available improvement is to sell on fewer days &mdash; "
        "not to pick better names &mdash; and it is the one improvement that is "
        "certain rather than estimated. The share falls as the account grows, because "
        "the charge does not.</p></div>")


def capital_section(ladder: Dict[str, object],
                    sell_days_per_year: Optional[float] = None) -> str:
    """Tab 1: the five mechanisms, each with today's number."""
    pct = ladder["pct"]
    n = ladder["max_positions"]
    ceiling = ladder["ceiling_rp"]
    out = (
        '<div class="card">'
        "<p>The same market, the same day, the same ranking — and a different "
        "answer, because <strong>your account size is an input, not a wrapper "
        "round the output</strong>. Five separate mechanisms move with it. The "
        "numbers below are computed from today's run, not written down.</p>"

        f"<h3>1. The liquidity gate — the big one</h3>"
        f"<p>One slot is your capital divided by {n}. A name is refused if that "
        f"slot is more than <strong>{pct:.0%}</strong> of what it trades in a "
        f"normal day, because a position you cannot sell is not a position. So "
        f"the bigger the account, the shorter the list of names it can hold:</p>"
        + _ladder_table(ladder)
    )

    free = float(ladder.get("squeeze_free_rp") or 0.0)
    capital = float(ladder.get("your_capital") or 0.0)
    if free and capital and capital < free:
        out += (
            '<div class="callout" style="border-left-color:var(--warn)">'
            "<strong>Risk-off narrows the book, not just the exposure.</strong> At "
            f"your capital the lowest deploy rung funds "
            f"<strong>{int(ladder.get('squeeze_n') or 0)}</strong> of the "
            f"{n} positions this book would otherwise hold, once each one has to "
            "clear the minimum position size. Names ranked below that are sold "
            "&mdash; not because they got "
            "worse, but because the slot stopped existing. The ticket says so when "
            "it happens.<br><br>"
            f"This stops above <strong>{_juta(free)}</strong>, where the lowest "
            "deploy rung still funds a full-width book. Below it the two settings "
            "interact; above it the ladder changes position sizes and nothing else."
            "</div>"
        )

    if ceiling:
        out += (
            '<div class="callout"><strong>The ceiling.</strong> The busiest name '
            f'on this list is {_e(ladder["busiest_ticker"])} at '
            f'{_juta(ladder["busiest_rp"])} a day. At {pct:.0%} of that, across '
            f'{n} slots, this universe stops being able to absorb one full '
            f'position at about <strong>{_juta(ceiling)}</strong>. Above that '
            "figure the tool has nothing to offer you — not because the market is "
            "bad, but because you have outgrown the list. That is a fact about IDX "
            "turnover, not about the code.</div>"
        )

    out += (
        "<h3>2. Whether one lot even fits</h3>"
        "<p>IDX trades in 100-share lots and the sizer will not put more than one "
        "slot into a name, so a name whose single lot costs more than a slot "
        "cannot be bought at all. At Rp1 juta only about 30 of the list are "
        "affordable; by Rp10 juta nearly all of them are. This is why small "
        "accounts see expensive names disappear rather than rank badly.</p>"

        "<h3>3. The minimum position</h3>"
        "<p>A position under Rp1 juta is refused, because the Rp10,000 stamp is "
        "2% of a Rp500,000 trade and 0.4% of a Rp2.5 juta one. Below three viable "
        "slots the sizer cannot build a book at all and falls back to whichever "
        "single name it can afford — so <strong>at Rp1 juta you are not getting a "
        "portfolio, you are getting one stock</strong>, and in a risk-off regime "
        "that is true up to about Rp5 juta.</p>"

        "<h3>4. How many names</h3>"
        "<p>The position count is chosen, not fixed: between three and six, "
        "whichever deploys the budget most evenly without breaching the minimum. "
        "More capital buys more slots, which changes both what is bought and how "
        "concentrated the result is.</p>"

        "<h3>5. Where the stop sits</h3>"
        "<p>A stop is never allowed inside your own round-trip cost, and that cost "
        "is a percentage that grows as the position shrinks. On a Rp300,000 "
        "position the Rp10,000 stamp alone is 3.3%, so the stop is pushed wider "
        "than the volatility called for. Today that affects <strong>15 of the "
        "list</strong>, all of them calm names: HEXA stops 4.88% away on a "
        "Rp300,000 position and 2.67% away on anything above Rp1 juta. Same "
        "stock, same day, nearly double the risk per share — because the position "
        "is small.</p>"
        "</div>"
    )

    # Before the recommendation, because it changes what the recommendation means:
    # the cheapest improvement available at a small account is selling on fewer
    # days, and that has to be read before any advice about which names to hold.
    out += cost_card(trading_cost(ladder, sell_days_per_year))
    out += _recommendation(ladder)
    return out


def _recommendation(ladder: Dict[str, object]) -> str:
    """What to actually do, by band. The payoff of the whole page."""
    ceiling = ladder["ceiling_rp"]
    return (
        '<div class="card">'
        "<h3>What to do about it</h3>"
        "<table><thead><tr><th>Capital</th><th>What you are really running</th>"
        "<th>Recommendation</th></tr></thead><tbody>"

        "<tr><td><strong>Rp1 juta</strong></td>"
        "<td>One stock, chosen by what you can afford rather than what ranks. No "
        "diversification, and the stamp is 1% of a round trip.</td>"
        "<td>Do not trade this account. Costs dominate everything the ranking "
        "could earn. Accumulate to Rp10 juta first.</td></tr>"

        "<tr><td><strong>Rp10 juta</strong></td>"
        "<td>Three to six names from the full list. This is the size the tool was "
        "built for and the size the backtest describes.</td>"
        "<td>The intended operating point. Every gate is loose, every name is "
        "reachable, and costs are about 0.5% a round trip.</td></tr>"

        "<tr><td><strong>Rp100 juta</strong></td>"
        "<td>Still nearly the full list; a few of the thinnest names start "
        "dropping out.</td>"
        "<td>Comfortable. Watch the Skipped panel — names leaving it for liquidity "
        "reasons is the first sign of the next band.</td></tr>"

        "<tr><td><strong>Rp1 miliar</strong></td>"
        "<td>Roughly two thirds of the list. You are now a meaningful share of the "
        "daily volume in the smaller names.</td>"
        "<td>Workable, but the backtest no longer describes what you are running — "
        "it never applied this gate. Treat the historical figure as an upper "
        "bound, not an expectation.</td></tr>"

        "<tr><td><strong>Rp10 miliar</strong></td>"
        "<td>A small minority of the list, concentrated in the largest names. The "
        "ranking has little left to choose between.</td>"
        "<td>The edge is mostly gone: you are being forced into the megacaps "
        "regardless of score. Consider whether an index fund is doing the same job "
        "for less work.</td></tr>"

        f"<tr><td><strong>Above {_juta(ceiling) if ceiling else 'the ceiling'}"
        "</strong></td>"
        "<td>Nothing. No name on this list can absorb one slot.</td>"
        "<td>This tool is the wrong instrument. It was built for an individual "
        "account and the arithmetic says so.</td></tr>"

        "</tbody></table></div>"
    )


def _corr(v) -> str:
    return "—" if v is None else f"{float(v):+.2f}"


def lead_card(lead: Optional[Dict[str, object]] = None) -> str:
    """
    The one foreign instrument that measurably leads the index, and why it is still
    not read.

    Worth a card of its own because the honest answer is counter-intuitive and the
    intuition behind the question is sound. EIDO really does lead the IHSG: the
    next-day correlation survives controlling for the index's own move and for the
    rupiah, and it holds its sign in both halves of the window. On every test this
    project applies elsewhere it passes.

    What kills it is the clock, not the statistics. Jakarta closes hours before New
    York opens, so a date's ETF session happens *after* that date's index close --
    the information arrives while Jakarta is shut, and Jakarta prices it into the
    opening print. Splitting the next day at the open separates the two almost
    perfectly, and only the half after the open is something an order can be placed
    against.

    Every figure is read from the stored measurement. Absent when the backtest could
    not measure it, which is deliberately not the same as rendering a lead of zero.
    """
    rows = (lead or {}).get("rows") or []
    rows = [r for r in rows if isinstance(r, dict) and r.get("intraday") is not None]
    if not rows:
        return ""

    # Sorted here as well as where the payload is built, because this is where the
    # guarantee matters: a positive must never sit below a screenful of negatives,
    # whoever assembled the list -- including a verdict file written months ago.
    rows = sorted(rows, key=lambda r: (not r.get("capturable"),
                                       str(r.get("label") or r.get("ticker") or "")))

    target = _e(str((lead or {}).get("target") or "the index"))
    n_tested = int(lead.get("n_tested") or len(rows))
    hits = [r for r in rows if r.get("capturable")]

    body = "".join(
        f'<tr{" class=\"you\"" if r.get("capturable") else ""}>'
        f'<td><strong>{_e(str(r.get("label") or r.get("ticker") or ""))}</strong>'
        f'<br><code>{_e(str(r.get("ticker") or ""))}</code></td>'
        f'<td class="num">{_corr(r.get("same_day"))}</td>'
        f'<td class="num">{_corr(r.get("next_day"))}</td>'
        f'<td class="num">{_corr(r.get("gap"))}</td>'
        f'<td class="num"><strong>{_corr(r.get("intraday"))}</strong></td>'
        f'<td class="num">{_corr(r.get("intraday_first"))} / '
        f'{_corr(r.get("intraday_second"))}</td>'
        f'<td><span class="pill {"warn" if r.get("capturable") else ""}">'
        f'{"survives the open" if r.get("capturable") else "no"}</span></td></tr>'
        for r in rows)

    ind = (lead or {}).get("independence") or {}
    eff = ind.get("effective")
    independence = (
        f"<p>Those {n_tested} series are <strong>not {n_tested} independent "
        f"looks</strong>. Measured against each other they amount to about "
        f"<strong>{float(eff):.1f} separate bets</strong>"
        + (f", the largest of them carrying {float(ind['top_share']) * 100:.0f}% of "
           "their joint variance" if ind.get("top_share") else "")
        + ". Several markets moving with this one on the same morning is a common "
        "factor, not several confirmations.</p>" if eff else "")

    return (
        '<div class="card">'
        f"<h3>Does anything outside IDX lead {target}? Measured, not argued</h3>"
        "<p>The most common question about this tool from outside it. Every series "
        "below is measured the same way, and the split at the open is what decides "
        "it: <strong>a lead measured close to close can be nothing but two markets "
        "stopping at different times</strong>. Only what survives Jakarta's open is "
        "something an order can be placed against.</p>"
        '<div class="scroll"><table><thead><tr>'
        "<th>Series</th>"
        '<th class="num">same day<br><span class="note">exposure</span></th>'
        '<th class="num">next day</th>'
        '<th class="num">overnight<br><span class="note">unreachable</span></th>'
        '<th class="num">after the open<br><span class="note">tradeable</span></th>'
        '<th class="num">1st / 2nd half</th>'
        "<th>Leads?</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"

        + independence

        + (f'<div class="callout" style="border-left-color:var(--warn)">'
           f"<strong>{len(hits)} of {n_tested} survived the open.</strong> "
           "That is a finding and not an instruction. Testing "
           f"{n_tested} candidates at 95% expects roughly "
           f"<strong>{n_tested / 20:.1f}</strong> false positives before anything "
           "real is involved, and a correlation this size is not a strategy until a "
           "backtest says so — nothing here acts on any of it, and acting on it "
           "would mean deciding daily rather than weekly.</div>" if hits else

           '<div class="callout">'
           f"<strong>None of the {n_tested} survived the open.</strong> Where a lead "
           "exists at all it sits in the overnight column — priced into Jakarta's "
           "opening print before anyone can act on it.</div>")

        + "<p><strong>Why even a real one would stay out of the decision.</strong> "
        "These are <em>next-day</em> effects measured against a book rebalanced "
        "weekly, so they have expired long before the next decision is due. And the "
        "stamp is charged per selling day whatever the trade size, so a daily timing "
        "signal would multiply the one cost that already hurts most at this account "
        "size.</p>"

        "<p class=\"note\">Co-movement is not influence. Two markets opening after "
        "the same overnight news move together without either moving the other, and "
        "a fund holding these shares moves with them by construction — one that did "
        "not would be broken.</p></div>"
    )


def moves_section(regime, exposure: Dict[str, object],
                  lead: Optional[Dict[str, object]] = None) -> str:
    """Tab 2: what it reads from outside IDX equities, and what it does not."""
    signals = ""
    for s in (getattr(regime, "signals", None) or []):
        state = ("positive" if s.risk_on is True
                 else "negative" if s.risk_on is False else "unavailable")
        cls = "good" if s.risk_on is True else "bad" if s.risk_on is False else ""
        signals += (
            f'<tr><td><strong>{_e(s.name)}</strong></td>'
            f'<td><code>{_e(s.ticker)}</code></td>'
            f'<td><span class="pill {cls}">{state}</span></td>'
            f'<td>{_e(s.detail)}</td></tr>'
        )
    sig_table = (
        '<table><thead><tr><th>Signal</th><th>Series</th><th>Today</th>'
        f"<th>Reading</th></tr></thead><tbody>{signals}</tbody></table>"
        if signals else '<div class="empty">No regime signals in this run.</div>'
    )

    sectors = "".join(
        f'<tr><td>{_e(name)}</td><td class="num">{cnt}</td>'
        f'<td class="num">{cnt / exposure["total"] * 100:.0f}%</td></tr>'
        for name, cnt in exposure["counts"]
    ) if exposure["total"] else ""

    return (
        '<div class="card">'
        "<h3>It reads exactly two things that are not an IDX share price</h3>"
        f"{sig_table}"
        "<p>That is the entire external input. Both are read the same way — is the "
        "series above or below its own 200-day average — and the count of positive "
        "signals picks a deploy level from a three-rung ladder: 30%, 60%, 100%.</p>"

        "<h3>So yes, the rupiah is inside the decision</h3>"
        "<p>A rising USD/IDR means a weakening rupiah, which has historically "
        "pressured IDX equities through foreign outflow. It is one of the two "
        "signals, and it reaches the ticket through a long chain:</p>"
        "<pre class=\"cli\">USD/IDR vs its 200-day mean\n"
        "  → risk-on count (0, 1 or 2)\n"
        "  → deploy 30% / 60% / 100%\n"
        "  → budget = capital × deploy\n"
        "  → how many positions fit above the Rp1 juta minimum\n"
        "  → how many lots of each\n"
        "  → the whole ticket</pre>"
        "<p>It has a second, quieter job: several IDX companies report in USD "
        "while trading in rupiah, and their price-to-book arrives unusable. The "
        "USD/IDR rate is used to repair it. Get the rate wrong and those names are "
        "mis-scored on a factor carrying full weight.</p>"

        "<h3>What it does not read at all</h3>"
        "<p>Nothing below is downloaded, referenced or inferred:</p>"
        "<ul>"
        "<li><strong>Rates and bonds</strong> — the BI 7-day repo rate, the 10-year "
        "IDIB yield, the real yield, or any credit spread.</li>"
        "<li><strong>Other currencies</strong> — SGD, JPY, CNY, EUR. Only USD/IDR "
        "exists here. A yen carry unwind or a yuan devaluation reaches this tool "
        "only after it has already moved IDX prices.</li>"
        "<li><strong>Commodities</strong> — no coal, nickel, tin, gold, CPO or "
        "oil price, despite what the universe is made of.</li>"
        "<li><strong>The world</strong> — no Fed, no US yields, no DXY, no "
        "regional index, no foreign flow data, no VIX.</li>"
        "</ul>"

        "<div class=\"callout\" style=\"border-left-color:var(--warn)\">"
        "<strong>And here is the exposure that creates.</strong> "
        f"<strong>{exposure['commodity_n']} of {exposure['total']} names "
        f"({exposure['commodity_pct']:.0f}%)</strong> in this universe are Energy "
        "or Basic Materials — coal, nickel, tin, gold. Their earnings are a USD "
        "commodity price translated into rupiah. So the book carries a large, "
        "correlated macro bet on exactly the variables the model never measures. "
        "The tool sees the <em>consequence</em> — through momentum, through the "
        "IHSG trend, through USD/IDR — always after the fact, never before.</div>"
        + (f'<div class="scroll"><table><thead><tr><th>Sector</th>'
           f'<th class="num">Names</th><th class="num">Share</th></tr></thead>'
           f"<tbody>{sectors}</tbody></table></div>" if sectors else "")
        + "</div>"

        # Directly after the exclusion list, because it is the measured case for one
        # of those exclusions rather than a separate topic.
        + lead_card(lead)

        + '<div class="card">'
        "<h3>What a macro layer would look like — designed, not built</h3>"
        "<p>The honest way to add any of this is as <em>another signal in the "
        "existing ladder</em>, not as a new mechanism. The regime already counts "
        "positive signals and picks a deploy level; a third and fourth signal "
        "change the count and nothing else, which means the machinery, the "
        "backtest and the explanation all keep working.</p>"
        "<table><thead><tr><th>Candidate</th><th>Series</th><th>Rule</th>"
        "<th>What it would catch</th></tr></thead><tbody>"
        "<tr><td>Local rates</td><td>BI 7-day repo, or the 10-year IDIB yield</td>"
        "<td>Falling or below its own trend = positive</td>"
        "<td>The domestic liquidity cycle, which drives IDX multiples directly and "
        "is invisible to a price trend until it has already worked through.</td></tr>"
        "<tr><td>Commodity basket</td><td>Equal-weight coal + nickel + tin, or a "
        "proxy ETF</td><td>Above its 200-day mean = positive</td>"
        f"<td>The earnings of {exposure['commodity_pct']:.0f}% of this universe. "
        "The single largest unmeasured driver of the book.</td></tr>"
        "<tr><td>Global dollar</td><td>DXY</td><td>Below its 200-day mean = "
        "positive</td><td>Emerging-market risk appetite in general, of which "
        "USD/IDR is only the local expression.</td></tr>"
        "</tbody></table>"
        "<div class=\"callout\"><strong>None of this should be switched on before "
        "it is tested — and the backtest cannot adjudicate it yet.</strong> See "
        "Limits: three of the four live selection stages are absent from the "
        "simulation, so it currently cannot tell you whether a fourth signal "
        "helps. Fix the backtest first, then let it decide. Adding signals because "
        "they sound prudent is how a model acquires parameters nobody can defend."
        "</div></div>"
    )


def limits_section(ladder: Dict[str, object],
                   factors: Optional[Dict[str, object]] = None) -> str:
    """Tab 3: concerns and blockers, worst first."""
    independence = ""
    if factors and factors.get("effective"):
        independence = (
            '<div class="card">'
            "<h3>Ten factors, fewer bets</h3>"
            f"<p>The score is a weighted sum of <strong>{factors['declared']}</strong> "
            f"factors, but they are not {factors['declared']} separate pieces of "
            f"evidence. Measured on today's own correlation matrix they behave like "
            f"<strong>{factors['effective']:.1f}</strong> independent ones, with the "
            f"largest single component explaining {factors['top_share'] * 100:.0f}% "
            "of the variation between them. Value, low volatility and dividend yield "
            "move together: cheap names tend to be calm names tend to be high-yield "
            "names.</p>"
            + (f"<p>The consequence for the score: the weighted composite carries "
               f"<strong>{factors['concentration']:.2f}x</strong> the variance it "
               "would if the factors were independent. That is not a bug &mdash; a "
               "composite of correlated factors is a legitimate design &mdash; but "
               "reading ten weights as ten opinions overstates how diversified the "
               "ranking is.</p>" if factors.get("concentration") else "")
            + "</div>"
        )
    return (independence + (
        '<div class="card">'
        "<h3>The backtest does not test what you are running</h3>"
        "<p>The live path applies four selection stages before a name reaches the "
        "ticket. The simulation applies one.</p>"
        "<table><thead><tr><th>Stage</th><th>Live</th><th>Backtest</th></tr></thead>"
        "<tbody>"
        "<tr><td>Liquidity gate</td><td>yes</td>"
        "<td><span class=\"pill warn\">no</span></td></tr>"
        "<tr><td>Sector cap</td><td>yes</td><td>yes</td></tr>"
        "<tr><td>Decorrelation</td><td>yes</td>"
        "<td><span class=\"pill warn\">no</span></td></tr>"
        "<tr><td>Score floor / ties</td><td>yes</td>"
        "<td><span class=\"pill warn\">no</span></td></tr>"
        "</tbody></table>"
        "<p>So the historical figure describes a strategy that could hold names "
        "your account cannot exit, ran a more concentrated book than you will get, "
        "and acted on score differences the live tool deliberately refuses to act "
        "on. At Rp10 juta the two nearly coincide, because every name passes the "
        "gate anyway. <strong>The gap widens with every rupiah you add</strong> — "
        "which makes it worst exactly where you were asking about it.</p>"
        "<div class=\"callout\" style=\"border-left-color:var(--warn)\">"
        "<strong>Treat the backtest number as an upper bound, not an "
        "expectation</strong> — and the more capital you run, the looser that "
        "bound. Closing this is the single most valuable change left in this "
        "project.</div>"
        "</div>"

        '<div class="card">'
        "<h3>The ranking is more precise-looking than it is</h3>"
        "<p>Scores are z-scores against the rest of the list, so a name's score "
        "depends on which other names are in the universe. The tool measures how "
        "much — by re-scoring with each name left out — and refuses to act on "
        "differences smaller than that. Two names inside the floor are shown as "
        "tied, and one will not be sold to buy the other. Rank order below the top "
        "few is mostly noise; treat the list as a set, not a sequence.</p>"

        "<h3>A verdict of “in line” can be circular</h3>"
        "<p>Fair value is your own multiple against the peer group's median. In a "
        "sector with five names, the median name <em>is</em> the peer group on "
        "that measure, so its fair value comes out equal to its own price and the "
        "verdict is guaranteed. Check the peer count before trusting an “in line”; "
        "a wide <em>measures disagree</em> band alongside it means the same thing "
        "twice.</p>"

        "<h3>One data source, no redundancy</h3>"
        "<p>Everything comes from Yahoo Finance. If a fundamental is wrong there "
        "it is wrong here, and the sanity bounds catch magnitude errors, not "
        "plausible ones. Prices are daily closes: no intraday, and every level on "
        "every page is checked once per session against the close. A stop is a "
        "level to place with your broker, not something this tool can watch.</p>"

        "<h3>The universe was drawn knowing who survived</h3>"
        "<p>The list was chosen on liquidity, affordability and history length — "
        "deliberately blind to past returns — but it was chosen in 2026 from names "
        "that exist in 2026. Any backtest over it inherits that. The measured size "
        "of the effect is in the README; it is the reason the ranking is presented "
        "as a hypothesis rather than a finding.</p>"

        "<h3>It has no opinion on the market as a whole</h3>"
        "<p>Every valuation is relative to other IDX names. If the entire exchange "
        "is expensive, everything still reads “in line”. The regime is the only "
        "thing standing between you and being fully invested in an expensive "
        "market, and it is two moving averages.</p>"
        "</div>"
    ))


def render_method(ladder: Dict[str, object], exposure: Dict[str, object],
                  regime, factors: Optional[Dict[str, object]] = None,
                  sell_days_per_year: Optional[float] = None,
                  lead: Optional[Dict[str, object]] = None) -> str:
    # Appended, never inserted: existing callers pass the first four positionally,
    # and a parameter added in the middle binds silently to the wrong argument.
    return layout.tabbed(
        [("Your capital", capital_section(ladder, sell_days_per_year)),
         ("What moves it", moves_section(regime, exposure, lead)),
         ("Limits", limits_section(ladder, factors))],
        group="method",
    )


METHOD_CSS = """
.method table{width:100%;border-collapse:collapse;margin:8px 0}
tr.you{background:color-mix(in srgb,var(--accent) 12%,transparent)}
tr.you td{font-weight:700}
.method h3{font-size:1.04rem;margin:18px 0 6px}
.method p,.method li{max-width:78ch;line-height:1.55;color:var(--ink-dim)}
.method ul{margin:0 0 10px;padding-left:1.3em}
.method li{margin-bottom:5px}
"""
