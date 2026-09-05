# src/report/guide.py
"""
The Guide page: what this tool is, how to use it, and what every word means.

Reference material, not a view of today. Nothing here reads the run -- no
holdings, no prices, no ranking -- which is deliberate: the page has to read the
same on a fresh install with no data as it does at 12:40 on a Thursday, and a
glossary that depended on today's frame would be empty exactly when somebody
opened it to find out why the page was empty.

**The terms are data.** `TERMS` is a table, not prose baked into HTML, so
`tests/test_guide.py` can walk the vocabulary the rest of the app is capable of
putting on screen -- the pill maps, the action constants, the event labels -- and
fail the suite when one of them has no definition here. A glossary rots the
moment somebody adds a pill; this is the thing that stops it.

Python rather than YAML on purpose: it ships inside the frozen binary with no
seeding step, no `.replaced` backup dance, and no chance of a reader's stale copy
shadowing the shipped one.
"""
from __future__ import annotations

import html
from typing import Dict, List, NamedTuple, Tuple

from report import layout


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


class Term(NamedTuple):
    word: str
    group: str
    meaning: str
    seen_in: str = ""
    # Internal constants this entry covers. `EXIT` and `ROTATE` are branch names
    # the reader never sees verbatim -- an EXIT renders as "SELL all 3" and a
    # ROTATE as "out-ranked" -- but a new one appearing with no entry here is
    # exactly what the completeness test exists to catch, so it checks these too.
    aka: Tuple[str, ...] = ()


# The seven groups, in the order they are rendered. A term carrying a group that
# is not here is a typo, and the test says so rather than silently dropping it
# into a section nobody scrolls to.
GROUPS: Tuple[Tuple[str, str], ...] = (
    ("money", "Money and mechanics"),
    ("decision", "The decision"),
    ("risk", "Risk and exits"),
    ("ranking", "The ranking"),
    ("value", "Worth vs peers"),
    ("market", "The market"),
    ("events", "Events"),
    ("performance", "How you are doing"),
)


