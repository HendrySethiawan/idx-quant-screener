# core/config.py --revision 2
"""
Settings for the IDX quant screener.

Revision 2 changes (see docs/AUDIT.md):
  * `history_period` split out from `data_retention_days`. The old code reused the
    retention setting as the yfinance lookback, which capped every price series at
    ~30 bars and made momentum impossible to compute.
  * Added `broker`, `account`, `liquidity` and `regime` blocks so position sizing can
    respect IDX lot sizes and Indopremier's real fee schedule.
  * Added `factor_weights` / `sanity_bounds` / `sector_neutral_factors` so scoring is
    driven by config rather than a hardcoded dict inside FundamentalEngine.
  * `use_ml` defaults to False -- the ranker trains on a label derived from the very
    score it overwrites, so leaving it on makes the output circular.
"""
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
from typing import Dict, List, Optional, Any

USER_CONFIG_PATH = "configs/user.yaml"

_PATH_FIELDS = ("output_dir", "model_dir", "log_dir")


class Settings(BaseSettings):
    # ---- App ---------------------------------------------------------------
    app_env: str = "dev"
    log_level: str = "INFO"
    data_retention_days: int = Field(default=30, ge=1)  # cache housekeeping only

    # ---- Data --------------------------------------------------------------
    # Price lookback handed to yfinance. Needs >13 months for 12-month momentum.
    history_period: str = "2y"
    cache_ttl_minutes: int = Field(default=60, ge=1)
    yfinance_timeout: int = Field(default=30, ge=5)
    max_retries: int = Field(default=3, ge=1)

    # ---- Paths -------------------------------------------------------------
    output_dir: Path = Field(default=Path("data/output"))
    model_dir: Path = Field(default=Path("models"))
    log_dir: Path = Field(default=Path("logs"))

    # ---- Universe ----------------------------------------------------------
    stock_tickers: Dict[str, str] = Field(default_factory=dict)
    benchmarks: Dict[str, str] = Field(default_factory=dict)
    sectors: Dict[str, str] = Field(default_factory=dict)

    # ---- Technical ---------------------------------------------------------
    technical: Dict[str, Any] = Field(default_factory=lambda: {
        "ma_periods": [5, 20],
        "rsi_window": 14,
        "bb_window": 20,
        "vol_window": 60,
        "liquidity_window": 20,
    })

    # ---- Fundamental -------------------------------------------------------
    fundamental_metrics: List[str] = Field(default_factory=lambda: [
        "pe_ratio", "price_to_book", "dividend_yield", "beta",
        "roe", "gross_margin", "debt_to_equity",
    ])
    scoring_method: str = "zscore_normalized"

    # Sign encodes direction: higher composite == more attractive.
    # `beta` is deliberately absent -- yfinance reports 0.016 for Bank Mandiri and
    # -0.71 for others, which is noise. Realised volatility is used instead.
    factor_weights: Dict[str, float] = Field(default_factory=lambda: {
        "pe_ratio": -1.0,
        "price_to_book": -1.0,
        "dividend_yield": 1.0,
        "roe": 1.0,
        "gross_margin": 1.0,
        "debt_to_equity": -1.0,
        "realized_vol": -0.5,
        "mom_1m": 0.5,
        "mom_6m": 1.0,
        "mom_12m": 1.0,
    })

    # Two-sided magnitude bounds. |x| above the bound is a data glitch -> NaN ->
    # neutral 0 contribution, never a silent row deletion.
    sanity_bounds: Dict[str, float] = Field(default_factory=lambda: {
        "dividend_yield": 0.15,
        "price_to_book": 20.0,
        "roe": 3.0,
        "gross_margin": 1.5,
        "debt_to_equity": 2000.0,
        "pe_ratio": 200.0,
    })

    sector_neutral_factors: List[str] = Field(default_factory=lambda: [
        "roe", "gross_margin", "debt_to_equity",
    ])
    min_sector_size: int = Field(default=4, ge=2)
    winsorize_k: float = Field(default=5.0, gt=0)

    risk_adjusted: bool = False
    max_per_sector: int = Field(default=2, ge=0)
    top_picks_n: int = Field(default=8, ge=1)

    # ---- Broker (Indopremier) ---------------------------------------------
    broker: Dict[str, Any] = Field(default_factory=lambda: {
        "buy_fee": 0.0019,
        "sell_fee": 0.0029,
        # Bea meterai: charged once per DAY that contains at least one sell,
        # not per order. Batching sells is therefore worth real money.
        "stamp_duty_rp": 10000,
        "lot_size": 100,
    })

    # ---- Account -----------------------------------------------------------
    account: Dict[str, Any] = Field(default_factory=lambda: {
        "capital_rp": 100_000_000,
        "min_positions": 3,
        "max_positions": 6,
        # A name's single lot must fit inside one slot; above this it is forced
        # overweight with no way to trim, so it is excluded from sizing.
        "max_lot_to_slot": 1.0,
        # Penalty applied per unit of max weight deviation when choosing N.
        "deviation_penalty": 0.5,
        # Floor on a single position. The Rp10,000 stamp is 2% of a Rp500,000
        # position but 0.4% of a Rp2.5 juta one, so slicing a small budget into
        # many slots quietly hands the broker the edge.
        "min_position_rp": 1_000_000,
        "holdings_path": "current_holdings.yaml",
        "journal_path": "data/journal.csv",
        "marks_path": "data/journal_marks.csv",
        # Closed round-trips required before the report will name a winner
        # between the screener's picks and the user's own. At 4-8 trades a month
        # anything less is noise.
        "min_trades_for_verdict": 30,
    })

    # ---- Liquidity ---------------------------------------------------------
    liquidity: Dict[str, Any] = Field(default_factory=lambda: {
        # A position may not exceed this share of median daily traded value,
        # so it can be exited in roughly one session without moving the price.
        "max_position_pct_of_daily_value": 0.01,
        "min_median_daily_value_rp": 250_000_000,
        "window": 20,
    })

    # ---- Regime ------------------------------------------------------------
    regime: Dict[str, Any] = Field(default_factory=lambda: {
        "trend_ma": 200,
        "benchmark": "^JKSE",
        "fx_ticker": "IDR=X",
        # Deployment fraction by number of risk-on signals (0, 1, 2 of 2).
        "deploy_ladder": [0.30, 0.60, 1.00],
    })

    # ---- Backtest ----------------------------------------------------------
    backtest: Dict[str, Any] = Field(default_factory=lambda: {
        "history_period": "5y",
        "rebalances": ["M", "W"],
        # Skip rebalance dates with fewer listed names than this -- early in the
        # window much of the universe had not IPO'd yet.
        "min_names": 10,
    })

    events_path: str = "configs/events.yaml"
    event_horizon_days: int = Field(default=14, ge=1)

    # ---- ML (off by design) -----------------------------------------------
    use_ml: bool = False
    ml: Dict[str, Any] = Field(default_factory=lambda: {
        "features": [],
        "scoring_method": "zscore_normalized",
        "walk_forward_splits": 3,
    })
    ml_random_state: int = 42
    min_samples_for_training: int = Field(default=10, ge=5)
    ml_test_size: float = Field(default=0.2, ge=0.0, le=1.0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    @model_validator(mode="after")
    def _coerce_paths(self) -> "Settings":
        for field_name in _PATH_FIELDS:
            path_val = getattr(self, field_name)
            if isinstance(path_val, str):
                path_val = Path(path_val)
                setattr(self, field_name, path_val)
            path_val.mkdir(parents=True, exist_ok=True)
        return self

    @model_validator(mode="after")
    def _validate_ml_features(self) -> "Settings":
        if isinstance(self.ml, dict) and not isinstance(self.ml.get("features", []), list):
            self.ml["features"] = []
        return self

    # -- convenience accessors ------------------------------------------------
    @property
    def lot_size(self) -> int:
        return int(self.broker.get("lot_size", 100))

    @property
    def capital_rp(self) -> float:
        return float(self.account.get("capital_rp", 0) or 0)

    def sector_of(self, ticker: str) -> str:
        return self.sectors.get(ticker, "Unknown")


def _apply_overrides(settings: Settings, overrides: dict, replace_keys: tuple = ()) -> None:
    """
    Merge a YAML dict onto a Settings instance in place.

    Dicts deep-merge so a partial override does not wipe sibling keys. Keys named in
    `replace_keys` are replaced wholesale instead -- required for `stock_tickers` and
    `sectors`, where an additive merge could never express a *removal*.
    """
    for key, value in overrides.items():
        if not hasattr(settings, key):
            continue
        if key in replace_keys:
            setattr(settings, key, value)
            continue
        current_val = getattr(settings, key)
        if isinstance(current_val, dict) and isinstance(value, dict):
            setattr(settings, key, {**current_val, **value})
        else:
            setattr(settings, key, value)


def load_settings(
    override_config_path: Optional[str] = None,
    user_config_path: Optional[str] = USER_CONFIG_PATH,
) -> Settings:
    """Defaults -> env -> default.yaml -> user.yaml (app-managed overlay)."""
    settings = Settings()

    if override_config_path and Path(override_config_path).exists():
        with open(override_config_path, "r", encoding="utf-8") as f:
            _apply_overrides(settings, yaml.safe_load(f) or {})

    if user_config_path and Path(user_config_path).exists():
        with open(user_config_path, "r", encoding="utf-8") as f:
            _apply_overrides(
                settings,
                yaml.safe_load(f) or {},
                replace_keys=("stock_tickers", "sectors"),
            )

    for field_name in _PATH_FIELDS:
        path_val = getattr(settings, field_name)
        if isinstance(path_val, str):
            setattr(settings, field_name, Path(path_val))
        getattr(settings, field_name).mkdir(parents=True, exist_ok=True)

    return settings


def save_user_overrides(updates: dict, path: str = USER_CONFIG_PATH) -> Path:
    """Write app-managed overrides to configs/user.yaml. default.yaml is never rewritten."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if target.exists():
        with open(target, "r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    existing.update(updates)
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(existing, f, sort_keys=False, allow_unicode=True)
    return target
