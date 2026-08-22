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

Record what you own in `current_holdings.yaml` so the brief can diff against it:

```yaml
holdings:
  BBRI.JK: {lots: 3, avg_price: 4100}
```

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

---

## Development

```bash
pip install -r requirements-dev.txt
pytest                      # 126 tests, no network required
```

```
src/
├── core/          config + logging
├── fetchers/      yfinance access, windowed cache, currency repair
├── analysis/      technical indicators, factor scoring, sector cap
├── market/        regime, liquidity gate
├── portfolio/     fees, lot-aware sizing, holdings
├── report/        plain-English reasons, the HTML brief
├── viz/           diagnostic PNG
└── pipeline.py    shared orchestration
```

[docs/AUDIT.md](docs/AUDIT.md) records what was broken in the original build, with
the measurements that showed it.

MIT licensed.