TERMS: Tuple[Term, ...] = (
    # ---------------------------------------------------------------- money
    Term("Lot", "money",
         "100 shares. IDX trades in lots, so you cannot buy 150 shares of "
         "anything — only 1 lot or 2. It is why a name whose single lot costs "
         "more than your slot cannot be bought at all, however well it ranks.",
         "every order row"),
    Term("Stamp duty", "money",
         "Rp10,000, charged once per DAY on which you sell anything — not per "
         "trade. Selling four names on one day costs one stamp; spreading them "
         "over four days costs four. It is the single biggest reason this tool "
         "batches sells and refuses trims below about Rp452,000.",
         "Do this today → estimated cost"),
    Term("Buy fee / sell fee", "money",
         "0.19% going in, 0.29% coming out, as configured for Indopremier. "
         "Change them in Settings if your broker differs — they are used for "
         "sizing and for every break-even figure, not just for display.",
         "Settings"),
    Term("Round trip", "money",
         "What one position costs to open and later close: both fees plus the "
         "stamp. A position has to gain this much before you are square, which "
         "is why the ticket states it as a percentage you must beat.",
         "Do this today → break even on this book"),
    Term("FIFO", "money",
         "First in, first out — the order your lots are considered sold in when "
         "working out realised profit. Buy at 1,000 then at 1,200, sell one lot, "
         "and the 1,000 lot is the one that left.",
         "Portfolio → How you are doing"),
    Term("Paid in", "money",
         "The money you have put into the account. Either the capital figure in "
         "Settings, or the sum of your recorded deposits and withdrawals once a "
         "cash ledger exists — the ledger wins, so the two cannot disagree.",
         "Regime and capital"),
    Term("Cash", "money",
         "What is not currently in a position, from the ledger.", "top bar"),
    Term("Holdings", "money",
         "What your open positions are worth at the last close.", "top bar"),

    # ------------------------------------------------------------- decision
    Term("Do this today", "decision",
         "The ticket: the whole decision, as instructions. Every row carries an "
         "action, a size in lots and rupiah, the stop the position sits under, "
         "and one line saying why. Nothing else on the page is an instruction.",
         "Markets"),
    Term("BUY", "decision",
         "Open or add to a position, at the stated lots.", "Do this today"),
    Term("SELL", "decision",
         "Close the whole position. Either a stop was hit or the last rung was "
         "reached (the exit plan's own decision), or the book is being cut back. "
         "The row always says which of those it is.",
         "Do this today", aka=("exit",)),
    Term("TRIM", "decision",
         "Sell PART of a position and let the rest run. It is a different "
         "decision from SELL: taking profit at a target, not being wrong.",
         "Do this today"),
    Term("HOLD", "decision",
         "Do nothing today. Not a blank row — it still carries the stop, and how "
         "far away that stop is IS the answer to whether to keep it.",
         "Do this today"),
    Term("WAIT", "decision",
         "The ranking wants this name but a cooldown is blocking it. Buying now "
         "would undo a sale you just made and pay both sides of the spread for "
         "the privilege.",
         "Do this today"),
    Term("Target book", "decision",
         "What the ranking would have you hold today, given the budget. Held "
         "names outside it are candidates for selling — but only for a stated "
         "reason, never just for being outside.",
         "Do this today"),
    Term("de-risk", "decision",
         "A sale made because the REGIME cut your budget, not because the name "
         "is bad. When you hold more than today's budget allows, the book is cut "
         "worst-ranked first and nothing is bought. A rank-4 name can be sold "
         "this way; the row says so, so you do not read it as a verdict.",
         "Do this today"),
    Term("out-ranked", "decision",
         "A sale made because a better name took the slot — a rotation. It only "
         "fires when the replacement beats this name by more than the score "
         "floor, so the tool cannot churn your book on a difference it cannot "
         "actually measure.",
         "Do this today", aka=("rotate",)),
    Term("Cooldown", "decision",
         "A name sold recently is blocked from being bought or topped up again "
         "for a few sessions. Without it the stop sells on Monday and the "
         "re-rank buys it straight back on Tuesday, costing 0.29% out, the "
         "stamp, and 0.19% back in to end up exactly where you started.",
         "Do this today"),
    Term("Deploy %", "decision",
         "The share of your capital the market regime says to have at work "
         "today: 30%, 60% or 100%, by how many risk signals are positive.",
         "Regime and capital"),
    Term("Positions", "decision",
         "How many names the budget is split across. Chosen, not fixed: too many "
         "slots on a small budget means the Rp10,000 stamp dominates every trade.",
         "Regime and capital"),

    # ----------------------------------------------------------------- risk
    Term("ATR", "risk",
         "Average True Range — how far this stock moves in a typical day, in "
         "rupiah. Every stop here is a multiple of the name's OWN ATR rather "
         "than a fixed percentage, because the same 7% stop is an ordinary "
         "fortnight for one IDX stock and a coin flip for another.",
         "the basis of every stop"),
    Term("Stop", "risk",
         "The price at which you close the position and accept being wrong. "
         "Set at 2.5 × ATR below your entry, then widened if that would sit "
         "inside the round-trip cost.",
         "Do this today, What you hold"),
    Term("initial stop", "risk",
         "The first stop, measured from what you paid. It has not moved yet.",
         "What you hold → Stop"),
    Term("break-even stop", "risk",
         "The stop after it has been raised to where you get out square — above "
         "your entry price, because entry already paid 0.19% and exiting pays "
         "0.29% plus the stamp. Stopping at the entry price is a small loss "
         "wearing a neutral name.",
         "What you hold → Stop"),
    Term("trailing stop", "risk",
         "A stop that follows the price up and never comes back down. What takes "
         "you out of a winner: the runner is sold when the trail is hit, not at "
         "a target you guessed in advance.",
         "What you hold → Stop"),
    Term("R", "risk",
         "One R is what you risk on the position — the distance from your entry "
         "to your stop. A +2R level is twice that distance above entry. Levels "
         "are quoted in R rather than percent so a calm stock and a wild one are "
         "measured on the same scale.",
         "The plan, e.g. “+2R Rp1,131”"),
    Term("The plan", "risk",
         "The column holding a position's whole staged exit at once — every rung "
         "with its price and lots, what is already done, and what runs. Shown in "
         "full rather than only the next step: the point of a staged exit is "
         "knowing the shape of it before the price gets there.",
         "What you hold"),
    Term("Ladder", "risk",
         "The staged exit: trim some at +1R, some at +2R, let the rest run on "
         "the trailing stop. Rungs are whole lots and each must be big enough to "
         "pay its own stamp, or it rolls forward into the next one.",
         "What you hold → The plan"),
    Term("Runner", "risk",
         "The lots left after every rung has been taken — the part that runs to "
         "the trailing stop instead of a fixed target.",
         "What you hold → The plan"),
    Term("At risk", "risk",
         "What this position loses if the stop is hit, in rupiah and as a "
         "percentage of your capital. The number that matters more than the "
         "rupiah deployed: Rp1.3 juta of a name is not a decision, Rp220,000 at "
         "risk is.",
         "What you hold, and the book total"),
    Term("Entry risk", "risk",
         "The same figure for a name you do not own yet — what a proposed BUY "
         "would put at risk under the stop it would be opened with.",
         "Do this today → BUY rows"),
    Term("capped", "risk",
         "The stop wanted to sit further away than the maximum allowed (15% of "
         "the price), so it was pulled in. The position is riskier per share "
         "than the ATR alone would suggest.",
         "What you hold → Stop"),
    Term("over", "risk",
         "This position risks more of your capital than the per-trade budget "
         "allows. Reported, never silently resized — shrinking the position "
         "would make the ticket stop describing the strategy the backtest ran.",
         "Do this today"),
    Term("NO STOP", "risk",
         "No stop could be set, because the price series has no measurable daily "
         "range. Not the same as a wide stop: there is no level to act on, so "
         "the position is unmanaged until there is.",
         "What you hold → Today"),
    Term("CHECK ENTRY", "risk",
         "The recorded entry price cannot be a real fill — it is too far from "
         "where the stock actually traded that day, usually a typo. Nothing else "
         "is computed for the position, because everything else is measured from "
         "that price. Fix the trade in the ledger.",
         "What you hold → Today"),

    # -------------------------------------------------------------- ranking
    Term("Rank score", "ranking",
         "The 0-to-1 number beside each candidate. It is the composite rescaled "
         "so the best name today reads 1.00 — which makes it useful for ordering "
         "and useless for asking how far apart two names are, or whether "
         "anything is cheap in absolute terms.",
         "Best candidates"),
    Term("raw score", "ranking",
         "The composite before rescaling, on its own scale. Distances are "
         "meaningful here, so this is what the score floor is measured against "
         "and what the ticket compares when deciding whether one name really "
         "beats another.",
         "screener_results.csv"),
    Term("Score floor", "ranking",
         "How far a name's score moves when the universe gains or loses a single "
         "member, measured by re-running the scoring with each name left out. "
         "Two names closer together than this are tied, not ranked.",
         "Why → Which of these are actually level"),
    Term("Tied", "ranking",
         "Two names the score cannot tell apart. The order between them is not a "
         "preference, and the tool will not sell one to buy the other. Inside a "
         "tie the name that least duplicates what you already hold goes first.",
         "Do this today, Why"),
    Term("Factor", "ranking",
         "One measurable property that feeds the score: P/E, P/B, dividend "
         "yield, ROE, gross margin, debt/equity, volatility, and momentum over "
         "1, 6 and 12 months. Ten of them, each with a stated weight.",
         "Why → Scored on ten factors"),
    Term("z-score", "ranking",
         "How unusual a value is compared with the rest of the list, in standard "
         "deviations. Every factor is z-scored so that a P/E and a dividend "
         "yield — different units entirely — can be added together.",
         "the scoring step"),
    Term("Sector-neutral", "ranking",
         "Scored against the name's own sector rather than the whole market, for "
         "factors where sectors differ structurally. A bank's debt ratio "
         "compared with a miner's says nothing.",
         "the scoring step"),
    Term("Sector cap", "ranking",
         "At most a set number of names per sector in the shortlist, so one bad "
         "sector cannot take the whole book down. A name crowded out this way is "
         "shown separately from one that failed a gate — they are different "
         "facts.",
         "Skipped"),
    Term("Correlation", "ranking",
         "How closely two names move together. Applied after the sector cap "
         "because labels miss it: two different sectors can correlate 0.87 while "
         "tin, palm oil and coal sit near 0.30.",
         "Why → the decorrelation step"),
    Term("Shortlist", "ranking",
         "The few names that go through to sizing. Everything dropped here "
         "passed every gate and was simply out-ranked.",
         "Why → Keep the best few"),
    Term("Universe", "ranking",
         "Every name the tool is allowed to consider. Chosen on liquidity, lot "
         "affordability, history length and a measurable ATR — deliberately "
         "blind to past returns, so the list cannot be a story about who "
         "survived.",
         "Screener"),
    Term("Imputed", "ranking",
         "A missing fundamental filled with the sector median rather than "
         "dropped. The count is shown because an imputed name is ranked partly "
         "on a number nobody reported.",
         "Markets → data quality note"),
    Term("Liquidity gate", "ranking",
         "A name must trade a minimum value per day, and your position must stay "
         "a small share of that. A stock you cannot sell is not a position.",
         "Skipped"),
    Term("Liquidity", "ranking",
         "Whether you could get back out. Measured as the value this name trades "
         "in a normal day against the size of the position you would be taking — "
         "not as a judgement of the company.",
         "Best candidates"),
    Term("ok", "ranking",
         "This name trades enough that your position size is not a problem.",
         "Best candidates → Liquidity"),
    Term("thin", "ranking",
         "It trades, but your position would be a large share of a normal day's "
         "volume — getting out may move the price against you.",
         "Best candidates → Liquidity"),

    # ---------------------------------------------------------------- value
    Term("Worth vs peers", "value",
         "A second opinion, entirely separate from the ranking: what the price "
         "would be if this company traded on its peer group's multiples. It does "
         "NOT feed the ranking or the ticket — the score ranks, this values.",
         "Best candidates"),
    Term("Peer group", "value",
         "The sector when it holds at least four names, otherwise the whole "
         "universe. The brief always says which was used. In a small sector a "
         "name can BE the median, in which case its fair value equals its own "
         "price and the verdict is close to meaningless.",
         "Screener → valuation"),
    Term("below peers", "value",
         "The price is under both fair-value estimates. Cheap relative to the "
         "peer group — which is not the same as cheap.",
         "Worth vs peers"),
    Term("in line", "value",
         "The price sits inside the range the two estimates span.",
         "Worth vs peers"),
    Term("above peers", "value",
         "The price is over both estimates. Note that a company earning 60% on "
         "equity SHOULD trade above its peers; this method takes no view on "
         "whether a premium is deserved, which is why ROE sits beside it.",
         "Worth vs peers"),
    Term("one measure", "value",
         "Only one of the two routes worked — usually no usable P/E or no usable "
         "P/B. A single point, not a range, so it gets its own state rather than "
         "being dressed up as a verdict.",
         "Worth vs peers"),
    Term("cannot value", "value",
         "Neither multiple was usable, or there is no price. Said out loud "
         "rather than left blank, because a silent gap reads as “nothing to "
         "say about it”.",
         "Worth vs peers"),
    Term("measures disagree", "value",
         "The earnings route and the book route are more than 60% apart, "
         "relative to their midpoint. Both are shown and neither is averaged "
         "away — the gap IS the uncertainty. Treat the verdict as a hint, not a "
         "number, and do not size up on it.",
         "Worth vs peers"),
    Term("P/E", "value",
         "Price divided by earnings per share. The earnings route to fair value: "
         "your EPS × the peer group's median P/E.",
         "Screener"),
    Term("P/B", "value",
         "Price divided by book value per share. The asset route: your book "
         "value × the peer group's median P/B. It disagrees with P/E most for "
         "capital-light businesses.",
         "Screener"),
    Term("ROE", "value",
         "Return on equity — what the company earns on the capital it holds. "
         "Shown next to every valuation verdict so you can judge whether a "
         "premium is earned.",
         "Screener"),

    # --------------------------------------------------------------- market
    Term("Regime", "market",
         "Two signals — is the index above its 200-day mean, and is the rupiah "
         "holding — read together to decide how much of your capital should be "
         "at work at all.",
         "Regime and capital"),
    Term("RISK-ON", "market",
         "Both signals positive: deploy the full budget.", "Regime and capital"),
    Term("RISK-OFF", "market",
         "Both signals negative: hold back, deploy at most 30%. If you already "
         "hold more than that, the book is cut back rather than added to.",
         "Regime and capital"),
    Term("MIXED", "market",
         "One signal positive, one not: deploy about 60%.",
         "Regime and capital"),
    Term("IHSG", "market",
         "The Jakarta Composite Index — the whole exchange, and the benchmark "
         "your results are measured against.",
         "the chart"),
    Term("200-day mean", "market",
         "The average close over the last 200 sessions. Price above it is the "
         "trend signal; the chart draws both so you can see the distance.",
         "the chart"),

    # --------------------------------------------------------------- events
    Term("earnings", "events",
         "A results date. Yahoo has one for only about a third of the universe, "
         "so most of this calendar is what you record yourself.",
         "Events"),
    Term("ex-dividend", "events",
         "The date from which a buyer no longer receives the declared dividend.",
         "Events"),
    Term("index review", "events",
         "MSCI or FTSE adding or dropping names. The one event whose PAST still "
         "counts: a deleted name keeps losing passive money for weeks, so a "
         "review stays on the ticket line for 21 days after it takes effect.",
         "Events, and ticket rows"),
    Term("note", "events",
         "Anything else you wanted on the calendar.", "Events"),
    Term("auto", "events",
         "Fetched from Yahoo Finance rather than recorded by you. Yahoo carries "
         "an earnings date for only about a third of this universe, so most of "
         "the calendar is not this.",
         "Events → Source"),
    Term("you", "events",
         "You recorded it, in configs/events.yaml or with --event. Yours "
         "overrides a built-in row for the same date and name.",
         "Events → Source"),
    Term("est.", "events",
         "Estimated from the months this company has paid in before. A pattern, "
         "not a schedule — companies move and skip payments.",
         "Events → Source"),
    Term("built in", "events",
         "An index-review date that shipped with the app and refreshes when you "
         "install a new build. Not something you typed.",
         "Events → Source"),
    Term("Coverage", "events",
         "How many names have an earnings date from any source. The point of "
         "showing it: an empty row means we cannot see, not that nothing is "
         "coming.",
         "Events"),
    Term("Blind", "events",
         "A name with no earnings date anywhere. Reported as “no earnings date "
         "available — check IDX or CNBC yourself”, never as “nothing scheduled”.",
         "ticket rows"),

    # ---------------------------------------------------------- performance
    Term("Realised", "performance",
         "Profit on positions you have actually closed, net of both fees and the "
         "stamp. The only number that is real money.",
         "Portfolio → How you are doing"),
    Term("Unrealised", "performance",
         "Profit on paper, on positions still open at the last close. It is not "
         "yours until it has paid the sell fee and the stamp.",
         "Portfolio, What you hold"),
    Term("Index shadow", "performance",
         "What the same money, moved on the same days, would have done in IHSG "
         "instead — the fair comparison for whether the picking added anything. "
         "It moves the amount you actually deployed net of fees, so the "
         "benchmark is not given free trading.",
         "Portfolio → How you are doing"),
    Term("Watchlist benchmark", "performance",
         "The same comparison against holding your whole universe equally, "
         "rather than the index. It answers a different question: did the "
         "ranking help, or was it the list?",
         "Portfolio → How you are doing"),
)


