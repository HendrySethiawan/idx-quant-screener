# src/analysis/fundamental.py --revision 1
import pandas as pd
import numpy as np
from typing import Dict, List, Union
from pathlib import Path
from pydantic import BaseModel

from core.logger import setup_logger

logger = setup_logger(Path("logs"))


class FundamentalEngine:
    def __init__(self, config: Union[dict, BaseModel]):
        """
        Accept both dict and Pydantic BaseModel for config.
        Normalizes to dict internally for consistent access.
        """
        # Normalize Pydantic model → dict
        if isinstance(config, BaseModel):
            if hasattr(config, "model_dump"):
                config = config.model_dump()
            else:
                config = config.dict()
        
        self.config = config
        self.metrics = config.get("fundamental_metrics", [
            "pe_ratio", "price_to_book", "dividend_yield", "beta"
        ])
        self.scoring_method = config.get("scoring_method", "zscore_normalized")
        # ✅ NEW: Risk adjustment toggle
        self.risk_adjusted = config.get("risk_adjusted", False)
        
    def validate_fundamentals(self, records: List[Dict]) -> pd.DataFrame:
        df = pd.DataFrame(records)
        required_cols = ["ticker", "name"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        
        numeric_cols = [c for c in self.metrics if c in df.columns]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            
        if "pe_ratio" in df.columns:
            df = df[df["pe_ratio"] > 0]
        if "price_to_book" in df.columns:
            df = df[df["price_to_book"] > 0]
        if "beta" in df.columns:
            df = df[(df["beta"] > -2) & (df["beta"] < 5)]
            
        logger.info(f"Validated {len(df)} fundamental records")
        return df
    
    def compute_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.scoring_method == "zscore_normalized":
            df = self._zscore_normalize(df)
        elif self.scoring_method == "percentile_rank":
            df = self._percentile_rank(df)
        else:
            raise ValueError(f"Unsupported scoring method: {self.scoring_method}")
        
        # ✅ NEW: Apply risk adjustment if enabled
        if self.risk_adjusted and "beta" in df.columns and "undervaluation_score" in df.columns:
            df = self._apply_risk_adjustment(df)
            
        return df
    
    def _apply_risk_adjustment(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Adjust undervaluation score by beta (volatility).
        Formula: score * (1 - beta/2), clamped to [0, 1]
        Lower beta → less penalty; higher beta → more penalty
        """
        df = df.copy()
        # Clip beta to [0, 2] to avoid extreme penalties
        beta_clipped = df["beta"].clip(0, 2)
        # Risk multiplier: 1.0 (beta=0) → 0.0 (beta=2)
        risk_multiplier = 1 - (beta_clipped / 2)
        # Apply adjustment
        df["risk_adjusted_score"] = (df["undervaluation_score"] * risk_multiplier).clip(0, 1)
        
        # Optional: replace base score with risk-adjusted version
        # df["undervaluation_score"] = df["risk_adjusted_score"]
        
        logger.info("Risk adjustment applied (beta-weighted scoring)")
        return df
    
    def _zscore_normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        numeric_cols = [c for c in self.metrics if c in df.columns and c != "market_cap"]
        if not numeric_cols:
            df["undervaluation_score"] = 0.0
            return df
            
        weights = {"pe_ratio": -1.0, "price_to_book": -1.0, "dividend_yield": 1.0, "beta": -1.0}
        df["undervaluation_score"] = 0.0
        for col in numeric_cols:
            if col in weights and col in df.columns:
                col_data = df[col].dropna()
                if col_data.std() > 0:
                    z = (col_data - col_data.mean()) / col_data.std()
                    df["undervaluation_score"] += weights.get(col, 0) * z
        
        min_val, max_val = df["undervaluation_score"].min(), df["undervaluation_score"].max()
        if max_val != min_val:
            df["undervaluation_score"] = (df["undervaluation_score"] - min_val) / (max_val - min_val)
        else:
            df["undervaluation_score"] = 0.5
        logger.info("Z-score normalization complete")
        return df
    
    def _percentile_rank(self, df: pd.DataFrame) -> pd.DataFrame:
        numeric_cols = [c for c in self.metrics if c in df.columns and c != "market_cap"]
        if not numeric_cols:
            df["undervaluation_score"] = 0.5
            return df
        scores = pd.DataFrame()
        for col in numeric_cols:
            if col in df.columns:
                scores[col] = df[col].rank(pct=True)
        df["undervaluation_score"] = scores.mean(axis=1).fillna(0.5)
        logger.info("Percentile rank scoring complete")
        return df
    
    def filter_by_liquidity(self, df: pd.DataFrame, min_market_cap: float = None) -> pd.DataFrame:
        if min_market_cap and "market_cap" in df.columns:
            df = df[df["market_cap"] >= min_market_cap]
        return df