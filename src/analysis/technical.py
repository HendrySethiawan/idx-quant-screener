# src/analysis/technical.py --revision 1
import pandas as pd
import numpy as np
from typing import Dict

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ma_periods = [5, 20]
    rsi_window = 14
    bb_window = 20
    
    # Moving Averages
    for period in ma_periods:
        df[f"MA_{period}"] = df["Close"].rolling(window=period).mean()
    
    # RSI (renamed to match config/ML feature names)
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(window=rsi_window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_window).mean()
    rs = gain / loss
    df["rsi_14"] = 100 - (100 / (1 + rs))
    
    # Bollinger Bands
    bb_mid = df["Close"].rolling(window=bb_window).mean()
    bb_std = df["Close"].rolling(window=bb_window).std()
    df["BB_Upper"] = bb_mid + 2 * bb_std
    df["BB_Lower"] = bb_mid - 2 * bb_std
    
    # Volume SMA
    df["Volume_SMA"] = df["Volume"].rolling(10).mean()
    
    # ✅ NEW: Price Change %
    df["price_change_pct"] = df["Close"].pct_change() * 100
    
    # ✅ NEW: MA Slopes (computed as columns for ML pipeline consistency)
    df["ma5_slope"] = df["MA_5"].pct_change() * 100
    df["ma20_slope"] = df["MA_20"].pct_change() * 100
    
    return df

def extract_latest_indicators(df: pd.DataFrame) -> Dict[str, float]:
    if df.empty or len(df) < 2:
        return {"rsi_14": np.nan, "ma5_slope": np.nan, "ma20_slope": np.nan, 
                "price_change_pct": np.nan, "volume_ratio": np.nan}
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Direct extraction from computed columns with safe NaN handling
    return {
        "rsi_14": float(last["rsi_14"]) if pd.notna(last["rsi_14"]) else np.nan,
        "ma5_slope": float(last["ma5_slope"]) if pd.notna(last["ma5_slope"]) else np.nan,
        "ma20_slope": float(last["ma20_slope"]) if pd.notna(last["ma20_slope"]) else np.nan,
        "price_change_pct": float(last["price_change_pct"]) if pd.notna(last["price_change_pct"]) else np.nan,
        "volume_ratio": float(last["Volume"] / prev["Volume_SMA"]) if pd.notna(last["Volume"]) and pd.notna(prev["Volume_SMA"]) and prev["Volume_SMA"] != 0 else np.nan
    }