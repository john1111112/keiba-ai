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
from .features import FEATURE_COLS, FEATURE_COLS_V1_31, FEATURE_COLS_V2_28, get_feature_cols_by_version

# 既存の31特徴量本番モデルパス（保持・上書き禁止）
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "keiba_lgbm.joblib"
# 新しい28特徴量モデルパス（血統3特徴量を除外したv2モデル用・ファイルを明確に分離）
MODEL_V2_28_PATH = Path(__file__).resolve().parent.parent / "models" / "keiba_lgbm_v2_28cols.joblib"
# Phase 3B: 単勝1着確率予測モデルパス（ファイルを明確に分離・既存モデル上書き禁止）
MODEL_WIN_V2_28_PATH = Path(__file__).resolve().parent.parent / "models" / "keiba_lgbm_win_v2_28cols.joblib"
BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"

# 既知の信頼済み本番モデル SHA-256（Phase 1 バックアップで確定済み）
TRUSTED_V1_MODEL_SHA256 = "fc9757cb9220e5b6e2667a84f40eb750615472855fa4cf418d99460951f33eb1"

MIN_VALID_FINISH_ORDER = 1
MAX_VALID_FINISH_ORDER = 28


class KeibaAIModel:
    def __init__(self, model_path: Optional[Path] = None, feature_version: str = "v1_31cols"):
        self.feature_version = feature_version
        self.target_type = "place"
        if model_path is not None:
            self.model_path = Path(model_path)
        else:
            self.model_path = MODEL_V2_28_PATH if feature_version in ("v2_28cols", "v2_28cols_no_pedigree", "28") else MODEL_PATH
        self.model: Optional[lgb.LGBMClassifier] = None
        self.feature_cols: List[str] = get_feature_cols_by_version(feature_version)
        self.restored_from_sha256: Optional[str] = None

    @staticmethod
    def validate_and_filter_training_data(df_features: pd.DataFrame, target_type: str = "place") -> Tuple[pd.DataFrame, Dict[str, int]]:
        """
        【学習データの安全性検証・フィルタリング】
        1. 未確定レース（finish_order == 0）を学習対象から完全に除外する。
        2. 取消・除外・競走中止（finish_order > 28 または finish_order < 0 または NaN、または time_str が '' / '-'）を明確に分離・除外する。
        3. target_type に応じて目的変数を設定:
           - 'place': 1〜3着 = 1, 4着以下 = 0 (3着以内好走確率)
           - 'win': 1着 = 1, 2着以下 = 0 (単勝1着確率)
        4. 除外された件数の内訳を検証レポートとして返す。
        """
        if df_features is None or len(df_features) == 0:
            raise ValueError("学習データが空です。")
        if "finish_order" not in df_features.columns:
            raise ValueError("学習データに 'finish_order' カラムが存在しません。")

        total_rows = len(df_features)
        fo = pd.to_numeric(df_features["finish_order"], errors="coerce")
        if "time_str" in df_features.columns:
            t_str = df_features["time_str"].fillna("").astype(str).str.strip()
            non_finisher_time_mask = t_str.isin(["", "-"])
        else:
            non_finisher_time_mask = pd.Series([False] * total_rows, index=df_features.index)

        unconfirmed_mask = (fo == 0)
        aborted_or_invalid_mask = (~unconfirmed_mask) & (
            fo.isna() | (fo < MIN_VALID_FINISH_ORDER) | (fo > MAX_VALID_FINISH_ORDER) | non_finisher_time_mask
        )
        valid_mask = (fo >= MIN_VALID_FINISH_ORDER) & (fo <= MAX_VALID_FINISH_ORDER) & (~non_finisher_time_mask)

        clean_df = df_features.loc[valid_mask].copy()
        if "date" in clean_df.columns and "race_id" in clean_df.columns:
            clean_df = clean_df.sort_values(["date", "race_id"]).reset_index(drop=True)
        else:
            clean_df = clean_df.reset_index(drop=True)

        if target_type == "win":
            clean_df["target"] = (clean_df["finish_order"] == 1).astype(int)
        else:
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
        feature_cols: Optional[List[str]] = None,
        test_ratio: float = 0.2,
        save_model: bool = True
    ) -> Dict[str, Any]:
        """
        未確定・除外データを厳密に排除した上で、開催日単位の時系列境界（学習最大日付 < 評価最小日付）で分割してLightGBMを学習する。
        """
        if feature_cols is not None:
            self.feature_cols = list(feature_cols)
            if self.feature_cols == list(FEATURE_COLS_V2_28):
                self.feature_version = "v2_28cols_no_pedigree"
            elif self.feature_cols == list(FEATURE_COLS_V1_31):
                self.feature_version = "v1_31cols"
        else:
            self.feature_cols = get_feature_cols_by_version(self.feature_version)

        clean_df, validation_stats = self.validate_and_filter_training_data(df_features, target_type=self.target_type)

        # 開催日単位で時系列分割（同一開催日が学習と評価にまたがらないようにする）
        if "date" in clean_df.columns:
            clean_df["_dt"] = pd.to_datetime(clean_df["date"])
            unique_dates = np.sort(clean_df["_dt"].unique())
            if len(unique_dates) >= 2:
                raw_split_idx = int(len(clean_df) * (1.0 - test_ratio))
                boundary_date = clean_df["_dt"].iloc[raw_split_idx]
                train_df = clean_df[clean_df["_dt"] < boundary_date].copy()
                test_df = clean_df[clean_df["_dt"] >= boundary_date].copy()
                if len(train_df) == 0 or len(test_df) == 0:
                    boundary_date = unique_dates[-1]
                    train_df = clean_df[clean_df["_dt"] < boundary_date].copy()
                    test_df = clean_df[clean_df["_dt"] >= boundary_date].copy()
                assert train_df["_dt"].max() < test_df["_dt"].min(), "Train max date must be strictly < Test min date"
            else:
                split_idx = int(len(clean_df) * (1.0 - test_ratio))
                train_df = clean_df.iloc[:split_idx]
                test_df = clean_df.iloc[split_idx:]
        else:
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

        test_preds = self.model.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, test_preds)
        acc = accuracy_score(y_test, (test_preds >= 0.5).astype(int))

        if save_model:
            self.save()

        return {
            "feature_version": self.feature_version,
            "n_features": len(self.feature_cols),
            "auc": round(float(auc), 4),
            "accuracy": round(float(acc), 4),
            "train_samples": len(train_df),
            "test_samples": len(test_df),
            "best_iteration": self.model.best_iteration_,
            "validation_stats": validation_stats,
        }

    def predict_race(self, race_df: pd.DataFrame) -> pd.DataFrame:
        """
        出走馬データから3着以内予測確率（pred_score）と予想印を算出。
        【Phase 3A修正】
        - 3着以内確率 × 単勝オッズを単勝期待値として扱う誤りを撤廃。
        - 単勝期待値(win_expected_value)および複勝期待値(place_expected_value)は1着確率モデル・実複勝オッズが揃うまで算出不可(NaN)とし、
          expected_value カラムも算出停止（NaN）とする。
        - 期待値を根拠とした穴馬判定(is_ana)も一時停止(False)とする。
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

        # 単勝期待値・複勝期待値は算出不可（3着内確率×単勝オッズの誤計算を完全廃止）
        res_df["win_expected_value"] = np.nan
        res_df["place_expected_value"] = np.nan
        res_df["expected_value"] = np.nan

        res_df = res_df.sort_values("pred_score", ascending=False).reset_index(drop=True)

        marks = ["◎", "○", "▲", "△", "△", "☆"]
        mark_list = []
        for i in range(len(res_df)):
            if i < len(marks):
                mark_list.append(marks[i])
            else:
                mark_list.append("-")
        res_df["mark"] = mark_list

        # 期待値を根拠とした穴馬判定・購入推奨は一時停止
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
        payload = {
            "model": self.model,
            "feature_cols": list(self.feature_cols),
            "feature_version": self.feature_version,
            "target_type": getattr(self, "target_type", "place"),
            "n_features": len(self.feature_cols),
        }
        joblib.dump(payload, self.model_path)
        # 信頼検証用の SHA-256 サイドカーファイルを同時に出力
        sha256_hex = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        sidecar = self.model_path.with_suffix(self.model_path.suffix + ".sha256")
        sidecar.write_text(
            json.dumps({
                "sha256": sha256_hex,
                "feature_version": self.feature_version,
                "n_features": len(self.feature_cols),
                "feature_cols": list(self.feature_cols),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    @staticmethod
    def _validate_loaded_model_dict(data: Any) -> bool:
        """
        読み込んだモデルオブジェクトが正常な LGBMClassifier であり、
        特徴量リストが V1（31個）または V2（28個）の定義（順序含む）と完全一致するかを厳密検証する。
        """
        if not isinstance(data, dict):
            return False
        mdl = data.get("model")
        fcols = data.get("feature_cols")
        if not isinstance(mdl, lgb.LGBMClassifier):
            return False
        if not hasattr(mdl, "booster_") or mdl.booster_ is None:
            return False
        if not isinstance(fcols, list):
            return False
        if fcols not in (list(FEATURE_COLS_V1_31), list(FEATURE_COLS_V2_28)):
            return False
        return True

    @staticmethod
    def _verify_file_sha256_before_load(target_path: Path, require_verified_manifest: bool = True) -> Tuple[bool, str]:
        """
        【セキュリティ強化：デシリアライズ（joblib.load）前の厳密ハッシュ検証】
        1. デシリアライズを実行する前に必ずファイルの SHA-256 を計算する。
        2. 既知の信頼済み本番モデルハッシュ（TRUSTED_V1_MODEL_SHA256）、または同ディレクトリの `backup_manifest.json`、
           または `.sha256` サイドカーファイルと照合する。
        3. require_verified_manifest=True（バックアップ復元時など）において、検証済みハッシュ記録が存在しないファイルは無条件に拒否する。
        """
        if not target_path.exists():
            return False, ""
        actual_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()

        # 1. 既知の本番 V1 モデルハッシュと完全一致すれば信頼
        if actual_hash == TRUSTED_V1_MODEL_SHA256:
            return True, actual_hash

        # 2. backup_manifest.json が存在する場合の照合
        manifest_path = target_path.parent / "backup_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                expected_entry = manifest.get("files", {}).get("models/keiba_lgbm.joblib")
                if expected_entry and "sha256" in expected_entry:
                    return (actual_hash == expected_entry["sha256"]), actual_hash
            except Exception:
                return False, actual_hash

        # 3. .sha256 サイドカーファイルが存在する場合の照合
        sidecar = target_path.with_suffix(target_path.suffix + ".sha256")
        if sidecar.exists():
            try:
                sc_data = json.loads(sidecar.read_text(encoding="utf-8"))
                if "sha256" in sc_data:
                    return (actual_hash == sc_data["sha256"]), actual_hash
            except Exception:
                return False, actual_hash

        # 検証済みハッシュが存在しない場合は無条件に信用しない
        if require_verified_manifest:
            return False, actual_hash
        return True, actual_hash

    def _find_backup_model_paths(self) -> List[Path]:
        """検証済みハッシュを持つバックアップモデルを新しい順に探索する"""
        candidates = []
        if BACKUP_DIR.exists():
            for p in sorted(BACKUP_DIR.rglob("keiba_lgbm.joblib"), key=lambda x: x.stat().st_mtime, reverse=True):
                candidates.append(p)
        adj_bak = self.model_path.with_suffix(".joblib.bak")
        if adj_bak.exists():
            candidates.append(adj_bak)
        return candidates

    def restore_from_backup(self) -> bool:
        """
        モデル破損または欠損時に、デシリアライズ前に SHA-256 ハッシュが検証済みマニフェストと一致する
        信頼できるバックアップからのみモデルを復元する（未検証バックアップは無条件に拒否）。
        """
        for bak in self._find_backup_model_paths():
            try:
                # デシリアライズ前に必ず SHA-256 を検証（検証済みハッシュ記録がなければ拒否）
                hash_ok, sha256_hex = self._verify_file_sha256_before_load(bak, require_verified_manifest=True)
                if not hash_ok:
                    continue
                data = joblib.load(bak)
                if self._validate_loaded_model_dict(data):
                    self.model_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bak, self.model_path)
                    self.model = data["model"]
                    self.feature_cols = list(data["feature_cols"])
                    self.feature_version = data.get(
                        "feature_version",
                        "v2_28cols_no_pedigree" if len(self.feature_cols) == 28 else "v1_31cols"
                    )
                    self.restored_from_sha256 = sha256_hex
                    return True
            except Exception:
                continue
        return False

    def load(self):
        if not self.model_path.exists():
            if self.restore_from_backup():
                return
            raise FileNotFoundError(f"Model file not found and no verified backup available: {self.model_path}")
        try:
            # デシリアライズ前に SHA-256 ハッシュ検証を実行
            hash_ok, _ = self._verify_file_sha256_before_load(self.model_path, require_verified_manifest=True)
            if not hash_ok:
                raise SecurityError("Model file SHA-256 hash is not verified by trusted manifest or sidecar.")
            data = joblib.load(self.model_path)
            if not self._validate_loaded_model_dict(data):
                raise ValueError("Invalid model type or feature specification mismatch.")
            self.model = data["model"]
            self.feature_cols = list(data["feature_cols"])
            self.feature_version = data.get(
                "feature_version",
                "v2_28cols_no_pedigree" if len(self.feature_cols) == 28 else "v1_31cols"
            )
        except Exception as e:
            if self.restore_from_backup():
                return
            raise RuntimeError(f"Model file corrupted, untrusted, or invalid ({e}) and backup restoration failed.") from e


class SecurityError(RuntimeError):
    pass


class KeibaAIWinModel(KeibaAIModel):
    """
    【Phase 3B: 単勝1着確率予測モデル】
    - 目的変数: 1着 = 1, 2着以下 = 0
    - 特徴量: 28特徴量版 (FEATURE_COLS_V2_28)
    - 確率整合性: P(Win) <= P(Top3) 制約、レース内完走馬間合計1.0正規化
    - 単勝期待値: 期待回収倍率 (p_win * odds) および 期待利益率 (p_win * odds - 1) の算出
    """
    def __init__(self, model_path: Optional[Path] = None, feature_version: str = "v2_28cols_win_model"):
        p = model_path or MODEL_WIN_V2_28_PATH
        super().__init__(model_path=p, feature_version="v2_28cols_no_pedigree")
        self.target_type = "win"
        self.feature_version = feature_version

    def predict_win_race(
        self,
        race_df: pd.DataFrame,
        place_model: Optional[KeibaAIModel] = None,
        calibrator = None,
        normalize_to_one: bool = True
    ) -> pd.DataFrame:
        """
        レース単位で各馬の1着確率・3着内確率との整合性制約・正規化・単勝期待値を算出する。
        """
        if self.model is None:
            self.load()

        res_df = race_df.copy()
        for c in self.feature_cols:
            if c not in res_df.columns:
                res_df[c] = 0.0

        X = res_df[self.feature_cols]
        raw_win_probs = self.model.predict_proba(X)[:, 1]

        # 確率校正 (Calibrator が与えられている場合)
        if calibrator is not None and hasattr(calibrator, "predict_proba"):
            cal_win_probs = calibrator.predict_proba(raw_win_probs)
        else:
            cal_win_probs = raw_win_probs.copy()

        res_df["pred_win_prob_raw"] = raw_win_probs
        res_df["pred_win_prob"] = cal_win_probs

        # 3着以内確率との整合性 (P(Win) <= P(Top3))
        if "pred_score" in res_df.columns:
            p_top3 = res_df["pred_score"].clip(0.0, 1.0)
            res_df["pred_win_prob"] = np.minimum(res_df["pred_win_prob"], p_top3)
        elif place_model is not None:
            pred_place_df = place_model.predict_race(race_df)
            p_top3 = pred_place_df["pred_score"].clip(0.0, 1.0)
            res_df["pred_place_prob"] = p_top3
            res_df["pred_win_prob"] = np.minimum(res_df["pred_win_prob"], p_top3)

        # 取消・除外馬の除外と確率0化
        if "time_str" in res_df.columns:
            valid_mask = (res_df["finish_order"].fillna(0) >= 0) & (~res_df["time_str"].fillna("").astype(str).str.strip().isin(["", "-"]))
        else:
            valid_mask = (res_df["finish_order"].fillna(0) >= 0)
        unconfirmed = (res_df["finish_order"].fillna(0) == 0)
        effective_runners = valid_mask | unconfirmed
        res_df.loc[~effective_runners, "pred_win_prob"] = 0.0

        # レース内合計1.0の正規化
        if normalize_to_one:
            runner_sum = res_df.loc[effective_runners, "pred_win_prob"].sum()
            if runner_sum > 0:
                res_df.loc[effective_runners, "pred_win_prob"] = res_df.loc[effective_runners, "pred_win_prob"] / runner_sum

        # 単勝期待値の計算
        # 単勝期待回収倍率 = 予測1着確率 × 単勝オッズ
        # 単勝期待利益率 = 単勝期待回収倍率 - 1.0
        if "odds" in res_df.columns:
            odds_col = pd.to_numeric(res_df["odds"], errors="coerce").fillna(0.0)
        else:
            odds_col = pd.Series(0.0, index=res_df.index)
        res_df["win_expected_value"] = res_df["pred_win_prob"] * odds_col
        res_df["expected_profit_margin"] = res_df["win_expected_value"] - 1.0
        res_df["expected_value"] = res_df["win_expected_value"]

        # 単勝確率降順にソート
        res_df = res_df.sort_values("pred_win_prob", ascending=False).reset_index(drop=True)
        return res_df
