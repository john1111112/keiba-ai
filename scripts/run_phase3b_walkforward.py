"""
Phase 3B: 単勝1着確率予測モデルの構築・確率校正・整合性検証・厳密ウォークフォワード検証スクリプト
コピー環境で実行し、本番DB・既存モデル・本番キャッシュを変更しません。
"""
import sys
import json
import time
import shutil
import tempfile
import numpy as np
import pandas as pd
from pathlib import Path

# Add repo root to sys.path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from src.database import load_all_race_data, evaluate_race_completeness_and_export_csv
from src.features import build_features, FEATURE_COLS_V2_28
from src.model import KeibaAIModel, KeibaAIWinModel, MODEL_PATH
from src.calibration import (
    ProbabilityCalibrator,
    evaluate_probability_calibration,
    enforce_probability_consistency
)
import lightgbm as lgb


def run_phase3b_pipeline():
    print("=" * 70)
    print("競馬AI Phase 3B: 単勝1着確率モデル構築 & ウォークフォワード検証")
    print("=" * 70)

    tmp_dir = Path(tempfile.mkdtemp(prefix="keiba_phase3b_"))
    try:
        # 1. データのロードと完全レース判定
        print("\n[Step 1] SQLiteデータロード ＆ 5頭基準レース完全性判定...")
        df_raw = load_all_race_data()
        summary_df, incomplete_df = evaluate_race_completeness_and_export_csv(df_raw)
        complete_rids = set(summary_df.loc[summary_df["is_complete_for_eval"], "race_id"].astype(str))
        print(f"  総レース数: {df_raw['race_id'].nunique()}, 評価対象完全レース数: {len(complete_rids)}, 不完全除外レース数: {len(incomplete_df)}")

        # 2. 28特徴量の生成 (リークなし過去走参照)
        print("\n[Step 2] 28特徴量データ生成 (FEATURE_COLS_V2_28)...")
        df_features, feature_cols = build_features(df_raw, feature_version="v2_28cols_no_pedigree")
        df_features = df_features[df_features["race_id"].astype(str).isin(complete_rids)].copy()
        print(f"  完全レースレコード数: {len(df_features)}")

        # 3. 3着以内好走確率 (Top3) の計算（既存モデルとの比較・上限バウンド用）
        print("\n[Step 3] 既存3着以内モデルによる予測スコアの計算...")
        place_model = KeibaAIModel()
        try:
            place_model.load()
            has_place_model = True
        except Exception as e:
            print(f"  Note: 本番3着モデルロードスキップ ({e}) -> コピーモデルで学習")
            has_place_model = False

        if not has_place_model:
            place_model = KeibaAIModel(model_path=tmp_dir / "temp_place.joblib", feature_version="v2_28cols_no_pedigree")
            place_model.train(df_features, feature_cols=FEATURE_COLS_V2_28, save_model=True)

        # 4. ウォークフォワード検証（開催日単位・5分割 Block 0 ウォームアップ除外）
        print("\n[Step 4] 開催日境界ウォークフォワード検証 (5ブロック分割)...")
        clean_df, vstats = KeibaAIModel.validate_and_filter_training_data(df_features, target_type="win")
        print(f"  確定完走行数: {vstats['valid_finished_rows']}, 除外中止・取消等: {vstats['excluded_aborted_or_invalid_rows']}")

        clean_df["_dt"] = pd.to_datetime(clean_df["date"])
        unique_dates = np.sort(clean_df["_dt"].unique())
        n_blocks = 5
        block_size = len(unique_dates) // n_blocks
        boundary_dates = [unique_dates[i * block_size] for i in range(1, n_blocks)]

        # 記録用配列
        eval_indices = []
        raw_win_probs = []
        cal_sig_win_probs = []
        cal_iso_win_probs = []
        top3_probs = []
        y_trues = []
        odds_list = []
        race_ids = []
        dates_list = []
        horse_nums = []
        pops_list = []

        lgb_params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "boosting_type": "gbdt",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "max_depth": 6,
            "min_child_samples": 20,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "n_estimators": 150,
            "random_state": 42,
            "verbose": -1,
        }

        # OOF 予測キャッシュ（過去ブロックの予測値を蓄積し、校正器の学習に使用）
        historical_oof_probs = []
        historical_oof_trues = []

        for b in range(1, n_blocks):
            val_start_dt = boundary_dates[b - 1]
            if b < n_blocks - 1:
                val_end_dt = boundary_dates[b]
                val_mask = (clean_df["_dt"] >= val_start_dt) & (clean_df["_dt"] < val_end_dt)
            else:
                val_mask = (clean_df["_dt"] >= val_start_dt)

            tr_mask = (clean_df["_dt"] < val_start_dt)

            tr_max_date = clean_df.loc[tr_mask, "_dt"].max()
            val_min_date = clean_df.loc[val_mask, "_dt"].min()
            assert tr_max_date < val_min_date, f"Leakage detected: train max {tr_max_date} >= val min {val_min_date}"
            assert len(set(clean_df.loc[tr_mask, "_dt"]).intersection(set(clean_df.loc[val_mask, "_dt"]))) == 0

            X_tr = clean_df.loc[tr_mask, FEATURE_COLS_V2_28]
            y_tr = clean_df.loc[tr_mask, "target"]
            X_val = clean_df.loc[val_mask, FEATURE_COLS_V2_28]
            y_val = clean_df.loc[val_mask, "target"]

            # 1着予測モデルの学習
            model = lgb.LGBMClassifier(**lgb_params)
            model.fit(X_tr, y_tr)
            p_val_raw = model.predict_proba(X_val)[:, 1]

            # 3着以内モデルの予測確率
            p_top3 = place_model.model.predict_proba(clean_df.loc[val_mask, place_model.feature_cols])[:, 1]

            # 校正器の学習（Block 1 では過去 OOF が少ないため学習データの後半または過去OOFを使用）
            # 厳密性: 校正器は評価対象 (val_mask) より過去のデータだけで fit
            if len(historical_oof_probs) >= 1000:
                sig_cal = ProbabilityCalibrator(method="sigmoid").fit(np.array(historical_oof_probs), np.array(historical_oof_trues))
                iso_cal = ProbabilityCalibrator(method="isotonic").fit(np.array(historical_oof_probs), np.array(historical_oof_trues))
            else:
                # 初期学習データの一部（直近30%）を暫定校正用として使用（リークなし）
                tr_val_boundary = clean_df.loc[tr_mask, "_dt"].quantile(0.7)
                cal_tr_m = tr_mask & (clean_df["_dt"] < tr_val_boundary)
                cal_val_m = tr_mask & (clean_df["_dt"] >= tr_val_boundary)
                m_cal = lgb.LGBMClassifier(**lgb_params).fit(clean_df.loc[cal_tr_m, FEATURE_COLS_V2_28], clean_df.loc[cal_tr_m, "target"])
                p_cal_oof = m_cal.predict_proba(clean_df.loc[cal_val_m, FEATURE_COLS_V2_28])[:, 1]
                sig_cal = ProbabilityCalibrator(method="sigmoid").fit(p_cal_oof, clean_df.loc[cal_val_m, "target"].values)
                iso_cal = ProbabilityCalibrator(method="isotonic").fit(p_cal_oof, clean_df.loc[cal_val_m, "target"].values)

            p_val_sig = sig_cal.predict_proba(p_val_raw)
            p_val_iso = iso_cal.predict_proba(p_val_raw)

            # OOF の蓄積（次のブロックの校正器学習用）
            historical_oof_probs.extend(p_val_raw.tolist())
            historical_oof_trues.extend(y_val.tolist())

            eval_indices.extend(clean_df.loc[val_mask].index.tolist())
            raw_win_probs.extend(p_val_raw.tolist())
            cal_sig_win_probs.extend(p_val_sig.tolist())
            cal_iso_win_probs.extend(p_val_iso.tolist())
            top3_probs.extend(p_top3.tolist())
            y_trues.extend(y_val.tolist())
            odds_list.extend(clean_df.loc[val_mask, "odds"].tolist())
            race_ids.extend(clean_df.loc[val_mask, "race_id"].tolist())
            dates_list.extend(clean_df.loc[val_mask, "date"].tolist())
            horse_nums.extend(clean_df.loc[val_mask, "horse_number"].tolist())
            pops_list.extend(clean_df.loc[val_mask, "popularity"].tolist())

        df_eval = pd.DataFrame({
            "race_id": race_ids,
            "date": dates_list,
            "horse_number": horse_nums,
            "popularity": pops_list,
            "odds": odds_list,
            "target": y_trues,
            "p_win_raw": raw_win_probs,
            "p_win_sig": cal_sig_win_probs,
            "p_win_iso": cal_iso_win_probs,
            "p_top3": top3_probs,
        })
        df_eval["odds"] = pd.to_numeric(df_eval["odds"], errors="coerce").fillna(0.0)

        # 5. 確率整合性チェック（P(Win) <= P(Top3) & レース内合計1.0）
        print("\n[Step 5] 確率の整合性検証...")
        # (a) 未加工での P(Win) <= P(Top3) 違反数チェック
        violation_raw = (df_eval["p_win_raw"] > df_eval["p_top3"] + 1e-6).sum()
        print(f"  未加工での P(Win) > P(Top3) 違反頭数: {violation_raw} / {len(df_eval)} ({violation_raw/len(df_eval)*100:.2f}%)")

        # (b) 整合性制約適用: p_win = min(p_win, p_top3)
        df_eval["p_win_bounded"] = np.minimum(df_eval["p_win_sig"], df_eval["p_top3"])
        violation_after_bound = (df_eval["p_win_bounded"] > df_eval["p_top3"] + 1e-6).sum()
        print(f"  バウンド後の P(Win) > P(Top3) 違反頭数: {violation_after_bound} (完全解消)")

        # (c) レース内合計1.0正規化（ベクトル化処理）
        raw_sums = df_eval.groupby("race_id")["p_win_raw"].transform("sum")
        df_eval["p_win_norm_raw"] = np.where(raw_sums > 0, df_eval["p_win_raw"] / raw_sums, df_eval["p_win_raw"])

        sig_sums = df_eval.groupby("race_id")["p_win_bounded"].transform("sum")
        df_eval["p_win_norm_sig"] = np.where(sig_sums > 0, df_eval["p_win_bounded"] / sig_sums, df_eval["p_win_bounded"])

        race_sums = df_eval.groupby("race_id")["p_win_norm_sig"].sum()
        print(f"  各レースの1着確率合計: 平均 {race_sums.mean():.4f}, 最小 {race_sums.min():.4f}, 最大 {race_sums.max():.4f} (全レース原則1.0)")

        # 6. 確率校正の精度比較評価 (ROC-AUC, Log Loss, Brier Score)
        print("\n[Step 6] 予測確率校正の効果比較...")
        metrics_raw = evaluate_probability_calibration(df_eval["target"].values, df_eval["p_win_raw"].values)
        metrics_sig = evaluate_probability_calibration(df_eval["target"].values, df_eval["p_win_sig"].values)
        metrics_iso = evaluate_probability_calibration(df_eval["target"].values, df_eval["p_win_iso"].values)
        metrics_norm = evaluate_probability_calibration(df_eval["target"].values, df_eval["p_win_norm_sig"].values)

        print("\n  【確率予測モデル 精度比較表】")
        print(f"  {'モデル':<22} | {'ROC-AUC':<8} | {'Log Loss':<9} | {'Brier Score':<11}")
        print(f"  {'-'*22}-+-{'-'*8}-+-{'-'*9}-+-{'-'*11}")
        print(f"  {'1. 未校正 (Raw LGBM)':<20} | {metrics_raw['auc']:<8.4f} | {metrics_raw['log_loss']:<9.4f} | {metrics_raw['brier_score']:<11.4f}")
        print(f"  {'2. Sigmoid校正 (Platt)':<20} | {metrics_sig['auc']:<8.4f} | {metrics_sig['log_loss']:<9.4f} | {metrics_sig['brier_score']:<11.4f}")
        print(f"  {'3. Isotonic校正':<24} | {metrics_iso['auc']:<8.4f} | {metrics_iso['log_loss']:<9.4f} | {metrics_iso['brier_score']:<11.4f}")
        print(f"  {'4. 整合制約+レース正規化':<20} | {metrics_norm['auc']:<8.4f} | {metrics_norm['log_loss']:<9.4f} | {metrics_norm['brier_score']:<11.4f}")

        print("\n  【正規化後モデル 予測確率帯別 実際の勝率 (Calibration Table)】")
        print(f"  {'確率帯':<16} | {'サンプル数':<8} | {'平均予測確率':<12} | {'実際の勝率':<10} | {'誤差(予測-実績)':<12}")
        print(f"  {'-'*16}-+-{'-'*8}-+-{'-'*12}-+-{'-'*10}-+-{'-'*12}")
        for b in metrics_norm["bin_stats"]:
            print(f"  {b['range']:<16} | {b['count']:<8} | {b['mean_pred']:<12.4f} | {b['actual_win_rate']:<10.4f} | {b['diff']:<12.4f}")

        # 7. 単勝期待値の算出
        print("\n[Step 7] 単勝期待値の算出...")
        df_eval["win_ev"] = df_eval["p_win_norm_sig"] * df_eval["odds"]
        df_eval["expected_profit_margin"] = df_eval["win_ev"] - 1.0

        # 8. バックテスト & 戦略比較
        print("\n[Step 8] バックテスト ＆ 5大戦略パフォーマンス比較...")
        
        # レースごとの各戦略選択
        races_grouped = df_eval.groupby("race_id")
        
        # 1. 1番人気の単勝 (Favorite 1)
        fav1_bets = []
        # 2. AI予測1着確率1位 (AI top prob)
        ai_prob1_bets = []
        # 3. AI期待値1位 (AI top EV)
        ai_ev1_bets = []
        # 4. 期待値一定以上のみ購入 (EV >= 1.15 & prob >= 0.05)
        ev_filter_bets = []

        for rid, grp in races_grouped:
            # 1番人気
            valid_pops = grp[grp["popularity"] > 0]
            if len(valid_pops) > 0:
                fav1 = valid_pops.sort_values("popularity").iloc[0]
                fav1_bets.append(fav1)

            # AI確率1位
            top_prob = grp.sort_values("p_win_norm_sig", ascending=False).iloc[0]
            ai_prob1_bets.append(top_prob)

            # AI期待値1位
            top_ev = grp.sort_values("win_ev", ascending=False).iloc[0]
            ai_ev1_bets.append(top_ev)

            # 期待値フィルタ: EV >= 1.15 かつ p_win >= 0.05 の馬（オッズ過大ノイズを排除）
            qual_ev = grp[(grp["win_ev"] >= 1.15) & (grp["p_win_norm_sig"] >= 0.05)].sort_values("win_ev", ascending=False)
            if len(qual_ev) > 0:
                ev_filter_bets.append(qual_ev.iloc[0])

        def _calc_strategy_stats(bet_list, name="Strategy"):
            if not bet_list:
                return {
                    "name": name, "races": 0, "hits": 0, "hit_rate": 0.0,
                    "bet_amount": 0, "payout": 0, "profit": 0, "recovery_rate": 0.0,
                    "max_drawdown": 0
                }
            df_b = pd.DataFrame(bet_list).copy()
            df_b = df_b.sort_values("date").reset_index(drop=True)
            n_bets = len(df_b)
            hits = int(df_b["target"].sum())
            hit_rate = (hits / n_bets) * 100 if n_bets > 0 else 0.0
            
            # 各レース100円購入
            cost = n_bets * 100
            payout = int((df_b["target"] * df_b["odds"] * 100).sum())
            profit = payout - cost
            recovery = (payout / cost) * 100 if cost > 0 else 0.0

            # 最大ドローダウンの計算
            df_b["return"] = df_b["target"] * df_b["odds"] * 100 - 100
            cum_profit = df_b["return"].cumsum()
            running_max = cum_profit.cummax()
            drawdowns = running_max - cum_profit
            max_dd = int(drawdowns.max()) if len(drawdowns) > 0 else 0

            return {
                "name": name,
                "races": n_bets,
                "hits": hits,
                "hit_rate": round(hit_rate, 2),
                "bet_amount": cost,
                "payout": payout,
                "profit": profit,
                "recovery_rate": round(recovery, 2),
                "max_drawdown": max_dd,
                "df": df_b
            }

        res_fav1 = _calc_strategy_stats(fav1_bets, "1. 1番人気の単勝")
        res_prob1 = _calc_strategy_stats(ai_prob1_bets, "2. AI予測1着確率1位")
        res_ev1 = _calc_strategy_stats(ai_ev1_bets, "3. AI期待値1位の単勝")
        res_filter = _calc_strategy_stats(ev_filter_bets, "4. 期待値フィルタ(EV>=1.15&率>=5%)")
        res_nobet = {"name": "5. 全レース見送り", "races": 0, "hits": 0, "hit_rate": 0.0, "bet_amount": 0, "payout": 0, "profit": 0, "recovery_rate": 0.0, "max_drawdown": 0}

        print("\n  【5大戦略 パフォーマンス比較表】")
        print(f"  {'戦略名':<30} | {'購入鞍数':<7} | {'的中率':<7} | {'回収率':<8} | {'総損益':<10} | {'最大DD':<8}")
        print(f"  {'-'*30}-+-{'-'*7}-+-{'-'*7}-+-{'-'*8}-+-{'-'*10}-+-{'-'*8}")
        for r in [res_fav1, res_prob1, res_ev1, res_filter, res_nobet]:
            p_str = f"{r['profit']:+,}円"
            print(f"  {r['name']:<28} | {r['races']:<7} | {r['hit_rate']:>5.1f}% | {r['recovery_rate']:>6.1f}% | {p_str:>10} | -{r['max_drawdown']:,}円")

        # 期待値閾値別の回収率推移
        print("\n  【AI予測 期待値帯別の成績集計】")
        ev_bins = [
            ("全レース (EV無制限)", 0.0),
            ("EV >= 0.80", 0.80),
            ("EV >= 1.00 (期待値プラス)", 1.00),
            ("EV >= 1.10", 1.10),
            ("EV >= 1.20", 1.20),
            ("EV >= 1.30", 1.30),
            ("EV >= 1.50 (超高期待値)", 1.50),
        ]
        print(f"  {'期待値閾値':<24} | {'対象頭数':<8} | {'的中数':<6} | {'的中率':<7} | {'回収率':<8} | {'損益':<10}")
        print(f"  {'-'*24}-+-{'-'*8}-+-{'-'*6}-+-{'-'*7}-+-{'-'*8}-+-{'-'*10}")
        for label, threshold in ev_bins:
            sub = df_eval[df_eval["win_ev"] >= threshold]
            cnt = len(sub)
            if cnt > 0:
                h = int(sub["target"].sum())
                hr = (h / cnt) * 100
                cost = cnt * 100
                pay = int((sub["target"] * sub["odds"] * 100).sum())
                prof = pay - cost
                rec = (pay / cost) * 100
                print(f"  {label:<24} | {cnt:<8} | {h:<6} | {hr:>5.1f}% | {rec:>6.1f}% | {prof:>+10,}円")

        # オッズ帯別成績 (AI確率1位)
        print("\n  【AI確率1位 オッズ帯別成績】")
        df_prob1 = res_prob1["df"]
        odds_bins = [
            ("< 2.0倍", 0.0, 2.0),
            ("2.0 〜 2.9倍", 2.0, 3.0),
            ("3.0 〜 4.9倍", 3.0, 5.0),
            ("5.0 〜 9.9倍", 5.0, 10.0),
            ("10.0 〜 19.9倍", 10.0, 20.0),
            ("20.0倍以上", 20.0, 9999.0),
        ]
        print(f"  {'オッズ帯':<16} | {'購入鞍数':<7} | {'的中数':<6} | {'的中率':<7} | {'回収率':<8} | {'損益':<10}")
        print(f"  {'-'*16}-+-{'-'*7}-+-{'-'*6}-+-{'-'*7}-+-{'-'*8}-+-{'-'*10}")
        for o_label, o_min, o_max in odds_bins:
            sub_o = df_prob1[(df_prob1["odds"] >= o_min) & (df_prob1["odds"] < o_max)]
            c_o = len(sub_o)
            if c_o > 0:
                h_o = int(sub_o["target"].sum())
                hr_o = (h_o / c_o) * 100
                cost_o = c_o * 100
                pay_o = int((sub_o["target"] * sub_o["odds"] * 100).sum())
                prof_o = pay_o - cost_o
                rec_o = (pay_o / cost_o) * 100
                print(f"  {o_label:<16} | {c_o:<7} | {h_o:<6} | {hr_o:>5.1f}% | {rec_o:>6.1f}% | {prof_o:>+10,}円")

        # 月別成績 (AI確率1位)
        print("\n  【AI確率1位 月別成績】")
        df_prob1["month"] = df_prob1["date"].astype(str).str[:7]
        print(f"  {'年月':<10} | {'購入鞍数':<7} | {'的中数':<6} | {'的中率':<7} | {'回収率':<8} | {'損益':<10}")
        print(f"  {'-'*10}-+-{'-'*7}-+-{'-'*6}-+-{'-'*7}-+-{'-'*8}-+-{'-'*10}")
        for m_str, sub_m in df_prob1.groupby("month"):
            c_m = len(sub_m)
            h_m = int(sub_m["target"].sum())
            hr_m = (h_m / c_m) * 100
            cost_m = c_m * 100
            pay_m = int((sub_m["target"] * sub_m["odds"] * 100).sum())
            prof_m = pay_m - cost_m
            rec_m = (pay_m / cost_m) * 100
            print(f"  {m_str:<10} | {c_m:<7} | {h_m:<6} | {hr_m:>5.1f}% | {rec_m:>6.1f}% | {prof_m:>+10,}円")

        # 9. 新規1着モデルの保存 (models/keiba_lgbm_win_v2_28cols.joblib + .sha256)
        print("\n[Step 9] 1着確率予測モデルの保存...")
        win_model_out = repo_root / "models" / "keiba_lgbm_win_v2_28cols.joblib"
        win_model = KeibaAIWinModel(model_path=win_model_out)
        train_res = win_model.train(df_features, feature_cols=FEATURE_COLS_V2_28, save_model=True)
        print(f"  保存完了: {win_model_out}")
        print(f"  サイドカー: {win_model_out.with_suffix('.joblib.sha256')}")
        print(f"  モデル性能: AUC={train_res['auc']:.4f}, Accuracy={train_res['accuracy']:.4f}, Iterations={train_res['best_iteration']}")

        print("\n" + "=" * 70)
        print("Phase 3B パイプライン全ステップ完了")
        print("=" * 70)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_phase3b_pipeline()
