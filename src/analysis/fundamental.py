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

        # yfinance returns dividendYield on a percent scale for most .JK names
        # (BBRI came back as 14.17, not 0.1417). Normalise before bounds are applied.
        if "dividend_yield" in df.columns:
            pct_scale = df["dividend_yield"] > 1.0
            if pct_scale.any():
                df.loc[pct_scale, "dividend_yield"] = df.loc[pct_scale, "dividend_yield"] / 100.0
                for i in df.index[pct_scale]:
                    notes[i].append("dividend_yield:rescaled_from_percent")

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

        # Two-sided magnitude test catches both huge positives (P/B 179,615) and
        # huge negatives (deeply negative ROE from a one-off writedown).
        for col, bound in self.sanity_bounds.items():
            if col not in df.columns:
                continue
            bad = (df[col].abs() > float(bound)).fillna(False)
            if bad.any():
                df.loc[bad, col] = np.nan
                for i in df.index[bad]:
                    notes[i].append(f"{col}:nullified(|x|>{bound:g})")

        # Snapshot the two multiples valuation needs BEFORE winsorization.
        #
        # _winsorize clips outliers to a shared bound, which is right for ranking --
        # it is the PTRO/BRPT fix -- but it makes the stored value a property of the
        # bound rather than of the company. On a real run six unrelated tickers came
        # back with pe_ratio == 50.738811 to six decimals, and four shared
        # price_to_book == 9.266952. Deriving earnings or book value from a clipped
        # multiple would invent them, and it would do so precisely for the extreme
        # names where "is this expensive?" is the whole question.
        for col in ("pe_ratio", "price_to_book"):
            if col in df.columns:
                df[f"unclipped_{col}"] = df[col]

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