def _by_group() -> Dict[str, List[Term]]:
    out: Dict[str, List[Term]] = {key: [] for key, _ in GROUPS}
    for term in TERMS:
        out.setdefault(term.group, []).append(term)
    return out


# --------------------------------------------------------------------- render
def about_section() -> str:
    """What the tool is, and the things it refuses to do."""
    return (
        '<div class="card">'
        "<p><strong>This is a screener built around one set of constraints, not a "
        "general-purpose terminal.</strong> It assumes an Indopremier account "
        "(0.19% in, 0.29% out, Rp10,000 stamp on every day you sell), one or two "
        "trades a week, and a decision made over a lunch hour. Those numbers are "
        "not cosmetic — they are inside the ranking, the position sizing and "
        "every exit level, which is why a trim smaller than about Rp452,000 is "
        "refused outright.</p>"

        "<p>It reads daily closing prices and company fundamentals from Yahoo "
        "Finance, scores every name in the universe on ten factors, and turns "
        "the result into a list of instructions with the exact lots, the rupiah, "
        "the stop each one sits under, and one line saying why.</p>"

        "<h3>What it will not do</h3>"
        "<ul>"
        "<li><strong>It does not predict.</strong> The ranking is a hypothesis "
        "about relative value and momentum. The backtest on your own machine is "
        "the only evidence for it, and the ticket says so when that has never "
        "been run.</li>"
        "<li><strong>It does not trade.</strong> Nothing here reaches a broker. "
        "Every order is something you place yourself.</li>"
        "<li><strong>It cannot watch a stop for you.</strong> There is no live "
        "feed. Levels are checked once per session against the close, never "
        "intraday — put the stop in Indopremier if you want it to act while you "
        "are not looking.</li>"
        "<li><strong>It has no view on the market as a whole.</strong> Every "
        "valuation is relative to other IDX names, so if the whole exchange is "
        "expensive, everything still reads “in line”.</li>"
        "</ul>"

        "<h3>Your data stays here</h3>"
        "<p>Holdings, trades, cash and settings are files on this machine. The "
        "only thing that leaves it is a price request to Yahoo Finance.</p>"
        "</div>"
    )


