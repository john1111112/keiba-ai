import joblib
from pathlib import Path
import pandas as pd
import numpy as np
import lightgbm as lgb
from typing import Dict, Any, Tuple, List, Optional
from sklearn.metrics import roc_auc_score, accuracy_score

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "keiba_lgbm.joblib"

class KeibaAIModel:
    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or MODEL_PATH
        self.model: Optional[lgb.LGBMClassifier] = None
        self.feature_cols: List[str] = []
        
    def train(self, df_features: pd.DataFrame, feature_cols: List[str], test_ratio: float = 0.2) -> Dict[str, Any]:
        """
        時系列に沿って学習データと評価データに分割してLightGBMを学習
        """
        self.feature_cols = feature_cols
        
        # 時系列分割
        split_idx = int(len(df_features) * (1.0 - test_ratio))
        train_df = df_features.iloc[:split_idx]
        test_df = df_features.iloc[split_idx:]
        
        X_train = train_df[feature_cols]
        y_train = train_df["target"]
        X_test = test_df[feature_cols]
        y_test = test_df["target"]
        
        self.model = lgb.LGBMClassifier(
            objective="binary",
            metric="auc",
            n_estimators=300,
            learning_rate=0.03,
            num_leaves=31,
            max_depth=6,
            random_state=42,
            importance_type="gain",
            verbose=-1
        )
        
        self.model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)]
        )
        
        # 評価
        test_preds = self.model.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, test_preds)
        acc = accuracy_score(y_test, (test_preds >= 0.5).astype(int))
        
        # モデル保存
        self.save()
        
        return {
            "auc": round(float(auc), 4),
            "accuracy": round(float(acc), 4),
            "train_samples": len(train_df),
            "test_samples": len(test_df),
            "best_iteration": self.model.best_iteration_
        }
        
    def predict_race(self, race_df: pd.DataFrame) -> pd.DataFrame:
        """
        出走馬データから好走確率、期待値、予想印を算出
        """
        if race_df is None or len(race_df) == 0:
            return pd.DataFrame()
            
        if self.model is None:
            self.load()
            
        res_df = race_df.copy()
        # 必要な特徴量列を確保
        for c in self.feature_cols:
            if c not in res_df.columns:
                res_df[c] = 0.0
        X = res_df[self.feature_cols]
        probs = self.model.predict_proba(X)[:, 1]
        
        res_df["pred_score"] = probs
        
        # 期待値計算（予測好走率 × 単勝オッズ）
        if "odds" in res_df.columns:
            res_df["expected_value"] = res_df["pred_score"] * res_df["odds"]
        else:
            res_df["expected_value"] = res_df["pred_score"]
            
        # スコア順にソートして印を付与 (ascending=False)
        res_df = res_df.sort_values("pred_score", ascending=False).reset_index(drop=True)
        
        marks = ["◎", "○", "▲", "△", "△", "☆"]
        mark_list = []
        for i in range(len(res_df)):
            if i < len(marks):
                mark_list.append(marks[i])
            else:
                mark_list.append("-")
        res_df["mark"] = mark_list
        
        # 穴馬判定: 期待値が1.5以上かつ単勝オッズ8倍以上
        if "odds" in res_df.columns:
            res_df["is_ana"] = (res_df["expected_value"] >= 1.5) & (res_df["odds"] >= 8.0)
        else:
            res_df["is_ana"] = False
            
        return res_df

    def get_feature_importance(self) -> pd.DataFrame:
        if self.model is None:
            self.load()
        importance = self.model.feature_importances_
        return pd.DataFrame({
            "feature": self.feature_cols,
            "importance": importance
        }).sort_values("importance", ascending=False).reset_index(drop=True)

    def save(self):
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self.model, "feature_cols": self.feature_cols}, self.model_path)

    def load(self):
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model file not found: {self.model_path}")
        try:
            data = joblib.load(self.model_path)
            self.model = data["model"]
            self.feature_cols = data["feature_cols"]
        except Exception as e:
            # 万一の破損時は自己修復
            print(f"Warning: Model file corrupted ({e}). Retraining self-heal...")
            from .features import build_features
            from .database import load_all_race_data
            df_raw = load_all_race_data()
            df_feat, fcols = build_features(df_raw.head(1000))
            self.train(df_feat, fcols)

