# src/analysis/ml_ranker.py --revision 1
import pandas as pd
import numpy as np
import joblib
import pickle  # ← Added for bytes serialization
import hashlib
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from core.config import Settings
from core.logger import setup_logger

logger = setup_logger(Path("logs"))


class StockRanker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = None
        self.scaler = None
        self.model_dir = Path(settings.model_dir)  # Ensure Path type
        self.model_dir.mkdir(parents=True, exist_ok=True)
        # Handle both dict-style and attribute-style access for Pydantic
        self.features = settings.ml.get("features", []) if isinstance(settings.ml, dict) else settings.ml.features

    def train(self, df: pd.DataFrame) -> bool:
        df_clean = df.dropna(subset=self.features)
        if len(df_clean) < self.settings.min_samples_for_training:
            logger.warning(f"Insufficient samples ({len(df_clean)}) for ML training")
            return False
            
        X = df_clean[self.features]
        y = (df_clean["undervaluation_score"] > df_clean["undervaluation_score"].median()).astype(int)
        
        tscv = TimeSeriesSplit(n_splits=self.settings.ml.get("walk_forward_splits", 3) if isinstance(self.settings.ml, dict) else self.settings.ml.walk_forward_splits)
        cv_scores = []
        for train_idx, val_idx in tscv.split(X):
            X_tr, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]
            sc = StandardScaler()
            mdl = LogisticRegression(max_iter=1000, random_state=self.settings.ml_random_state)
            mdl.fit(sc.fit_transform(X_tr), y_tr)
            cv_scores.append(mdl.score(sc.transform(X_val), y_val))
        
        logger.info(f"Walk-Forward CV Accuracy: {np.mean(cv_scores):.3f} (±{np.std(cv_scores):.3f})")
        
        self.scaler = StandardScaler()
        self.model = LogisticRegression(max_iter=1000, random_state=self.settings.ml_random_state)
        self.model.fit(self.scaler.fit_transform(X), y)
        self._save_model()
        return True

    def _save_model(self):
        """Save model and scaler with content-based hashing for versioning"""
        if self.model is None or self.scaler is None:
            return
        
        # ✅ FIX: Use pickle.dumps() to get bytes for hashing (joblib has no dumps())
        try:
            model_bytes = pickle.dumps(self.model)
            scaler_bytes = pickle.dumps(self.scaler)
        except Exception as e:
            logger.warning(f"Could not serialize model for hashing: {e}. Using timestamp fallback.")
            model_bytes = b""
            scaler_bytes = b""
        
        model_hash = hashlib.sha256(model_bytes).hexdigest()[:12] if model_bytes else "nohash"
        scaler_hash = hashlib.sha256(scaler_bytes).hexdigest()[:12] if scaler_bytes else "nohash"
        
        model_path = self.model_dir / f"ranker_v{model_hash}.pkl"
        scaler_path = self.model_dir / f"scaler_v{scaler_hash}.pkl"
        
        # ✅ Use joblib.dump() to save to file (correct method)
        joblib.dump(self.model, model_path)
        joblib.dump(self.scaler, scaler_path)
        
        logger.info(f"Model saved: {model_path} | Checksum: {model_hash}")
        logger.info(f"Scaler saved: {scaler_path} | Checksum: {scaler_hash}")

    def predict(self, features_df: pd.DataFrame) -> np.ndarray:
        """
        Predict probability of being 'undervalued' for each row.
        Returns array aligned with input DataFrame index.
        """
        if self.model is None or self.scaler is None:
            # Try to auto-load if not trained in-session
            if not self._try_load_latest_model():
                raise RuntimeError("Model not trained. Call train() first.")
        
        # Ensure only expected features are used, in order
        X_clean = features_df[self.features].dropna()
        
        if X_clean.empty:
            # Return neutral probability for all rows
            return np.array([0.5] * len(features_df))
        
        # Transform and predict
        X_scaled = self.scaler.transform(X_clean)
        predictions = self.model.predict_proba(X_scaled)[:, 1]
        
        # ✅ FIX: Create result array aligned with original index
        # Use pandas Series for safe label-based alignment
        result = pd.Series(np.nan, index=features_df.index)
        result.loc[X_clean.index] = predictions
        
        # Return as numpy array in original order
        return result.values

    def _try_load_latest_model(self) -> bool:
        """Attempt to load the most recent model/scaler pair from model_dir"""
        model_files = list(self.model_dir.glob("ranker_v*.pkl"))
        if not model_files:
            return False
        
        # Load the most recently modified model
        latest_model = max(model_files, key=lambda p: p.stat().st_mtime)
        latest_scaler = latest_model.with_name(latest_model.name.replace("ranker_", "scaler_"))
        
        if not latest_scaler.exists():
            return False
        
        try:
            self.model = joblib.load(latest_model)
            self.scaler = joblib.load(latest_scaler)
            logger.info(f"Auto-loaded model: {latest_model.name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to load model: {e}")
            return False