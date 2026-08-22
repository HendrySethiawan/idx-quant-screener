# Audit — what was broken and what changed

Findings verified against this repository and its own committed output
(`data/output/screener_results.csv`, `summary.txt`) before any code changed.

---

## P0 — defects that corrupted the ranking

### 1. Silent NaN wipeout (the worst one)

```python
col_data = df[col].dropna()
z = (col_data - col_data.mean()) / col_data.std()
df["undervaluation_score"] += weights.get(col, 0) * z   # <-- index-aligns
```

`z` carries a *shorter* index than `df`. Pandas aligns on index during `+=`, so every
row absent from `z` became `NaN` — and one missing factor was enough to erase a
stock's entire score. `nlargest` then dropped it silently.

**Measured:** 9 of 41 stocks scored `NaN`, exactly the 9 missing `dividend_yield`.
22% of the universe could never be recommended, with no error and no warning.

**Fix:** each factor's contribution is now `(weight * z).reindex(df.index).fillna(0.0)`.
A missing factor contributes a neutral 0 and is recorded in `imputed_factors`.
Guarded by `tests/test_fundamental.py::test_missing_factor_does_not_erase_row`.

### 2. Thirty days of price history

`_fetch_single` requested `period=f"{data_retention_days}d"` — a *retention* setting
used as a *lookback*. Cache showed min 25 / median 29 / max 30 bars, so `MA_20` had
~10 valid points and momentum was not computable at all.

**Fix:** separate `history_period` (default `2y`). Runs now fetch ~479 bars.

### 3. Cache key ignored the window

Key was `f"{ticker}.pkl"`, so widening the lookback would have kept serving the stale
30-bar frame forever. **Fix:** `f"{ticker}__{period}.pkl"`.

### 4. No outlier control killed the P/B factor

Raw `price_to_book` held PTRO 179,615 · BRPT 77,600 · RAJA 73,333 · BRMS 66,111.
A single value that large inflates the standard deviation until every *other* stock's
P/B z-score collapses toward zero.

**Fix:** two-sided sanity bounds nullify impossible values, then survivors are
winsorized to `median ± 5·(1.4826·MAD)`. Median/MAD rather than mean/std because on a
49-name universe one glitch drags a mean-based band out of usefulness.

### 5. `beta` from yfinance is unusable for IDX

Reported mean 0.28, median 0.21, min −0.71 — Bank Mandiri came back at **0.016**.
Real IDX large-cap betas cluster near 1.0. It carried a `-1.0` weight, so the
"low-volatility" factor was noise.

**Fix:** dropped from the composite. Replaced with `realized_vol`, a 60-day
annualised standard deviation computed from our own price panel.

### 6. Currency mismatch destroyed P/B for a third of the universe

Found while checking why 17 names lost their P/B after the sanity bound was added.

16 of 49 IDX names report financials in **USD** while their shares trade in **IDR**.
yfinance divides the IDR price by the USD book value per share:

| ticker | price (IDR) | bookValue (USD) | reported priceToBook | repaired |
|---|---|---|---|---|
| ADRO.JK | 2,550 | 0.17 | 15,000 | **0.85** |
| PTRO.JK | 5,300 | 0.026 | 203,846 | **9.27** |
| INCO.JK | 5,200 | 0.269 | 19,331 | **1.09** |

`trailingEps` **is** already converted to IDR, so `trailingPE` needed no repair —
only `bookValue` is left in the reporting currency. Recomputing
`price / (bookValue × USDIDR)` took P/B coverage from **32/49 to 48/49**.

Handled in `fetchers.data_fetcher.repair_price_to_book`, tested in
`tests/test_currency_repair.py`.

### 7. Row deletion instead of value nulling

`df = df[df["pe_ratio"] > 0]` also drops `NaN` rows, because `NaN > 0` is `False`.
Loss-making companies and data gaps disappeared rather than ranking poorly —
universe 49 → 41. **Fix:** invalid values are nulled and noted; the row survives.

### 8. The ML ranker was circular and switched on

`train()` built its label as `y = (score > median of that same score)`, fitted a
logistic regression, then **overwrote** `undervaluation_score` with its own predicted
probability. `TimeSeriesSplit` was applied to a cross-section of stocks, which is not
a time series. The tell was in the committed output: `Avg Score: 0.5000059`, the
signature of a balanced-label classifier rather than a value score.

**Fix:** `use_ml: false`. The module stays on disk, gated and documented.

---

## P1 — hygiene

| Finding | Fix |
|---|---|
| `pytest` collected **0 tests, 1 error**; the only test called a nonexistent `fetch_all()` | `pytest.ini` with `pythonpath=src`; suite rebuilt |
| `sectors:` never defined in `default.yaml` despite `Settings` declaring it → every sector was `Unknown` | full 49-name sector map added |
| README claimed `status: production-ready` with a fabricated sample output | rewritten honestly |
| Not a git repository | left to the user (`git init`) |

---

## P2 — the gap neither this build nor the reference build addressed

At Rp 10 juta the binding constraints are not statistical.

| Constraint | Evidence | Handled by |
|---|---|---|
| **IDX trades in 100-share lots.** UNTR 1 lot = Rp2,292,500. It ranked **#4** in this repo's own `top_picks.csv` but does not fit a Rp2 juta slot. | computed from cache | `portfolio/sizing.py` |
| **Weight granularity.** BBCA gives 3 lots per slot; "equal weight" carries ±33% error. | computed | `Allocation.max_weight_error`, shown in the brief |
| **Liquidity.** WIKA median daily traded value **Rp 0**; BBSI ≈ Rp3.7 juta/day; BNLI ≈ Rp142 juta/day — all in the ranked universe. | computed | `market/liquidity.py` |
| **Fees.** Indopremier charges 0.19%/0.29% plus Rp10,000 stamp **per day containing a sell**, not per order. | broker schedule | `portfolio/fees.py` |
| **Position floor.** At 30% deployment the sizer first produced 6 positions of ~Rp500,000; the Rp10,000 stamp alone is 2% of such a position. | observed during build | `account.min_position_rp` |

### The stamp-batching result

```
8 actions/month (4 buy + 4 sell, Rp2.5 juta each) on Rp10 juta:
   sells spread across 4 days →  Rp88,000/mo = 0.88%/mo = 11.1%/yr
   sells batched into ONE day →  Rp58,000/mo = 0.58%/mo =  7.2%/yr
   ── batching saves Rp30,000/month ──
```

The brief prints this saving in rupiah whenever a ticket contains more than one sell.

---

## Verification

```bash
.venv/Scripts/python.exe -m pytest              # 126 passed
.venv/Scripts/python.exe main.py                # 49 rows, 0 NaN, brief.html written
```

Before → after on the same universe:

| | before | after |
|---|---|---|
| stocks ranked | 32 of 49 | **49 of 49** |
| `NaN` scores | 9 | **0** |
| price history | ~30 bars | **~479 bars** |
| P/B coverage | 32/49 | **48/49** |
| factors | 4 (one of them noise) | **10** |
| tests | 0 collected, 1 error | **126 passed** |
| orders | rupiah only, unfillable | **whole lots, costed, liquidity-checked** |
