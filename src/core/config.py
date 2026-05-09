# core/config.py --revision 1
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
from typing import Dict, List, Optional, Any


class Settings(BaseSettings):
    # App Configuration
    app_env: str = "dev"
    log_level: str = "INFO"
    data_retention_days: int = Field(default=30, ge=1)
    cache_ttl_minutes: int = Field(default=60, ge=1)
    yfinance_timeout: int = Field(default=30, ge=5)
    max_retries: int = Field(default=3, ge=1)
    
    # Paths
    output_dir: Path = Field(default=Path("data/output"))
    model_dir: Path = Field(default=Path("models"))
    log_dir: Path = Field(default=Path("logs"))
    
    # Data Configuration
    stock_tickers: Dict[str, str] = Field(default_factory=dict)
    benchmarks: Dict[str, str] = Field(default_factory=dict)
    
    # ✅ NEW: Sector mapping for visualization/grouping
    sectors: Dict[str, str] = Field(default_factory=dict)
    
    # Technical Analysis Config
    technical: Dict[str, Any] = Field(default_factory=lambda: {
        "ma_periods": [5, 20],
        "rsi_window": 14,
        "bb_window": 20
    })
    
    # Fundamental Analysis Config
    fundamental_metrics: List[str] = Field(default_factory=lambda: [
        "pe_ratio", "price_to_book", "dividend_yield", "beta"
    ])
    scoring_method: str = "zscore_normalized"
    
    # ✅ NEW: Risk adjustment toggle for fundamental scoring
    risk_adjusted: bool = False
    
    # ML Configuration
    ml: Dict[str, Any] = Field(default_factory=lambda: {
        "features": [],
        "scoring_method": "zscore_normalized",
        "walk_forward_splits": 3,
        "model_type": "gradient_boosting",
        "n_estimators": 100,
        "max_depth": 5,
        "learning_rate": 0.1
    })
    
    # Training Configuration
    ml_random_state: int = 42
    min_samples_for_training: int = Field(default=10, ge=5)
    ml_test_size: float = Field(default=0.2, ge=0.0, le=1.0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow"  # Allow extra fields from YAML for flexibility
    )
    
    @model_validator(mode="after")
    def validate_paths(self) -> "Settings":
        """Ensure all path fields are Path objects and create directories"""
        for field_name in ["output_dir", "model_dir", "log_dir"]:
            path_val = getattr(self, field_name)
            if isinstance(path_val, str):
                path_val = Path(path_val)
                setattr(self, field_name, path_val)
            path_val.mkdir(parents=True, exist_ok=True)
        return self
    
    @model_validator(mode="after")
    def validate_ml_features(self) -> "Settings":
        """Ensure ML features is a list"""
        if isinstance(self.ml, dict) and "features" in self.ml:
            if not isinstance(self.ml["features"], list):
                self.ml["features"] = []
        return self


def load_settings(override_config_path: Optional[str] = None) -> Settings:
    """
    Load settings with optional YAML override.
    
    Args:
        override_config_path: Path to YAML config file for overrides
        
    Returns:
        Settings: Validated Pydantic settings object
    """
    # Initialize with defaults + env vars
    settings = Settings()
    
    # Apply YAML overrides if provided
    if override_config_path and Path(override_config_path).exists():
        with open(override_config_path, "r", encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        
        # ✅ Deep merge for nested dicts (technical, ml, sectors, etc.)
        for key, value in overrides.items():
            if hasattr(settings, key):
                current_val = getattr(settings, key)
                # Deep merge dicts, replace primitives
                if isinstance(current_val, dict) and isinstance(value, dict):
                    merged = {**current_val, **value}
                    setattr(settings, key, merged)
                else:
                    setattr(settings, key, value)
    
    # ✅ Ensure Path objects for directory fields (in case YAML had strings)
    for field_name in ["output_dir", "model_dir", "log_dir"]:
        path_val = getattr(settings, field_name)
        if isinstance(path_val, str):
            setattr(settings, field_name, Path(path_val))
        getattr(settings, field_name).mkdir(parents=True, exist_ok=True)
    
    return settings


# ✅ Optional: Helper to get typed sub-configs
def get_technical_config(settings: Settings) -> Dict[str, Any]:
    """Get validated technical analysis config"""
    return settings.technical if isinstance(settings.technical, dict) else {}


def get_ml_config(settings: Settings) -> Dict[str, Any]:
    """Get validated ML config with defaults"""
    defaults = {
        "features": [],
        "scoring_method": "zscore_normalized",
        "walk_forward_splits": 3,
        "model_type": "gradient_boosting",
        "n_estimators": 100,
        "max_depth": 5,
        "learning_rate": 0.1
    }
    if isinstance(settings.ml, dict):
        return {**defaults, **settings.ml}
    return defaults