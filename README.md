# IDX Quant Screener

A personal stock screener for the **Indonesia Stock Exchange**, built for one
investor with a small account and a lunch break.

It ranks 74 IDX names on a transparent multi-factor score, then does the part most
screeners skip: it works out **what you can actually buy** — whole 100-share lots,
inside your capital, in stocks liquid enough to sell again — and costs it against
your real broker fees. The output is a single HTML page you read in about ten
minutes.

> **Not investment advice.** A personal research tool. See [Limitations](#limitations).

---

## What it produces

`data/output/brief.html` — one self-contained page, opened in a native desktop
window. Five destinations on a left rail:

| Rail | What it is |
|---|---|
| **Markets** | The decision. Ticket, IHSG, your capital, candidates, events, holdings |
| **Portfolio** | Record a trade, and the ledger: monthly profit, open positions, every round-trip |
| **Screener** | All 74 names and the evidence behind the ranking |
| **Why** | The decision, stage by stage, plus a lookup for any name |
| **Settings** | The broker fees, gates and factor weights driving every rule |

**Markets** is the landing page and it is complete on its own — you can trade off it
and never open the other four:

| Section | What it answers |
|---|---|
| Market right now | Risk-on or risk-off, and what share of capital to deploy today |
| **Do this today** | The ticket: BUY/TRIM/SELL/HOLD, exact lots, exact rupiah, the stop each one sits under, and estimated fees |
| **What you hold → Exit plan** | Where each position gets sold: a stop, the trim levels, and what runs |
| What you hold → Worth & health | Current positions, P&L, peer multiples and a health flag per name |
| Best candidates you can afford | Ranked, each with a fair-price range and a plain-English reason |
| **Worth vs peers** | Below / in line / above the price peer multiples imply — on candidates *and* on what you already hold |
| Skipped | Names that ranked well but failed the liquidity or lot-size gate |
| Events next 14 days | Earnings, ex-dividend, index reviews — labelled auto / you / est. |
| How you're doing | Realised P&L net of fees, and your total versus the same money in IHSG |

**Why** is how it got there — the chain from 74 names to today's ticket:

| Section | What it answers |
|---|---|
| The funnel | Every stage with its counts: `74 → 74 → 70 → 70 → 8 → 3` |
| One card per stage | The rule in plain words, the config key that sets it, and every name it dropped with the reason that gate gave |
| **Why wasn't this suggested?** | Type any ticker — all 74, including names cut at the first gate — and see exactly where it stopped |

**Screener** is the evidence behind it:

| Section | What it answers |
|---|---|
| Every stock, every factor | All 74 names with all 10 z-scores. Click any column to sort |
| **What is it worth?** | The arithmetic behind every verdict: EPS, book value, peer multiples, both estimates, peer group |
| Why these scored what they scored | Signed factor contributions per pick — which factor actually drove it |
| Are the factors independent? | Correlation heatmap. Momentum counted three times is one factor, not three |
| The regime signal, drawn | The index against its 200-day mean, so you can check the call by eye |
| Seasonality by month | All twelve months with `n` on every bar, thin samples greyed |
| What if I sized it differently? | Capital / position-count / deploy sliders over precomputed real allocations |
| Where the money actually sits | Sector exposure against the cap |
| Data quality | Which names were scored neutral on what |

### It is a terminal, not a document

A left rail of destinations, a dense grid of panels, dark. **The window never
scrolls** — `html, body { overflow: hidden }` — so the ticket stays where you left it
while you read a 74-row table beside it. Only panel bodies scroll. Below 900px that
is lifted and the page scrolls normally, because a phone has no room for panels.

Three breakpoints, not two: a single jump from three columns to one leaves the
900–1200px band showing three squeezed columns, which is worse than either end.
Tables scroll sideways rather than compressing.

**Rebuild and Re-run** sit in the top bar with their real cost on the label. Rebuild
redraws from what is already loaded in about two seconds — that is the one you want
after recording a trade or changing a setting, since the market has not moved.
Re-run screen fetches every ticker again and re-ranks, about a minute. Both
appear only once the app has answered, because a refresh button that cannot refresh
is the same lie as a form with nothing behind it.

**Two things from a real broker's terminal are deliberately missing.** There is no
BUY or SELL button — a control that looks like it trades, in a tool that sits beside
your actual broker, is a hazard and not a feature; the top bar shows the regime and
deploy % instead. And the header reads *as of <timestamp>*, never a ticking clock: a
live clock over daily data claims a feed this tool has never had. Tests assert that
no button, link or form on the page reads BUY or SELL.

The ticket is the first panel of the landing page, in the DOM before anything else,
and it keeps that place when it says HOLD or is empty. Ten panels of z-scores can
make it feel as though something must be done today; usually nothing must, and an
empty ticket is a result.

**The Why view is recorded, not reconstructed.** Each gate writes down what it did
as it runs, and the view only reads that — so the explanation cannot drift away from
the decision. A test asserts the funnel reconciles at every stage (`in − dropped ==
out`, and each stage starts where the last ended); it caught a real gap on the first
run, where five names left the sizing stage unaccounted for.

Also written: `screener_results.csv` (full ranked universe with every `z_*`),
`top_picks.csv`, `ticket.csv`, `factor_correlations.csv`.

---

## Quick start

### Just run it (Windows)

Unzip `IDX-Terminal-windows.zip` anywhere and double-click **IDX Terminal.exe**.
No Python, no virtualenv, no command line. ~44MB zipped, ~91MB unzipped, and it
starts in under two seconds.

**The first launch asks how much you are investing**, before it fetches anything,
and saves it to `configs\user.yaml` beside the exe. You can change it later in
Settings, or by editing that file:

```yaml
account:
  capital_rp: 25000000     # your number
```

If it is ever still on the shipped placeholder, the ticket says so in red above the
lot counts. Every number under that banner is sized for money that is not yours.

Everything the app writes — configs, price cache, the terminal it generates, your
journal — stays in that folder. Nothing is sent anywhere.

> **Windows will warn you the first time** ("Windows protected your PC"), because the
> build is not signed with a paid certificate. *More info → Run anyway*. Some
> antivirus tools flag PyInstaller programs for the same reason. Building it yourself
> from source is the alternative, below.

### Or from source

```bash
pip install -r requirements.txt
python main.py                 # opens the terminal in a native window
python main.py --browser       # open it in a browser tab instead
python main.py --png           # also write the old matplotlib screener_analysis.png
```

### Building the .exe yourself

```bash
pip install -r requirements-dev.txt
python packaging/build.py      # -> dist/IDX-Terminal-windows.zip
```

Windows only; PyInstaller does not cross-compile. The build **refuses to finish** if
it finds a personal file or your capital figure anywhere in the output — a comment
in a spec file is a promise, and this is a check. It caught a real one on the first
run: the example capital I had written into the bundled README happened to be the
same number as the real one.

First run fetches ~2 years of prices for 74 tickers and takes a minute or two;
afterwards the cache makes it fast.

The window comes from `pywebview`, which wraps the OS webview — on Windows that is
the Edge WebView2 runtime already present on Win10/11, so the page renders in the
same engine a browser would use. About 6MB of dependencies, and **it is not
load-bearing**: if pywebview is missing, WebView2 is absent, or the backend throws,
the launcher returns cleanly and falls back to a browser tab. A dependency must not
become a way for the tool to stop working.

There is still no server to start and no port to remember — the output is a single
~245KB HTML file that opens from disk, offline, and keeps working as an archive of
what the tool said on a given day.

### Your data stays local

This repository is public; your portfolio is not. These are git-ignored and never
leave your machine:

| File | What it holds |
|---|---|
| `data/journal.csv` | every trade, price, fee and realised P&L |
| `data/journal_marks.csv` | portfolio value over time |
| `current_holdings.yaml` | your positions and average cost |
| `configs/user.yaml` | your capital and universe edits |
| `configs/events.yaml` | your event calendar |
| `data/output/` | every brief, ticket and backtest you generate |

`current_holdings.example.yaml` and `configs/events.example.yaml` show the format.
You rarely need them: `--log` writes your holdings file and `--event` writes your
calendar, both creating the file on first use.

**Set your real capital in `configs/user.yaml`**, not in `default.yaml`:

```yaml
account:
  capital_rp: 25000000
```

It is applied on top of `configs/default.yaml`, so you only list what differs. The
value committed to `default.yaml` is a deliberate placeholder.

Broker settings live in `configs/default.yaml`:

```yaml
account:
  capital_rp: 100000000       # placeholder - override in configs/user.yaml
  min_position_rp: 1000000    # below this, the Rp10k stamp dominates the trade

broker:
  buy_fee: 0.0019             # Indopremier
  sell_fee: 0.0029
  stamp_duty_rp: 10000        # per DAY that has a sell, not per order
  lot_size: 100
```

---

## Logging trades

**In the app**, the Portfolio page has a form: pick Bought or Sold, type the ticker,
lots and price, and the fee breakdown fills in as you type — gross, the 0.19% or
0.29% commission, and the stamp. The button says *Record trade*, past tense: this
records what you already did in Indopremier and places no orders.

The preview is computed by Python calling the same `build_trade` that records the
row, never re-implemented in JavaScript. That matters more than it looks: the
Rp10,000 stamp is charged only on the **first** sell of a day, so a preview that did
not read your journal would quote it on a second sell and then record zero, and you
would find out reconciling against your broker weeks later.

Selling more lots than you hold is refused rather than warned — it is a typo often
enough that recording it would corrupt the FIFO matching for every later trade in
that name.

Opened as a plain file instead of in the app, there is no Python behind the page, so
the form is replaced by the equivalent command. A form with nothing behind it is
worse than no form, because it looks like it worked.

### The ledger

Three tables, all from the journal and none of them needing a price:

- **Realised, by month** — a round-trip counts in the month you **sold** it, already
  net of the buy fee paid earlier plus the sell fee and the stamp. A month's figure
  never changes after the month ends, so month-on-month comparison means something.
  The rows sum to the headline exactly; there is a test for that.
- **Still open** — average cost includes the buy fee you actually paid, which is the
  price the position has to beat to be genuinely ahead.
- **Every completed round-trip** — matched first-in first-out, the way an Indonesian
  broker statement does, with the fee share and net for each.

`data/journal.csv` stays a plain CSV you can open in a spreadsheet and check.

### Or from the command line

After you execute in Indopremier, record it in one line:

```bash
python main.py --log BUY  BBRI 3 4150               # 3 lots @ Rp4,150, dated today
python main.py --log SELL BBCA 2 6450 --note "MSCI rebalance"
python main.py --log BUY  TLKM 2 2610 --source own  # your call, not the tool's
python main.py --journal                            # performance report
python main.py --mark                               # snapshot value vs IHSG
```

## Events

```bash
python main.py --event ADRO earnings 2026-08-27
python main.py --event MSCI review 2026-08-28 --note "Aug index review"
python main.py --events                             # what's coming, and what we can't see
```

Yahoo Finance has an earnings date for only about a third of the universe — the gaps
include SRTG, TINS, TAPG, MAPI, ISAT, ITMG and KLBF. So `configs/events.yaml` is the
primary source and yfinance fills in the rest.

Because of that gap, every name resolves to one of three visibly different states, and
the third is the important one:

| State | Shown as |
|---|---|
| Event known | `⚠ earnings in 4 days` |
| Genuinely clear | `nothing scheduled in the next 14 days` |
| **No data at all** | `— no earnings date available, check IDX or CNBC yourself` |

Events **warn but never filter**. A blocking rule could only fire on the third of the
universe we can see, so it would quietly tilt the portfolio toward the two thirds we
can't — a bias you'd never notice.

Each `--log` prints the fee it computed so you can check it against the broker's
confirmation, and rewrites `current_holdings.yaml` to match — so the next brief always
diffs against what you actually own.

The `--source` flag is what makes the report able to separate the screener's picks from
your own discretionary calls.

---

## When to get out

Until recently this tool decided what to **buy** and never decided what to **sell**:
the ticket proposed a sale for exactly one reason, that a name had fallen out of the
target book on a re-rank. A position could halve with nothing on the page mentioning
it.

Every open position now carries three things, on the **Exit plan** tab and in the
ticket:

| | |
|---|---|
| **A stop** | `entry − 2.5 × ATR(14)`, where the trade is wrong |
| **A ladder** | Trims at +1R and +2R, in whole lots, where `R = entry − stop` |
| **A runner** | What is left, on a trailing stop, until that is hit — which is how you end up fully out |

**The distance is the stock's own daily range, never a percentage.** Across this
universe a 2.5 × ATR stop runs from **3.0% on BBSI to 16.8% on INET** — a 5.6×
spread. One percentage cannot serve both: it is an ordinary fortnight for one name
and a coin flip for the other. It is also what makes the rule adapt without a
setting to change — when the market turns violent the daily range grows and every
stop widens with it.

**Only the runner trails, and that is a cost decision.** Measured over 1,705
simulated entries on a 42-session horizon:

| rule | stopped out within 2 months | median sessions held |
|---|---|---|
| 2.5 × ATR fixed at entry | 44% | 27 |
| 2.5 × ATR trailing from entry | 74% | 16 |
| 2.0 × ATR trailing from entry | 82% | 13 |

At Rp10 juta each firing costs about Rp22,000 once the stamp, the sell fee and the
buy fee to get back in are counted — near Rp700,000 a year across four slots, 7% of
the account. So the stop is fixed until the first trim banks a gain; after that the
stop moves to break-even (entry **plus** the round trip, not entry) and the
remainder trails.

**Some positions cannot be staged, and the page says so.** A trim has to clear its
own cost, and the Rp10,000 stamp puts the smallest viable one at about Rp452,000.
A position too small to slice comes back as *"this is one decision, not a ladder"*
rather than instructions you cannot follow.

**A stop beats the ranking, then a cooldown blocks the re-buy.** Without the
cooldown the loop closes on itself — the stop sells today, tomorrow's re-rank puts
the name straight back in the target book, and you pay both sides to end up exactly
where you started. It blocks any *increase*, not just a re-entry, so the ladder
cannot undo itself by topping a trimmed position back up.

### What the ladder measured, and how much the answer depends on how often you re-rank

From a random entry, +1R arrived 38.6% of the time and the stop first 37.4% — near a
coin flip, which is what a random walk implies. The ladder narrows the spread of
outcomes; it does not create return. What `--backtest` measures is whether that
narrowing is worth paying for, and **the answer differs by cadence**:

| **Weekly** re-rank | CAGR | Max drawdown | Sharpe | Fees | Selling days |
|---|---|---|---|---|---|
| Hold to the rebalance | **+34.5%** | −31.6% | 1.16 | Rp4,480,878 | 167 |
| Stop only, 2.5 × ATR | +33.8% | −19.5% | 1.30 | Rp3,924,705 | 171 |
| **Stop + ladder (shipped)** | +31.1% | **−10.7%** | **1.61** | Rp4,612,458 | 255 |

| **Monthly** re-rank | CAGR | Max drawdown | Sharpe | Fees | Selling days |
|---|---|---|---|---|---|
| Hold to the rebalance | +31.7% | −30.7% | 0.98 | Rp1,532,495 | 41 |
| **Stop only, 2.5 × ATR** | **+37.9%** | −25.8% | **1.09** | Rp1,749,080 | 51 |
| Stop + ladder (shipped) | +21.3% | −16.7% | 0.80 | Rp2,069,066 | 112 |

**On a weekly re-rank the ladder is the best risk-adjusted rule on the page.** It
gives up 3.4 points of return and buys a drawdown a third the size — −10.7% against
−31.6% — lifting Sharpe from 1.16 to 1.61. At 1–2 trades a week, that is the row
that describes you.

**On a monthly re-rank it is not.** There a stop with no profit-taking wins on both
return and Sharpe, and the ladder costs 10 points.

> **This conclusion reversed once the universe grew.** On the old 49-name list the
> ladder lost at both cadences and this section said so. With 74 names across 11
> properly scored sectors there are more independent trends, so trimming one winner
> no longer removes the whole edge while the drawdown protection still compounds.
> The ranking itself moved the same way: it went from **costing** 0.6pp a year
> against an equal-weight universe to **adding 9.3pp**. Re-run `--backtest` after any
> universe change; these numbers are not constants.

**One line turns the ladder off** if you prefer the monthly-style result. In
`configs/user.yaml`:

```yaml
risk:
  ladder: []            # stop only: no profit-taking, no trailing
  ladder_fractions: []
```

Caveats that cut against all of it: the backtest reconstructs only 3.0 of the 9.0
factor weight, on a survivorship-biased universe, over one regime — and it rebalances
on a fixed calendar, which is not how anyone actually trades. It disqualifies; it
does not validate.

**It cannot watch these levels for you.** The tool reads daily closes and has no
live feed, so a level is checked once per session against the close — never
intraday. Place the stop in Indopremier if you want it to act while you are not
looking. Triggering on the close is also the less churn-prone choice: 2.0 × ATR
survived 13 sessions on closes against 10 on intraday lows.

Everything above is in `configs/default.yaml` under `risk:`, with the measured basis
in the comments.

---

## How performance is measured

`python main.py --journal` answers one question: **would the same money, moved on the
same days, have done better in the index?**

- **Cash-flow-matched IHSG shadow.** Every rupiah you put into a stock on a given day
  buys the same rupiah of `^JKSE` on that day. A plain percentage return is misleading
  when money goes in and out at irregular times. Comparison is on total wealth, since
  both sides hold identical cash.
- **FIFO cost basis**, matching Indonesian broker statements, so the numbers reconcile
  against Indopremier.
- **Every figure is net of fees.** A Rp45,000 gross gain on a Rp1.2 juta position is
  Rp28,893 after Indopremier's cut.
- **It refuses to declare a winner too early.** Below 30 closed round-trips the report
  says `not enough data yet — 12 of 30` rather than showing a confident number built on
  noise. At 4–8 trades a month that is roughly six months of history.

---

## Backtest

```bash
python main.py --backtest      # writes data/output/backtest.html
```

**What it can and cannot tell you.** Only **3.0 of the 9.0 factor weight** is
reconstructible from history — momentum and realised volatility. The six fundamental
factors come from Yahoo as a *current snapshot*, so ranking 2023 stocks by today's P/E
would be look-ahead. The universe is also today's survivors, and this is one market
regime.

So a good result means *the price component was not obviously broken on one flattered
window*. It does not mean the tool works. **A backtest disqualifies; it does not
validate.**

It answers four questions separately:

1. **Did the price factors beat the alternatives?** Against IHSG *and* against an
   equal-weight universe — the second separates "the ranking added something" from
   "IDX stocks went up".
2. **What does being small cost?** Fees and stamp are a real drag. Whole-lot rounding
   is **not** — measured across 14 start months its effect was positive in 7 and
   negative in 7, with a standard deviation of ~157pp. At Rp10 juta rounding is *path
   noise*, not a tax, and the report labels it that way. Its deterministic part (budget
   left in cash) is ~0.2%.
3. **Does the risk-off ladder help?** Compared on CAGR *and* drawdown, since trading
   return for a smaller drawdown is a legitimate choice.
4. **Do the stops and the profit ladder help?** Holding to the next rebalance against
   stopping out, with and without staged trims, at three stop widths — reported
   beside **fees paid** and **selling days**, because selling more often costs more
   and that has to clear before anything was gained. See
   [When to get out](#when-to-get-out).

Then it stresses the result: momentum weights ±50%, position count 3–6, and each half
of the window independently. An edge that appears at only one setting is not an edge.

The backtest calls the same `choose_allocation`, `estimate_fees`, `assess_regime`,
`stop_level` and `build_ladder` the daily brief uses, so it tests the strategy you
actually run — including the cooldown that stops a rebalance buying back what an
exit just sold. Exits are off in questions 1–3, so those keep answering exactly what
they answered before.

---

## Where the numbers come from, and where they went wrong

A methodology audit ran over the whole calculation chain — every factor's units,
sign and bounds, the scoring, valuation, liquidity and sizing arithmetic, and the
stability of the ranking itself. What it found is worth keeping visible.

### The bug: a weight-1.0 factor was wrong for most of the universe

`dividend_yield` was read from yfinance's `dividendYield`, which is a **percentage**.
The code divided by 100 only when the value exceeded 1.0 — so any genuine yield
*below 1%* skipped the rescale and was then read as a fraction:

```
        scored as   actually paid
BREN      12.00%       Rp0          <- 3rd best in the universe on this factor
WIFI      10.00%       Rp2 on a Rp2,090 share
BRPT       9.00%       Rp0
PANI       8.00%       Rp0
CUAN       4.00%       Rp0
INET       1.00%       Rp0
```

No threshold could fix it: `0.5` may mean 0.5% or 50%. The obvious replacement,
`trailingAnnualDividendYield`, turned out to be the **last single payment** over the
price — 6.13% for BBRI, which paid Rp137 and Rp209 on a Rp3,390 share, and **0.0 for
PGAS three months after paying Rp125.6**.

So the yield is now computed from the dividend payments themselves, which ride along
with the price history at no extra request. A list of dates and rupiah amounts cannot
be misread, and every name now has a real figure — **74 of 74, where 23 used to be
imputed**.

### The fix that nearly did not reach you

Fixing the maths was not enough. The app opens from a **saved screen** so a launch
costs two seconds instead of a minute — and that screen holds the scores as they
were computed, not the inputs. The dividend fix changed what
`undervaluation_score` *means* without changing a single field name, so the file
stayed perfectly loadable and the rebuilt exe went on showing BREN at a 12% yield
on a stock that pays Rp0. Only pressing **Update data** would have cleared it, and
nothing on the page suggested it needed clearing.

A saved screen is now discarded when the code that produced it has been replaced —
compared against the executable's own timestamp in a build, or the newest file
under `src/` from source, which is the rule `verify_bridge.py` already used to
refuse a stale brief. That is automatic. The version stamp beside it is the
declaration, and it is bumped whenever the scoring changes meaning.

### What the audit checked and found correct

Worth recording, because an audit that only reports faults tells you nothing about
what it looked at:

* **Every factor's sign.** `corr(raw value, z)` carries the intended direction for
  all ten. Nothing is inverted.
* **Rank stability.** Jackknife across the universe: top-8 membership survived
  **every** drop, average rank move 0.35 places. When the picks change it is because
  the inputs changed, not because the ranking is noisy.
* **Imputation is not a back door.** Correlation between a name's number of missing
  factors and its rank: **0.126**.
* **The USD/IDR repair.** `price / (bookValue × fx)` is right and correctly targeted:
  ADRO's `trailingEps` is already 357.14 **IDR** while its `bookValue` is 0.16
  **USD**, so only book value needs converting — verified directly rather than
  assumed.
* **The liquidity gate.** The nominal-slot approximation cannot bind at Rp10 juta:
  the Rp250 juta floor dominates the 1%-of-volume rule for every position this
  account can hold. It would bind at larger capital.
* **Valuation** reads unclipped multiples, so a winsorized number can never become a
  fair-value estimate — and that snapshot now happens *before* the sanity bounds, so
  clipping a real extreme cannot quietly halve its book value either.

---

## Ranking is not valuation

These are two different questions and the tool answers them separately.

**The rank score does not say anything is cheap.** `undervaluation_score` is min-max
normalised across today's universe:

```python
df["undervaluation_score"] = (raw - raw.min()) / (raw.max() - raw.min())
```

So exactly one stock scores 1.00 and one scores 0.00 on every run, whatever the market
is doing. In a bubble the top name still reads 1.00. It answers *"which of these 74"*,
which is the question the ticket needs — but it is a ranking wearing a valuation's name,
and the brief labels it **Rank score** for that reason.

**Fair value is computed separately**, from two independent peer multiples:

```
EPS  = price / P/E      fair price = EPS  × peer median P/E
BVPS = price / P/B      fair price = BVPS × peer median P/B
```

The fair zone is **the gap between those two estimates**. When they agree the zone is
tight and the verdict is confident; when they fight it is wide and says so. There is no
"within ±15% is fair" rule, because a fixed band would claim equal confidence for a name
whose measures are 3% apart and one where they are 177% apart — both occur in the real
universe. Peer group is the sector when it holds at least four names, otherwise the whole
universe, and the brief always says which was used.

Every name lands in one of five states, and none of them is silence:

| State | Shown as |
|---|---|
| Both measures | `below peers · Rp6,560 – Rp12,999 · 71% below` |
| One measure | `one measure · ~Rp631, no range` |
| Measures disagree | the zone, plus `measures disagree` |
| No usable multiple | `cannot value — no usable P/E and no usable P/B` |
| No price | `cannot value` |

On the current run: 64 of 74 valued on both measures, 10 on one, none refused — and 19 of
those 64 carry the disagreement flag.

**Two things it cannot see.** Everything is relative to other IDX names, so if the whole
market is expensive everything still reads "in line". And it takes no view on whether a
premium is *deserved* — a company earning 60% on equity should trade above its peers, and
this method will call it overvalued. ROE sits next to every verdict so you can apply that
judgement yourself.

> **The input subtlety that matters.** Valuation reads the *pre-winsorization* multiples.
> Ranking uses clipped ones deliberately — that is what stops one bad P/B flattening a
> whole factor — but clipping collapses every outlier onto the same bound, and on a real
> run six unrelated tickers came back with `pe_ratio == 50.738811` to six decimals.
> Deriving earnings from that would have invented them, for exactly the extreme names
> where the question matters most.

**No DCF, deliberately.** It needs cash-flow forecasts, a growth rate and a discount rate;
yfinance supplies none of them, and a 1% change in the discount rate would move the answer
more than everything else the tool measures. Cheapness against a stock's *own* 5-year
average multiple would be better than peer-relative, and is blocked for the same reason
the fundamentals cannot be backtested: yfinance gives a current snapshot only.

---

## The universe and how it was chosen

74 IDX tickers, in `configs/default.yaml`. Nothing outside the list can ever be
suggested however good it is, so **how the list was drawn matters as much as what
is on it.**

### The rule, and why it is blind to performance

`--backtest` reports that simply holding all 74 equally returned **+29.8% a year
against the index's +1.7%** — a 28-point gap from the ticker list alone, with no
ranking, no sizing and no timing. That gap is an artifact: the list was drawn in
2026, knowing which companies still exist. It is the largest single effect in the
whole simulation and none of it is skill.

Which means **any name added because it "looks good" is a name that looks good
because it already went up.** Curating on merit would inflate that artifact and
then call it a strategy. So every criterion is one you could have applied without
knowing what the price did:

| Criterion | Threshold | What it is really asking |
|---|---|---|
| Median daily traded value | ≥ Rp250 juta | Can you get back out? |
| One lot | ≤ Rp1,000,000 | Fits a slot at Rp10 juta in a risk-off regime |
| Price history | ≥ 273 sessions | 252 + 21, the minimum for 12-month momentum |
| Measurable ATR | not null | No ATR means no stop can be set for it |
| Sector floor | ≥ 5 per sector | Makes `min_sector_size` reachable everywhere |
| Tie-break | most traded first | Liquidity rank, never return rank |

Not used: past return, momentum, market cap, or an opinion of the business. Those
are what the screener exists to discover.

### What changed, and the check that it was honest

Five names were removed for failing the liquidity gate on **every** run since it
existed — they could never have been recommended:

```
WIKA  Rp0/day traded, 98% flat sessions, and no measurable ATR at all
ADHI  Rp0/day traded, 62% flat sessions
BBSI  Rp2.3 juta/day   against the Rp250 juta floor   (100x short)
BTPN  Rp56 juta/day                                   (4.5x short)
BNLI  Rp116 juta/day                                  (2.2x short)
```

Thirty were added, filling two sectors that were entirely absent — **Properties**
and **Transportation** — and lifting four more above the scoring floor. Before the
change, six of eleven IDX-IC sectors held fewer than `min_sector_size: 4` names,
so `roe`, `gross_margin` and `debt_to_equity` were silently scored against the
whole universe instead of against peers: a hospital's return on equity compared to
a coal miner's.

**The honesty check.** If the selection had quietly favoured winners, the
survivorship gap would have widened. It did not:

| | 49 names | 74 names |
|---|---|---|
| Equal-weight universe, CAGR | +29.7% | +29.8% |
| Gap over the index | +28.0pp | +28.1pp |
| **Share of names beating the index** | **78%** | **73%** |
| Median name's total return | +96.8% | +95.4% |

The gap is unchanged within noise and the share of index-beaters *fell*. That is
what an outcome-blind rule looks like from the outside. Re-run `--backtest` and
compare these two lines after any change to the list.

Two side effects worth knowing:

* **The ranking started working.** Against an equal-weight universe it went from
  costing 0.6pp a year to **adding 9.3pp**. A ten-factor z-score needs a cross
  section; 49 names with six broken sectors was not one.
* **A full Update takes about a minute** instead of forty seconds, dominated by
  fundamentals (0.61s a ticker against 0.07s for prices). Launches are unaffected —
  they read the saved screen.

### What is still on the list despite failing something

* **GOTO** — pinned at the Rp50 minimum tradable price for 100% of the last 42
  sessions, so it has no measurable ATR and the exit engine cannot give it a stop.
  BEI's move of that floor to Rp1 was targeted **7 September 2026**; removing GOTO
  days before the rule that unpins it would be the wrong moment. Worth checking
  against idx.co.id.
* **ITMG, UNTR** — one lot costs Rp2.6 and Rp2.5 juta, more than a risk-off slot at
  this account size, so the sizer refuses them and says why. They become reachable
  at full deploy.
* **EMAS** — 231 sessions of history, so it scores neutral on 12-month momentum
  until it has more. A recent listing, not a defect.

---

## How the score works

Each factor is turned into a cross-sectional z-score, multiplied by a signed weight,
and summed. The sign is the direction — negative means lower is better.

| Group | Factors |
|---|---|
| Value | `pe_ratio` (−), `price_to_book` (−) |
| Income | `dividend_yield` (+) |
| Quality *(scored within sector)* | `roe` (+), `gross_margin` (+), `debt_to_equity` (−) |
| Risk | `realized_vol` (−) |
| Momentum | `mom_1m` (+), `mom_6m` (+), `mom_12m` (+) |

Five deliberate choices:

- **A missing factor scores neutral, never removes the stock.** It is listed in
  `imputed_factors` and flagged in the brief.
- **Quality is judged within sector.** A bank's leverage is compared to other banks.
- **`beta` is not used.** yfinance reports 0.016 for Bank Mandiri and negative values
  for several IDX large caps. `realized_vol`, computed from the price panel, replaces it.
- **`dividend_yield` is computed from the dividends actually paid**, not read from a
  summary field. Both of yfinance's are wrong here: `dividendYield` is a *forward*
  estimate on a percent scale, and `trailingAnnualDividendYield` turns out to be the
  *last single payment* over the price. Summing the payments in the trailing year is
  the only version that agrees with what lands in your account — and it is the same
  basis your dividend ledger records. See [Where the numbers come
  from](#where-the-numbers-come-from-and-where-they-went-wrong).
- **An extreme multiple is clipped, not discarded.** A nulled factor scores
  *neutral*, so discarding a P/E of 1,667 handed the most expensive name in the
  universe a free pass on a factor weighted −1.0. It is clipped to the bound and
  scores worst-in-class. Only a value beyond ten times the bound — the currency
  glitch this machinery was built for read 179,615 — is treated as broken and
  dropped.

**The score's own precision is measured, and the ticket says so.** Every factor is a
z-score against the rest of the list, so a score belongs to a company *and its
peers*: drop one unrelated name and everybody shifts. A jackknife over the universe
puts that shift at about **0.10**, against a score spread of 3.75 — while picks #3 to
#8 sit **0.02 to 0.21** apart. Names closer together than the measured floor are
reported as **tied**, and among ties the one that least duplicates what you already
hold goes first. Without that the ticket buys the name that scored 0.02 higher and
presents it as a decision.

Machine learning is **off** (`use_ml: false`). The ranker labelled its training data
using the very score it then overwrote — circular by construction. See
[docs/AUDIT.md](docs/AUDIT.md).

---

## Why lots and fees are in the ranking loop

Ranking alone produces orders you cannot fill. Three constraints bind before any
factor does at this account size:

- **Lots.** IDX trades in 100 shares. One lot of UNTR is Rp2,292,500 — it ranked #4
  in an earlier run of this very tool, against a Rp2 juta slot. The sizer now chooses
  both the position count and the lot counts, and reports the weight error that lot
  rounding forces.
- **Liquidity.** WIKA's median daily traded value was **Rp 0**. A position you cannot
  exit is not a position. Names are rejected with their actual number shown.
- **Fees.** The Rp10,000 stamp is charged per *day* with a sell, not per order.
  Batching four sells into one day saves Rp30,000/month at this cadence. The brief
  says so, in rupiah, whenever a ticket has more than one sell.

---

## Limitations

1. **Survivorship bias.** The universe is today's tickers. Companies that already
   failed are absent, so any implied historical performance is flattered.
2. **Fundamentals are a snapshot, not point-in-time.** yfinance gives current values
   only, so the fundamental score cannot be honestly backtested.
3. **Data gaps are common.** About two-thirds of the universe is missing at least one
   factor on a given run. Those are scored neutral and marked ⚠ — check
   `imputed_factors` before trading a name.
4. **Prices can be stale or wrong.** Always confirm the live price in your broker
   before sending an order.
5. **The regime signal is two moving averages.** Deliberately simple enough to verify
   on a chart. It is not a forecast.
6. **Fees are an estimate** based on Indopremier's published schedule.
7. **Seasonality is weak evidence.** Even on full history that is ~37 observations per
   calendar month. The line always shows `n` and says so. Context, not a rule.
8. **Event coverage is partial.** Two-thirds of the universe has no automatic earnings
   date. The brief marks those explicitly rather than implying they are clear.
9. **Fair value is peer-relative, not absolute.** It cannot see the whole market being
   expensive, and it treats a deserved quality premium as overvaluation. See
   [Ranking is not valuation](#ranking-is-not-valuation).
10. **`gross_margin` is not reported for banks.** yfinance returns a literal `0.0`, which
    the scorer used to read as a real observation — every bank scoring worst-in-class on
    a factor that does not apply to them. It is now treated as missing and scored neutral.
11. **A stop is checked once a session, against the close.** There is no live feed, so
    the tool cannot see an intraday print and cannot act while you are away. It also
    cannot model a gap through a level: on IDX a limit-down open fills below the stop,
    not at it, so the rupiah-at-risk figure is a floor rather than a worst case.
12. **A capped stop understates its own risk.** Past `max_stop_pct` the level is the
    cap rather than 2.5 × ATR, which makes the wildest names look like the safest.
    Those rows are flagged `capped` wherever the risk is shown.
13. **The dividend yield is trailing, not forward.** It is what the last twelve
    months actually paid, so a company that has just initiated or just cut its
    dividend is scored on history rather than on intent. The forward figure is
    carried alongside and a material disagreement is flagged.
14. **Picks within the measured score precision are ties, not a ranking.** The
    ticket says which, and breaks them on diversification rather than on a
    difference the score cannot support.
15. **Whether the ladder pays for itself depends on how often you re-rank**, and it
    reversed once the universe grew from 49 names to 74. Weekly it is the best
    risk-adjusted rule measured; monthly a stop with no profit-taking beats it. One
    config line turns it off. See [When to get out](#when-to-get-out).
16. **The universe is 74 names chosen on structure, not on merit.** Liquidity, lot
    price, history length and sector coverage — never past return. That keeps the
    selection from inflating the survivorship artifact, but it also means no
    judgement was applied about which businesses are good. See
    [The universe](#the-universe-and-how-it-was-chosen).

---

## Development

```bash
pip install -r requirements-dev.txt
pytest                      # 578 tests, no network required
```

```
src/
├── core/          config + logging
├── fetchers/      yfinance access, windowed cache, currency repair
├── analysis/      indicators, factor scoring, valuation, the decision trail
├── market/        regime, liquidity gate, events, seasonality
├── portfolio/     fees, sizing, holdings, journal, ledger, performance
├── report/        the terminal shell, panels, inline SVG charts
├── backtest/      historical simulation under real frictions
├── viz/           the optional matplotlib PNG (--png)
├── cli.py         --log / --mark / --journal handlers
├── core/paths.py  where files live, from source or from the .exe
├── api.py         the bridge the page calls to record a trade
├── runner.py      full_run (fetches) and render (does not)
├── first_run.py   asks for capital before the first fetch
├── desktop.py     native window, with a browser fallback
└── pipeline.py    shared orchestration
```

**Why there is no web framework here.** The screener produces one answer per day, so
there is nothing to stream and nothing to poll; a reactive server's whole value would
sit idle. Startup latency is the binding constraint — the decision has to happen in a
lunch break, and `streamlit run` plus a port plus a terminal you must not close spends
that budget on ceremony. The one genuinely interactive question, *"what if I sized it
differently?"*, is answered by precomputing the whole surface with the real sizer and
embedding it, because `choose_allocation` is pure and cheap. Keeping the page a pure
function that returns a string is also what lets all 578 tests run offline.

[docs/AUDIT.md](docs/AUDIT.md) records what was broken in the original build, with
the measurements that showed it.

MIT licensed.
