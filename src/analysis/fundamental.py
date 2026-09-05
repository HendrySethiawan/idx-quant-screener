# src/analysis/fundamental.py --revision 2
"""
Multi-factor cross-sectional scoring.

Revision 2 fixes three defects that silently corrupted every ranking (docs/AUDIT.md):

  1. NaN wipeout. The old accumulator did `df["score"] += weight * z` where `z` was
     built from `df[col].dropna()`. Pandas aligns on index, so a stock missing ONE
     factor had its ENTIRE score turned to NaN and was then dropped by nlargest.
     Measured on real output: 9 of 41 stocks vanished. A missing factor now
     contributes a neutral 0 and is recorded in `imputed_factors`.

  2. No outlier control. Raw price_to_book held values of 179,615 (PTRO) and 77,600
     (BRPT). Those inflate the standard deviation until every other stock's P/B
     z-score collapses toward zero, killing the factor. Values are now nullified
     against two-sided sanity bounds, then winsorized on median/MAD.

  3. Row deletion. `df = df[df["pe_ratio"] > 0]` also drops NaN rows, because
     `NaN > 0` is False. Loss-making companies disappeared instead of ranking
     poorly. Invalid values are now nulled and noted; the row survives.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Union

from pydantic import BaseModel

from core.logger import setup_logger

logger = setup_logger(Path("logs"))

_MAD_SCALE = 1.4826  # makes MAD a consistent estimator of sigma for normal data

# Absolute gap, in fraction terms, above which the forward and trailing dividend
# yields are reported as disagreeing. 2 percentage points: BBRI's two figures are
# 6.1pp apart and UNVR's 6.9pp, while an ordinary payer's differ by well under one.
_DIVIDEND_DISAGREEMENT = 0.02

# Multiples that can be extreme and still real. Above a sanity bound a value is
# nullified, which scores it NEUTRAL -- so the most expensive names in the universe
# used to get a free pass on a factor weighted -1.0. These two are clipped to the
# bound instead, so a P/E of 1,667 scores worst-in-class rather than average.
_CLIP_NOT_NULL = ("pe_ratio", "price_to_book")

# ...but only up to a point, and the point is measured. On this universe the real
# extremes reach P/B 38.1 (BREN) and P/E 1,666.7 (MDKA) -- under ten times their
# bounds. The currency glitch this whole mechanism was built for was P/B 179,615,
# some nine thousand times over. So beyond 10x the bound a value is not an
# expensive company, it is a broken field, and clipping one to the band would drag
# the mean and compress everybody else's z-score -- which is the exact harm the
# bound exists to prevent.
_GLITCH_MULTIPLE = 10.0


class FundamentalEngine:
    def __init__(self, config: Union[dict, BaseModel]):
        if isinstance(config, BaseModel):
            config = config.model_dump() if hasattr(config, "model_dump") else config.dict()

        self.config = config
        self.metrics: List[str] = config.get("fundamental_metrics", [
            "pe_ratio", "price_to_book", "dividend_yield", "beta",
            "roe", "gross_margin", "debt_to_equity",
        ])
        self.scoring_method: str = config.get("scoring_method", "zscore_normalized")
        self.risk_adjusted: bool = config.get("risk_adjusted", False)
        self.sanity_bounds: Dict[str, float] = config.get("sanity_bounds", {})
        self.factor_weights: Dict[str, float] = config.get("factor_weights", {})
        self.sector_neutral_factors: List[str] = config.get("sector_neutral_factors", [])
        self.min_sector_size: int = int(config.get("min_sector_size", 4))
        self.winsorize_k: float = float(config.get("winsorize_k", 5.0))

    # ------------------------------------------------------------------ validate
    def validate_fundamentals(self, records: List[Dict]) -> pd.DataFrame:
        df = pd.DataFrame(records)
        missing = [c for c in ("ticker", "name") if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        df = df.reset_index(drop=True)
        # Seed from any note the fetcher already attached (e.g. the USD/IDR
        # price-to-book repair), so the audit trail survives into the output.
        if "fetch_note" in df.columns:
            notes: List[List[str]] = [[str(n)] if pd.notna(n) and str(n) else [] for n in df["fetch_note"]]
            df = df.drop(columns="fetch_note")
        else:
            notes = [[] for _ in range(len(df))]

        for col in (c for c in self.metrics if c in df.columns):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # `dividend_yield` is now yfinance's `trailingAnnualDividendYield`, which is
        # a fraction and needs no rescaling. `dividend_yield_forward` is the
        # indicated figure, which yfinance reports on a percent scale ALWAYS.
        #
        # There used to be a heuristic here -- divide by 100 when the value exceeds
        # 1.0 -- and it was wrong in the one direction that mattered. A genuine
        # yield below 1% arrives as 0.12 meaning 0.12%, skips the test, and is then
        # read as 12%. BREN pays literally nothing and was scoring third-best in
        # the universe on a factor weighted +1.0; BRPT, CUAN, INET, PANI and WIFI
        # were doing the same. No threshold can separate "0.5 meaning 0.5%" from
        # "0.5 meaning 50%", so the heuristic is gone rather than tuned: the two
        # fields are read from sources whose scale is known.
        if "dividend_yield_forward" in df.columns:
            df["dividend_yield_forward"] = pd.to_numeric(
                df["dividend_yield_forward"], errors="coerce") / 100.0

        # Where the two disagree materially, the reader is told rather than left to
        # assume the ranked number is the whole story. The forward figure is an
        # estimate of the next twelve months; the trailing one is a fact about the
        # last twelve, and after a spinoff or a cut they are different questions.
        if {"dividend_yield", "dividend_yield_forward"} <= set(df.columns):
            trailing = pd.to_numeric(df["dividend_yield"], errors="coerce")
            forward = df["dividend_yield_forward"]
            gap = (forward - trailing).abs()
            disagrees = (gap > _DIVIDEND_DISAGREEMENT).fillna(False) & trailing.notna()
            for i in df.index[disagrees]:
                notes[i].append(
                    f"dividend_yield:forward {forward[i] * 100:.1f}% vs trailing "
                    f"{trailing[i] * 100:.1f}%")

        # Structurally impossible or not-actually-reported values -> NaN (neutral),
        # never a dropped row.
        for col, rule, tag in (
            ("pe_ratio", lambda s: s <= 0, "nonpositive"),
            ("price_to_book", lambda s: s <= 0, "nonpositive"),
            # yfinance does not compute grossMargins for banks and returns a literal
            # 0.0. Read as an observation that is every bank in the universe scoring
            # worst-in-class on a factor that does not apply to banks. Exact zero
            # only: a genuine negative gross margin (selling below cost) is real and
            # must survive.
            ("gross_margin", lambda s: s == 0.0, "not_reported"),
        ):
            if col in df.columns:
                bad = rule(df[col]).fillna(False)
                if bad.any():
                    df.loc[bad, col] = np.nan
                    for i in df.index[bad]:
                        notes[i].append(f"{col}:{tag}")

        # Snapshotted BEFORE the bounds, not after. Valuation must see the real
        # multiple: BREN's P/B is 38.1, and reading the clipped 20 would put its
        # book value per share at nearly twice the truth and its fair price with
        # it. A glitch is nulled below and takes its snapshot with it, so valuation
        # still never sees 179,615 -- it sees nothing, which is the honest answer.
        for col in ("pe_ratio", "price_to_book"):
            if col in df.columns:
                df[f"unclipped_{col}"] = df[col]

        # Two-sided magnitude test catches both huge positives (P/B 179,615) and
        # huge negatives (deeply negative ROE from a one-off writedown).
        #
        # For the two valuation multiples a POSITIVE outlier is not a glitch any
        # more. That bound was written for a currency error -- an IDR price over a
        # USD book value, giving ADRO 15,000 and PTRO 203,846 -- and
        # `repair_price_to_book` now fixes that at the source. The highest real P/B
        # left in the universe is 19.7, so the bound only ever fires on genuine
        # extremes, and nullifying one scored the most expensive name in the
        # universe as perfectly average. Those are clipped instead, and land at
        # worst-in-class where they belong. A negative value is still impossible
        # and still nullified.
        for col, bound in self.sanity_bounds.items():
            if col not in df.columns:
                continue
            bound = float(bound)
            over = (df[col].abs() > bound).fillna(False)
            if not over.any():
                continue

            if col in _CLIP_NOT_NULL:
                real = (over & (df[col] > 0)
                        & (df[col] <= bound * _GLITCH_MULTIPLE)).fillna(False)
                df.loc[real, col] = bound
                for i in df.index[real]:
                    notes[i].append(f"{col}:clipped_to_bound({bound:g})")
                over = over & ~real

            if over.any():
                df.loc[over, col] = np.nan
                for i in df.index[over]:
                    notes[i].append(f"{col}:nullified(|x|>{bound:g})")
                # A nulled value is a broken field, so the snapshot goes with it.
                # Valuation must have nothing rather than a number nobody believes.
                if f"unclipped_{col}" in df.columns:
                    df.loc[over, f"unclipped_{col}"] = np.nan

        # `unclipped_*` is snapshotted above, before the bounds and therefore before
        # winsorization too.
        #
        # _winsorize clips outliers to a shared bound, which is right for ranking --
        # it is the PTRO/BRPT fix -- but it makes the stored value a property of the
        # bound rather than of the company. On a real run six unrelated tickers came
        # back with pe_ratio == 50.738811 to six decimals, and four shared
        # price_to_book == 9.266952. Deriving earnings or book value from a clipped
        # multiple would invent them, and it would do so precisely for the extreme
        # names where "is this expensive?" is the whole question.
        df = self._winsorize(df, [c for c in self.metrics if c in df.columns], notes)

        df["data_quality_notes"] = [";".join(n) for n in notes]
        df["data_quality_flag"] = [len(n) > 0 for n in notes]
        logger.info(f"Validated {len(df)} fundamental records (0 dropped by design)")
        return df

    def _winsorize(self, df: pd.DataFrame, cols: List[str], notes: List[List[str]]) -> pd.DataFrame:
        """
        Cap each column to median +/- k * (1.4826 * MAD).

        Median/MAD rather than mean/std or quantiles: on a ~49-name universe a single
        glitch drags a mean-based band far enough to be useless, and percentile bands
        need more observations than we have.
        """
        for col in cols:
            valid = df[col].dropna()
            if len(valid) < 5:
                continue

            median = valid.median()
            mad = (valid - median).abs().median()
            spread = _MAD_SCALE * mad
            if not np.isfinite(spread) or spread <= 0:
                lo, hi = valid.quantile(0.01), valid.quantile(0.99)
            else:
                lo, hi = median - self.winsorize_k * spread, median + self.winsorize_k * spread
            if not np.isfinite(lo) or not np.isfinite(hi) or lo >= hi:
                continue

            outside = ((df[col] < lo) | (df[col] > hi)).fillna(False)
            if outside.any():
                df[col] = df[col].clip(lower=lo, upper=hi)
                for i in df.index[outside]:
                    notes[i].append(f"{col}:winsorized")
        return df

    # -------------------------------------------------------------------- score
    def compute_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        scoring_cols = [c for c in self.factor_weights if c in df.columns]

        # Price-side factors (momentum, realised vol) arrive from the merge with the
        # technical frame and have not been through validate_fundamentals().
        late_cols = [c for c in scoring_cols if c not in self.metrics]
        if late_cols:
            notes: List[List[str]] = [[] for _ in range(len(df))]
            df = df.reset_index(drop=True)
            df = self._winsorize(df, late_cols, notes)
            # Merged back rather than dropped. These notes used to be collected and
            # then discarded, so a clipped momentum or volatility was invisible on
            # the Data quality panel while a clipped P/E was listed -- the same
            # operation, reported for half the factors it applies to.
            if "data_quality_notes" in df.columns:
                existing = df["data_quality_notes"].fillna("").astype(str)
                merged = [";".join(p for p in (a, ";".join(b)) if p)
                          for a, b in zip(existing, notes)]
                df["data_quality_notes"] = merged
                df["data_quality_flag"] = [len(s) > 0 for s in merged]

        if self.scoring_method == "zscore_normalized":
            df = self._zscore_normalize(df)
        elif self.scoring_method == "percentile_rank":
            df = self._percentile_rank(df)
        else:
            raise ValueError(f"Unsupported scoring method: {self.scoring_method}")

        if self.risk_adjusted and "realized_vol" in df.columns:
            df = self._apply_risk_adjustment(df)
        return df

    @staticmethod
    def _global_z(series: pd.Series) -> pd.Series:
        valid = series.dropna()
        if len(valid) < 2 or not np.isfinite(valid.std()) or valid.std() == 0:
            return pd.Series(0.0, index=series.index)
        return (series - valid.mean()) / valid.std()

    def _sector_neutral_z(self, series: pd.Series, sectors: pd.Series) -> pd.Series:
        """
        Judge a bank's leverage against banks, not against miners.

        Starts from the global z and overwrites only those sectors with enough valid
        observations to produce a meaningful within-sector distribution.
        """
        z = self._global_z(series)
        for sector, idx in series.groupby(sectors).groups.items():
            grp = series.loc[idx].dropna()
            if len(grp) < self.min_sector_size or grp.std() == 0 or not np.isfinite(grp.std()):
                continue
            z.loc[idx] = (series.loc[idx] - grp.mean()) / grp.std()
        return z

    def _zscore_normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        # `imputed` is a plain list indexed positionally, so a frame whose index is
        # not 0..n-1 -- any subset, any filtered view -- raises IndexError deep in
        # the loop. Reset once here rather than trusting every caller to have done
        # it, which is exactly the assumption that broke when the jackknife started
        # passing subsets in.
        df = df.reset_index(drop=True)
        factors = [c for c in self.factor_weights if c in df.columns and c != "market_cap"]
        if not factors:
            df["undervaluation_score"] = 0.0
            df["imputed_factors"] = ""
            return df

        has_sector = "sector" in df.columns
        raw = pd.Series(0.0, index=df.index)
        imputed: List[List[str]] = [[] for _ in range(len(df))]

        for col in factors:
            weight = float(self.factor_weights.get(col, 0.0))
            if weight == 0.0:
                continue

            if has_sector and col in self.sector_neutral_factors:
                z = self._sector_neutral_z(df[col], df["sector"])
            else:
                z = self._global_z(df[col])

            # Only stocks with an actual observation get a z. Everything else must
            # land on 0 -- reindex+fillna is what stops a short series from
            # index-aligning the whole row into NaN. This is fix #1.
            z = z.where(df[col].notna())
            df[f"z_{col}"] = z.reindex(df.index).fillna(0.0)

            contribution = (weight * z).reindex(df.index).fillna(0.0)
            raw = raw + contribution

            for i in df.index[df[col].isna()]:
                imputed[i].append(col)

        df["imputed_factors"] = [";".join(m) for m in imputed]
        if "data_quality_flag" in df.columns:
            df["data_quality_flag"] = df["data_quality_flag"].astype(bool) | (
                df["imputed_factors"].str.len() > 0
            )
        else:
            df["data_quality_flag"] = df["imputed_factors"].str.len() > 0

        lo, hi = raw.min(), raw.max()
        df["undervaluation_score"] = 0.5 if (hi == lo or not np.isfinite(hi - lo)) else (raw - lo) / (hi - lo)
        df["raw_score"] = raw

        n_bad = int(df["undervaluation_score"].isna().sum())
        if n_bad:  # must never fire; kept as a live assertion against regression
            logger.error(f"{n_bad} rows scored NaN after normalisation -- regression of the P0 bug")
        logger.info(f"Scored {len(df)} stocks across {len(factors)} factors")
        return df

    def score_noise_floor(self, df: pd.DataFrame, quantile: float = 0.9,
                          max_names: int = 120) -> float:
        """
        How far a name's score moves when the universe gains or loses one member.

        Every factor is a z-score against the rest of the list, so a score is not a
        property of a company alone -- it is a property of the company and its
        peers. Drop one unrelated name and everybody's score shifts a little. This
        measures that shift by jackknife and returns its `quantile`, which is the
        precision the ranking actually has.

        On the real universe it comes out at about 0.10, against a score standard
        deviation of 3.75 and gaps between picks 3 and 8 of 0.02 to 0.21. Without
        it the ticket buys the name that scored 0.02 higher and presents that as a
        decision.

        Measured on the day's own universe rather than frozen as a constant: a
        hardcoded precision would go stale exactly when the universe changed, which
        is the moment it matters. Roughly a second per twenty names, so it is
        capped and only ever runs in `full_run`.
        """
        if df is None or len(df) < 5:
            return 0.0

        work = df.reset_index(drop=True)
        if len(work) > max_names:
            work = work.head(max_names)

        # `_zscore_normalize` logs a line per call and this makes one per name, so
        # a 74-name universe would bury the run's real output under 75 identical
        # "Scored 73 stocks" lines. Silenced for the loop only.
        logger.disable(__name__)
        try:
            base = self._zscore_normalize(work.copy())["raw_score"]
            base.index = work["ticker"]

            moves: List[float] = []
            for i in range(len(work)):
                sub = work.drop(index=i)
                out = self._zscore_normalize(sub.copy())["raw_score"]
                out.index = sub["ticker"]
                common = out.index.intersection(base.index)
                moves.extend((out[common] - base[common]).abs().dropna().tolist())
        finally:
            logger.enable(__name__)

        if not moves:
            return 0.0
        return float(np.quantile(moves, quantile))

    def _percentile_rank(self, df: pd.DataFrame) -> pd.DataFrame:
        factors = [c for c in self.factor_weights if c in df.columns and c != "market_cap"]
        if not factors:
            df["undervaluation_score"] = 0.5
            df["imputed_factors"] = ""
            return df

        scores = pd.DataFrame(index=df.index)
        for col in factors:
            weight = float(self.factor_weights.get(col, 0.0))
            if weight == 0.0:
                continue
            pct = df[col].rank(pct=True)
            scores[col] = (1.0 - pct) if weight < 0 else pct

        df["undervaluation_score"] = scores.mean(axis=1).fillna(0.5)
        df["imputed_factors"] = [
            ";".join(c for c in factors if pd.isna(df.at[i, c])) for i in df.index
        ]
        logger.info("Percentile rank scoring complete")
        return df

    def _apply_risk_adjustment(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Penalise volatile names using realised volatility from our own price panel.

        Deliberately not yfinance `beta`, which reports 0.016 for Bank Mandiri and
        negative values for several IDX large caps -- unusable as a risk measure.
        """
        df = df.copy()
        vol = df["realized_vol"]
        valid = vol.dropna()
        if valid.empty:
            df["risk_adjusted_score"] = df["undervaluation_score"]
            return df

        scaled = ((vol - valid.min()) / (valid.max() - valid.min())).fillna(0.5) if valid.max() > valid.min() else pd.Series(0.5, index=df.index)
        df["risk_adjusted_score"] = (df["undervaluation_score"] * (1 - 0.5 * scaled)).clip(0, 1)
        logger.info("Risk adjustment applied (realised-volatility weighted)")
        return df

    # -------------------------------------------------------------- diagnostics
    def compute_factor_correlations(self, df: pd.DataFrame) -> pd.DataFrame:
        factors = [c for c in self.factor_weights if c in df.columns]
        if len(factors) < 2:
            return pd.DataFrame()
        return df[factors].corr()

    def save_factor_diagnostics(self, df: pd.DataFrame, output_dir: Path) -> None:
        corr = self.compute_factor_correlations(df)
        if corr.empty:
            return
        out = Path(output_dir) / "factor_correlations.csv"
        corr.round(3).to_csv(out)
        logger.info(f"Factor correlations saved to {out}")

    def filter_by_liquidity(self, df: pd.DataFrame, min_market_cap: float = None) -> pd.DataFrame:
        if min_market_cap and "market_cap" in df.columns:
            return df[df["market_cap"] >= min_market_cap]
        return df