def routine_section() -> str:
    """The loop, naming the controls that actually exist."""
    return (
        '<div class="card">'
        "<h3>The five-minute routine</h3>"
        "<ol>"
        "<li><strong>Open it.</strong> It reopens the screen it last saved — "
        "opening the app is not a request for fresh data.</li>"
        "<li><strong>Update data</strong> (top bar, ~40s) fetches fresh prices "
        "and re-ranks. <strong>Rebuild</strong> (~2s) only redraws what is "
        "already loaded, which is what you want after recording a trade.</li>"
        "<li><strong>Read “Do this today”.</strong> It is the whole decision. "
        "Sells come first because a breached stop is the most urgent thing on "
        "the page, then trims, then buys, then what needs no action.</li>"
        "<li><strong>Check each row's stop and reason</strong> before acting. "
        "A row that says <em>de-risk</em> is not saying the name is bad.</li>"
        "<li><strong>Place the orders in Indopremier yourself.</strong></li>"
        "<li><strong>Record what you actually filled</strong> — Portfolio → "
        "Record what you did. The fill price matters: everything about the "
        "position afterwards is measured from it.</li>"
        "</ol>"

        "<h3>Where things are</h3>"
        "<ul>"
        "<li><strong>Markets</strong> — the ticket, what you hold and its exit "
        "plan, the regime, the candidates, and what was skipped.</li>"
        "<li><strong>Portfolio</strong> — record trades, cash and dividends; the "
        "ledger; and how you are doing against the index.</li>"
        "<li><strong>Screener</strong> — every name with its evidence.</li>"
        "<li><strong>Why</strong> — the funnel: how the universe became today's "
        "ticket, stage by stage, with what each stage dropped.</li>"
        "<li><strong>Settings</strong> — capital, the fee rates, and every gate's "
        "numbers. Edits are written to <code>configs/user.yaml</code>, never to "
        "the shipped defaults, so a bad change is one deleted file away from "
        "fixed.</li>"
        "</ul>"

        "<h3>Two small things</h3>"
        "<ul>"
        "<li>The <strong>A A A</strong> buttons in the top bar set the text size "
        "for the whole page, and the choice survives a restart.</li>"
        "<li>Everything also works from the command line — "
        "<code>python main.py --log BUY BBRI 3 4150</code> records a trade, "
        "<code>--events</code> lists the calendar, <code>--backtest</code> tests "
        "the ranking against simply holding everything.</li>"
        "</ul>"
        "</div>"
    )


