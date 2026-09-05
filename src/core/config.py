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
        # Placeholder only. Real capital belongs in configs/user.yaml, which is
        # git-ignored -- this repository is public.
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
        # Deposits and withdrawals. Once this file has a row, `capital_rp` above
        # is derived from it rather than read -- see portfolio/cash.sync_capital.
        "cash_path": "data/cash.csv",
        # Dividends received. Kept out of the journal so they can never reach FIFO
        # matching -- see portfolio/dividends.py.
        "dividends_path": "data/dividends.csv",
        # The last fetched screen, so launching does not mean fetching.
        "snapshot_path": "data/snapshot/run.joblib",
        # Closed round-trips required before the report will name a winner
        # between the screener's picks and the user's own. At 4-8 trades a month
        # anything less is noise.
        "min_trades_for_verdict": 30,
    })

    # ---- Market rules ------------------------------------------------------
    # IDX rules, not broker rules, and in config because BEI changes them. The
    # minimum tradable price is moving Rp50 -> Rp1 (tested 22 & 29 Aug 2026,
    # targeted 7 Sept 2026); when it lands, edit `min_price_rp` here and nothing
    # else needs to know.
    #
    # The thresholds decide when a price series can no longer support a
    # volatility or momentum estimate. Chosen from the real universe, where the
    # separation is wide: GOTO sat at the floor for 34-100% of each factor window
    # and WIKA printed no change on 95-99% of sessions, while healthy names run
    # 6-24% flat and never touch the floor.
    market: Dict[str, Any] = Field(default_factory=lambda: {
        "min_price_rp": 50.0,
        "max_flat_pct": 0.60,      # sessions with no price change at all
        "max_floor_pct": 0.20,     # sessions sitting at the minimum price
    })

    # Annualised, in percent. Subtracted before every Sharpe -- roughly the BI
    # 7-day reverse repo rate. Return divided by volatility with no risk-free
    # term is not a Sharpe ratio, and in a market with a 5%+ policy rate the
    # difference is most of the number.
    risk_free_pct: float = 5.5

    # ---- Exits -------------------------------------------------------------
    # When to get out, and in how many pieces. Every default here was measured on
    # the cached universe -- 1,705 simulated entries across 46 tradable names over
    # a 42-session horizon -- rather than picked as a round number.
    #
    #   rule                     stopped out within 2 months   median sessions held
    #   2.0 x ATR fixed at entry            51%                        23
    #   2.5 x ATR fixed at entry            44%                        27
    #   3.0 x ATR fixed at entry            39%                        31
    #   2.0 x ATR trailing                  82%                        13
    #   2.5 x ATR trailing                  74%                        16
    #   3.0 x ATR trailing                  66%                        20
    #
    # Trailing the whole position from entry is why `trail_after_stage` exists: at
    # Rp10 juta each firing costs about Rp22,000 in stamp, sell fee and the buy fee
    # to get back in -- roughly 0.9% of a Rp2.5 juta position, and near Rp700,000 a
    # year across four slots. So the stop is fixed until the first trim banks a
    # gain, and only the remainder trails.
    risk: Dict[str, Any] = Field(default_factory=lambda: {
        "atr_window": 14,
        # Stop distance in ATRs. A percentage cannot serve this universe: 2.5 x ATR
        # is 3.0% on BBSI and 16.8% on INET.
        "k_atr": 2.5,
        # Trim levels, in multiples of the initial risk (R = entry - stop), and the
        # share of the ORIGINAL position sold at each. What is left runs.
        "ladder": [1.0, 2.0],
        "ladder_fractions": [0.4, 0.3],
        "trail_after_stage": 1,
        # The daily close, never the intraday low. This tool has no live feed and
        # cannot observe an intraday print; close-triggering also churns less.
        "trigger": "close",
        # Beyond this the stop is not protection, it is a shrug. The plan says the
        # name is too wild for one slot instead of quoting a 30% level.
        "max_stop_pct": 15.0,
        # A trim whose own sell fee and stamp exceed this share of its value is
        # dropped and its lots roll into the next stage. The threshold is set by
        # the stamp, not by taste: 10,000 / (0.025 - 0.0029) makes the smallest
        # viable trim about Rp452,000. Below that the Rp10,000 dominates -- one
        # lot of a Rp179,500 position costs Rp10,520 to sell alone, 5.9%. Anything
        # tighter than this deletes every ladder the account can actually hold,
        # since a solo Rp830,000 trim already costs 1.49%.
        "max_trim_cost_pct": 2.5,
        # Warned about, not enforced: the sizer deploys the budget, and it is the
        # code the backtest validates. See docs and the ticket's risk line.
        "max_position_risk_pct": 2.0,
        # Sessions a name is blocked from re-entry after it was sold. Without this
        # the next re-rank buys back what the stop just sold, paying both sides.
        "cooldown_sessions": 10,
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

    # ---- Valuation ---------------------------------------------------------
    # Peer-multiple fair value. Separate from `factor_weights`, which produces a
    # RANK; this produces a price. See src/analysis/valuation.py.
    valuation: Dict[str, Any] = Field(default_factory=lambda: {
        "enabled": True,
        # Below this many names, a sector median is noise -- fall back to the
        # universe and say so. Four of nine real sectors hold two names.
        "min_peers": 4,
        # When the P/E- and P/B-implied prices differ by more than this, the page
        # says they disagree instead of implying the midpoint means something.
        "wide_band_pct": 0.60,
    })

    # ---- Backtest ----------------------------------------------------------
    backtest: Dict[str, Any] = Field(default_factory=lambda: {
        "history_period": "5y",
        "rebalances": ["M", "W"],
        # Skip rebalance dates with fewer listed names than this -- early in the
        # window much of the universe had not IPO'd yet.
        "min_names": 10,
    })

    # ---- Selection ---------------------------------------------------------
    # `max_per_sector` caps by label; this caps by behaviour. 0.70 sits above all
    # but 21 of 2,401 measured pairs, so it is a safety net rather than a filter:
    # it catches BRPT/PTRO (0.87, one conglomerate across two sectors) and the big
    # banks (0.80), and leaves ordinary picks alone.
    selection: Dict[str, Any] = Field(default_factory=lambda: {
        "max_correlation": 0.70,
        "correlation_window": 120,
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


def _deep_merge(base: dict, updates: dict) -> dict:
    """
    Merge `updates` into `base`, recursing into nested dicts.

    A shallow `dict.update` replaces a whole block: writing
    `{"account": {"capital_rp": X}}` over a file that also held
    `account.min_positions` would silently drop it. Editing one field at a time --
    which is exactly what a settings screen does -- hits that on the second edit,
    and the loss is invisible until a rule quietly reverts to its default.
    """
    out = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def save_user_overrides(updates: dict, path: Optional[str] = None) -> Path:
    """
    Write app-managed overrides to configs/user.yaml. default.yaml is never rewritten.

    The default is resolved HERE rather than bound at definition, so redirecting
    `USER_CONFIG_PATH` actually redirects the write. Bound as a default argument it
    could not be, and the test suite wrote a fixture value straight into the
    reader's real capital -- twice, before anyone noticed the figure had changed.
    """
    target = Path(path or USER_CONFIG_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if target.exists():
        with open(target, "r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    merged = _deep_merge(existing, updates)
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False, allow_unicode=True)
    return target


def drop_user_override(dotted: str, path: Optional[str] = None) -> Path:
    """
    Remove one override so the value falls back to `default.yaml`.

    Resetting a field has to mean "delete the override", not "write the default in
    again": writing it back would freeze today's default into the user's file, and a
    later change to the shipped default would silently not reach them.

    Path resolved at call time, for the reason `save_user_overrides` gives.
    """
    target = Path(path or USER_CONFIG_PATH)
    if not target.exists():
        return target

    with open(target, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            return target
        node = node[part]
    node.pop(parts[-1], None)

    # Do not leave an empty block behind; it reads as "something is overridden here".
    for depth in range(len(parts) - 1, 0, -1):
        parent = data
        for part in parts[:depth - 1]:
            parent = parent[part]
        if isinstance(parent.get(parts[depth - 1]), dict) and not parent[parts[depth - 1]]:
            parent.pop(parts[depth - 1])

    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return target
