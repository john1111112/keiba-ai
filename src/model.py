import shutil
import hashlib
import json
import joblib
from pathlib import Path
import pandas as pd
import numpy as np
import lightgbm as lgb
from typing import Dict, Any, Tuple, List, Optional
from sklearn.metrics import roc_auc_score, accuracy_score
from .features import FEATURE_COLS

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "keiba_lgbm.joblib"
BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"

# JRAの最大出走頭数は18頭（地方・海外含む場合でも最大28頭）。
# 0 = 未確定・発走前、29以上（99等）または負数・NaN = 取消・除外・競走中止として明確に区別する。
MIN_VALID_FINISH_ORDER = 1
MAX_VALID_FINISH_ORDER = 28


class KeibaAIModel:
    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path or MODEL_PATH
        self.model: Optional[lgb.LGBMClassifier] = None
        self.feature_cols: List[str] = []

    @staticmethod
    def validate_and_filter_training_data(df_features: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, int]]:
        """
        【学習データの安全性検証・フィルタリング】
        1. 未確定レース（finish_order == 0）を学習対象から完全に除外する。
        2. 取消・除外・競走中止（finish_order > 28 または finish_order < 0 または NaN）を明確に分離・除外する。
        3. 除外された件数の内訳を検証レポートとして返す。
        """
        if df_features is None or len(df_features) == 0:
            raise ValueError("学習データが空です。")
        if "finish_order" not in df_features.columns:
            raise ValueError("学習データに 'finish_order' カラムが存在しません。")

        total_rows = len(df_features)
        fo = pd.to_numeric(df_features["finish_order"], errors="coerce")

        unconfirmed_mask = (fo == 0)
        aborted_or_invalid_mask = fo.isna() | (fo < 0) | (fo > MAX_VALID_FINISH_ORDER)
        valid_mask = (fo >= MIN_VALID_FINISH_ORDER) & (fo <= MAX_VALID_FINISH_ORDER)

        clean_df = df_features.loc[valid_mask].copy()
        if "date" in clean_df.columns and "race_id" in clean_df.columns:
            clean_df = clean_df.sort_values(["date", "race_id"]).reset_index(drop=True)
        else:
            clean_df = clean_df.reset_index(drop=True)

        # 目的変数を再検証（1〜3着のみ1、4〜28着は0）
        clean_df["target"] = ((clean_df["finish_order"] >= 1) & (clean_df["finish_order"] <= 3)).astype(int)

        stats = {
            "input_rows": int(total_rows),
            "valid_finished_rows": int(valid_mask.sum()),
            "excluded_unconfirmed_rows": int(unconfirmed_mask.sum()),
            "excluded_aborted_or_invalid_rows": int(aborted_or_invalid_mask.sum()),
        }
        if len(clean_df) < 50:
            raise ValueError(f"有効な確定完走データが不足しています ({len(clean_df)} 行)。")

        return clean_df, stats

    def train(
        self,
        df_features: pd.DataFrame,
        feature_cols: List[str],
        test_ratio: float = 0.2,
        save_model: bool = True
    ) -> Dict[str, Any]:
        """
        未確定・除外データを厳密に排除した上で、時系列に沿って学習データと評価データに分割してLightGBMを学習する。
        """
        self.feature_cols = list(feature_cols)
        clean_df, validation_stats = self.validate_and_filter_training_data(df_features)

        # 時系列分割
        split_idx = int(len(clean_df) * (1.0 - test_ratio))
        train_df = clean_df.iloc[:split_idx]
        test_df = clean_df.iloc[split_idx:]

        X_train = train_df[self.feature_cols]
        y_train = train_df["target"]
        X_test = test_df[self.feature_cols]
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

        if save_model:
            self.save()

        return {
            "auc": round(float(auc), 4),
            "accuracy": round(float(acc), 4),
            "train_samples": len(train_df),
            "test_samples": len(test_df),
            "best_iteration": self.model.best_iteration_,
            "validation_stats": validation_stats,
        }

    def predict_race(self, race_df: pd.DataFrame) -> pd.DataFrame:
        """
        出走馬データから好走確率、期待値、予想印を算出
        """
        if race_df is None or len(race_df) == 0:
            return pd.DataFrame()

        res_df = race_df.copy()
        if "pred_score" not in res_df.columns or res_df["pred_score"].isna().any():
            if self.model is None:
                self.load()
            for c in self.feature_cols:
                if c not in res_df.columns:
                    res_df[c] = 0.0
            X = res_df[self.feature_cols]
            probs = self.model.predict_proba(X)[:, 1]
            res_df["pred_score"] = probs

        # 期待値計算（予測好走率 × 単勝オッズ、ただしオッズ未発表<=0のときは0.0）
        if "odds" in res_df.columns:
            res_df["expected_value"] = np.where(
                res_df["odds"] > 0.0,
                res_df["pred_score"] * res_df["odds"],
                0.0
            )
        else:
            res_df["expected_value"] = 0.0

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
        # 上書き前に既存の正常モデルがあれば自動バックアップを作成
        if self.model_path.exists():
            bak_path = self.model_path.with_suffix(".joblib.bak")
            try:
                shutil.copy2(self.model_path, bak_path)
            except Exception:
                pass
        joblib.dump({"model": self.model, "feature_cols": self.feature_cols}, self.model_path)

    def _find_backup_model_paths(self) -> List[Path]:
        """利用可能なバックアップモデルを新しい順に探索する"""
        candidates = []
        adj_bak = self.model_path.with_suffix(".joblib.bak")
        if adj_bak.exists():
            candidates.append(adj_bak)
        if BACKUP_DIR.exists():
            for p in sorted(BACKUP_DIR.rglob("keiba_lgbm.joblib"), key=lambda x: x.stat().st_mtime, reverse=True):
                candidates.append(p)
        return candidates

    @staticmethod
    def _validate_loaded_model_dict(data: Any) -> bool:
        """
        読み込んだモデルオブジェクトが正常な LGBMClassifier であり、
        特徴量リストが現在のコードの FEATURE_COLS（31個・順序含む）と完全一致するかを厳密検証する。
        """
        if not isinstance(data, dict):
            return False
        mdl = data.get("model")
        fcols = data.get("feature_cols")
        if not isinstance(mdl, lgb.LGBMClassifier):
            return False
        if not hasattr(mdl, "booster_") or mdl.booster_ is None:
            return False
        if not isinstance(fcols, list) or fcols != list(FEATURE_COLS):
            return False
        return True

    @staticmethod
    def _verify_backup_sha256(bak_path: Path) -> Tuple[bool, str]:
        """
        バックアップファイル自身の SHA-256 を計算し、同ディレクトリに backup_manifest.json が存在する場合は
        記録された期待ハッシュ値と一致するかを照合する。
        """
        actual_hash = hashlib.sha256(bak_path.read_bytes()).hexdigest()
        manifest_path = bak_path.parent / "backup_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                expected_entry = manifest.get("files", {}).get("models/keiba_lgbm.joblib")
                if expected_entry and "sha256" in expected_entry:
                    if actual_hash != expected_entry["sha256"]:
                        return False, actual_hash
            except Exception:
                return False, actual_hash
        return True, actual_hash

    def restore_from_backup(self) -> bool:
        """
        モデル破損または欠損時に、先頭1,000行で不完全再学習する処理を廃止し、
        SHA-256 ハッシュ検証および特徴量仕様検証を通過した正常なバックアップからのみモデルを復元する。
        """
        for bak in self._find_backup_model_paths():
            try:
                hash_ok, sha256_hex = self._verify_backup_sha256(bak)
                if not hash_ok:
                    continue
                data = joblib.load(bak)
                if self._validate_loaded_model_dict(data):
                    self.model_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bak, self.model_path)
                    self.model = data["model"]
                    self.feature_cols = list(data["feature_cols"])
                    self.restored_from_sha256 = sha256_hex
                    return True
            except Exception:
                continue
        return False

    def load(self):
        if not self.model_path.exists():
            if self.restore_from_backup():
                return
            raise FileNotFoundError(f"Model file not found and no valid backup available: {self.model_path}")
        try:
            data = joblib.load(self.model_path)
            if not self._validate_loaded_model_dict(data):
                raise ValueError("Invalid model type or feature specification mismatch with FEATURE_COLS.")
            self.model = data["model"]
            self.feature_cols = list(data["feature_cols"])
        except Exception as e:
            if self.restore_from_backup():
                return
            raise RuntimeError(f"Model file corrupted or invalid ({e}) and backup restoration failed.") from e
