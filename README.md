# IDX Quant Screener

A personal stock screener for the **Indonesia Stock Exchange**, built for one
investor with a small account and a lunch break.

It ranks ~49 IDX names on a transparent multi-factor score, then does the part most
screeners skip: it works out **what you can actually buy** — whole 100-share lots,
inside your capital, in stocks liquid enough to sell again — and costs it against
your real broker fees. The output is a single HTML page you read in about ten
minutes.

> **Not investment advice.** A personal research tool. See [Limitations](#limitations).

---

## What it produces

`data/output/brief.html` — one self-contained page:

| Section | What it answers |
|---|---|
| Market right now | Risk-on or risk-off, and what share of capital to deploy today |
| **Do this today** | The ticket: BUY/SELL/HOLD, exact lots, exact rupiah, estimated fees |
| What you hold | Current positions, P&L, and a health flag per name |
| Best candidates you can afford | Ranked, with a plain-English reason for each |
| Skipped | Names that ranked well but failed the liquidity or lot-size gate |
| Events next 14 days | Earnings, ex-dividend, index reviews — labelled auto / you / est. |
| How you're doing | Realised P&L net of fees, and your total versus the same money in IHSG |

Also written: `screener_results.csv` (full ranked universe with every `z_*`),
`top_picks.csv`, `ticket.csv`, `factor_correlations.csv`, `screener_analysis.png`.

---

## Quick start

```bash
pip install -r requirements.txt
python main.py
```

The brief opens in your browser automatically. First run fetches ~2 years of prices
for 49 tickers and takes a minute or two; afterwards the cache makes it fast.

Set your capital and broker in `configs/default.yaml`:

```yaml
account:
  capital_rp: 100000000
  min_position_rp: 1000000    # below this, the Rp10k stamp dominates the trade

broker:
  buy_fee: 0.0019             # Indopremier
  sell_fee: 0.0029
  stamp_duty_rp: 10000        # per DAY that has a sell, not per order
  lot_size: 100
```

---

## Logging trades

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

Yahoo Finance has an earnings date for only **16 of the 49** names — the gaps include
SRTG, TINS, TAPG, MAPI, ISAT, ITMG and KLBF. So `configs/events.yaml` is the primary
source and yfinance fills in a third of it.

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

Three deliberate choices:

- **A missing factor scores neutral, never removes the stock.** It is listed in
  `imputed_factors` and flagged in the brief.
- **Quality is judged within sector.** A bank's leverage is compared to other banks.
- **`beta` is not used.** yfinance reports 0.016 for Bank Mandiri and negative values
  for several IDX large caps. `realized_vol`, computed from the price panel, replaces it.

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

---

## Development

```bash
pip install -r requirements-dev.txt
pytest                      # 226 tests, no network required
```

```
src/
├── core/          config + logging
├── fetchers/      yfinance access, windowed cache, currency repair
├── analysis/      technical indicators, factor scoring, sector cap
├── market/        regime, liquidity gate, events, seasonality
├── portfolio/     fees, lot-aware sizing, holdings, journal, performance
├── report/        plain-English reasons, the HTML brief, performance view
├── viz/           diagnostic PNG
├── cli.py         --log / --mark / --journal handlers
└── pipeline.py    shared orchestration
```

[docs/AUDIT.md](docs/AUDIT.md) records what was broken in the original build, with
the measurements that showed it.

MIT licensed.