def glossary_section() -> str:
    """Every term, grouped. One or two sentences, and the number where there is one."""
    grouped = _by_group()
    out = ('<div class="card"><p class="note">Every word the app can put on '
           "screen. If something here appears on a page and not in this list, "
           "that is a bug — the test suite checks it.</p></div>")
    for key, title in GROUPS:
        terms = grouped.get(key) or []
        if not terms:
            continue
        rows = "".join(
            f'<div class="gl-row"><div class="gl-word">{_e(t.word)}'
            + (f'<span class="gl-where">{_e(t.seen_in)}</span>' if t.seen_in else "")
            + f'</div><div class="gl-mean">{t.meaning}</div></div>'
            for t in terms
        )
        out += f'<h3 class="gl-head">{_e(title)}</h3><div class="gl">{rows}</div>'
    return out


def render_guide() -> str:
    """The page body: three sections behind one tab strip."""
    return layout.tabbed(
        [("What this is", about_section()),
         ("Using it", routine_section()),
         ("Every term", glossary_section())],
        group="guide",
    )


# Styling for the glossary only. Lives here rather than in the shell for the same
# reason the ticket's does: it belongs to content this module renders.
GUIDE_CSS = """
.gl{margin-bottom:16px}
.gl-head{font-size:0.96rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);margin:18px 0 6px;padding-bottom:4px;
  border-bottom:1px solid var(--line)}
.gl-row{display:grid;grid-template-columns:minmax(0,15em) minmax(0,1fr);gap:14px;
  padding:7px 0;border-bottom:1px solid var(--line);align-items:baseline}
.gl-row:last-child{border-bottom:none}
.gl-word{font-weight:700;color:var(--ink)}
.gl-where{display:block;font-weight:400;font-size:0.84rem;color:var(--muted);
  margin-top:2px}
.gl-mean{color:var(--ink-dim);line-height:1.55;max-width:70ch}
.card h3{font-size:1.04rem;margin:16px 0 6px}
.card ol,.card ul{margin:0 0 10px;padding-left:1.3em;max-width:78ch}
.card li{margin-bottom:5px;line-height:1.55;color:var(--ink-dim)}
/* One column below the breakpoint: a 15em term column beside prose is unreadable
   once the panel is narrower than about 620px. */
@media (max-width:1100px){
  .gl-row{grid-template-columns:minmax(0,1fr);gap:2px}
}
"""
