"""
Phase 3C: 市場オッズ基準モデル・AI統合モデル・確率校正・厳密ウォークフォワード検証スクリプト
- レース件数相互排他整合性の完全検証 (2987 + 115 + 72 = 3174)
- 市場確率モデル (1/odds_i の正規化) のベンチマーク算出
- AI独自予測モデル (有界射影方式) の算出
- 過去データのみを用いたAI+市場確率最適ブレンドモデルの構築（リークゼロ）
- 大穴馬（100倍以上等）のオッズ帯別確率校正効果の検証
- 5大戦略厳格バックテスト（確定オッズ基準参考分析・最大DD起点0円）
"""
import sys
import json
import time
import shutil
import tempfile
import numpy as np
import pandas as pd
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from src.database import load_all_race_data, evaluate_race_completeness_and_export_csv
from src.features import build_features, FEATURE_COLS_V2_28
from src.model import KeibaAIModel, KeibaAIWinModel
from src.calibration import (
    ProbabilityCalibrator,
    evaluate_probability_calibration,
    project_win_probabilities_with_top3_bounds,
    compute_winner_log_loss
)
from src.market import (
    compute_market_probabilities,
    blend_probabilities,
    find_optimal_blend_weight
)
import lightgbm as lgb


def run_phase3c_pipeline():
    print("=" * 80)
    print("競馬AI Phase 3C: 市場オッズ基準モデル ＆ AI統合モデル 厳密検証")
    print("=" * 80)

    tmp_dir = Path(tempfile.mkdtemp(prefix="keiba_phase3c_"))
    try:
        # ---------------------------------------------------------
        # [Step 1] レース件数の整合性確認【最優先】
        # ---------------------------------------------------------
        print("\n[Step 1] レース件数の相互排他性・全件数整合性検証...")
        df_raw = load_all_race_data()
        summary_df, inc_df = evaluate_race_completeness_and_export_csv(df_raw)

        total_r = df_raw["race_id"].nunique()
        counts = summary_df["status"].value_counts().to_dict()
        n_comp = counts.get("PAST_CONFIRMED_COMPLETE", 0)
        n_incomp = counts.get("PAST_INCOMPLETE_EXTRACT", 0)
        n_up = counts.get("UPCOMING_UNCONFIRMED", 0)

        print(f"  DB総レース数: {total_r} 件")
        print(f"  ・過去完全確定レース (PAST_CONFIRMED_COMPLETE) : {n_comp:>5} 件")
        print(f"  ・過去抜粋不完全レース (PAST_INCOMPLETE_EXTRACT): {n_incomp:>5} 件")
        print(f"  ・発走前未確定レース (UPCOMING_UNCONFIRMED)     : {n_up:>5} 件")
        print(f"  ・合算合計                                     : {n_comp + n_incomp + n_up:>5} 件")

        assert n_comp == 2987, f"過去完全レース数不一致: {n_comp} != 2987"
        assert n_incomp == 115, f"不完全レース数不一致: {n_incomp} != 115"
        assert n_up == 72, f"未確定レース数不一致: {n_up} != 72"
        assert (n_comp + n_incomp + n_up) == total_r == 3174, "全件数不整合"
        print("  => 全分類が相互排他的であり、2987 + 115 + 72 = 3174 件と完全一致しました（検証合格）。")

        # ---------------------------------------------------------
        # [Step 2] 特徴量生成 (完全レースのみから騎手・血統累積統計を集計)
        # ---------------------------------------------------------
        print("\n[Step 2] 28特徴量データ生成（不完全レースを騎手・血統統計から完全分離）...")
        complete_rids = set(summary_df[summary_df["status"] == "PAST_CONFIRMED_COMPLETE"]["race_id"].astype(str))
        df_features, feature_cols = build_features(
            df_raw,
            feature_version="v2_28cols_no_pedigree",
            complete_race_ids=complete_rids
        )
        # 学習・評価は過去完全確定レースのみを対象とする
        df_features = df_features[df_features["race_id"].astype(str).isin(complete_rids)].copy()
        print(f"  過去完全確定レース出走レコード数: {len(df_features)} 行")

        # ---------------------------------------------------------
        # [Step 3] 厳密ウォークフォワード検証 (5ブロック分割・リークゼロ)
        # ---------------------------------------------------------
        print("\n[Step 3] 厳密ウォークフォワード検証 (5ブロック分割)...")
        clean_df, vstats = KeibaAIModel.validate_and_filter_training_data(df_features, target_type="win")

        clean_df["_dt"] = pd.to_datetime(clean_df["date"])
        unique_dates = np.sort(clean_df["_dt"].unique())
        n_blocks = 5
        block_size = len(unique_dates) // n_blocks
        boundary_dates = [unique_dates[i * block_size] for i in range(1, n_blocks)]

        lgb_win_params = {
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

        lgb_place_params = {
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

        eval_dfs = []
        hist_oof_win_raw = []
        hist_oof_market = []
        hist_oof_y = []

        block_weights = []

        for b in range(1, n_blocks):
            val_start_dt = boundary_dates[b - 1]
            if b < n_blocks - 1:
                val_end_dt = boundary_dates[b]
                val_mask = (clean_df["_dt"] >= val_start_dt) & (clean_df["_dt"] < val_end_dt)
            else:
                val_mask = (clean_df["_dt"] >= val_start_dt)

            tr_mask = (clean_df["_dt"] < val_start_dt)

            tr_max_d = str(clean_df.loc[tr_mask, "_dt"].max())[:10]
            val_min_d = str(clean_df.loc[val_mask, "_dt"].min())[:10]
            val_max_d = str(clean_df.loc[val_mask, "_dt"].max())[:10]
            assert tr_max_d < val_min_d

            # 1. 1着モデル・3着モデル学習（過去データのみ）
            X_tr = clean_df.loc[tr_mask, FEATURE_COLS_V2_28]
            y_tr_win = (clean_df.loc[tr_mask, "finish_order"] == 1).astype(int)
            y_tr_place = ((clean_df.loc[tr_mask, "finish_order"] >= 1) & (clean_df.loc[tr_mask, "finish_order"] <= 3)).astype(int)

            X_val = clean_df.loc[val_mask, FEATURE_COLS_V2_28]
            y_val_win = (clean_df.loc[val_mask, "finish_order"] == 1).astype(int)

            win_m = lgb.LGBMClassifier(**lgb_win_params).fit(X_tr, y_tr_win)
            place_m = lgb.LGBMClassifier(**lgb_place_params).fit(X_tr, y_tr_place)

            p_val_win_raw = win_m.predict_proba(X_val)[:, 1]
            p_val_place = place_m.predict_proba(X_val)[:, 1]

            # 2. 確率校正器の学習（過去データのみ）
            if len(hist_oof_win_raw) >= 1000:
                sig_cal = ProbabilityCalibrator(method="sigmoid").fit(
                    np.array(hist_oof_win_raw), np.array(hist_oof_y)
                )
            else:
                cal_split_dt = clean_df.loc[tr_mask, "_dt"].quantile(0.7)
                cal_tr_m = tr_mask & (clean_df["_dt"] < cal_split_dt)
                cal_val_m = tr_mask & (clean_df["_dt"] >= cal_split_dt)
                m_init = lgb.LGBMClassifier(**lgb_win_params).fit(
                    clean_df.loc[cal_tr_m, FEATURE_COLS_V2_28], (clean_df.loc[cal_tr_m, "finish_order"] == 1).astype(int)
                )
                p_cal_oof = m_init.predict_proba(clean_df.loc[cal_val_m, FEATURE_COLS_V2_28])[:, 1]
                y_cal_oof = (clean_df.loc[cal_val_m, "finish_order"] == 1).astype(int).values
                sig_cal = ProbabilityCalibrator(method="sigmoid").fit(p_cal_oof, y_cal_oof)

            p_val_win_sig = sig_cal.predict_proba(p_val_win_raw)

            sub_b = clean_df.loc[val_mask, ["race_id", "date", "horse_number", "popularity", "odds", "finish_order"]].copy()
            sub_b["target"] = y_val_win.values
            sub_b["p_win_raw"] = p_val_win_raw
            sub_b["p_win_sig"] = p_val_win_sig
            sub_b["p_place"] = p_val_place

            # 3. 市場確率の計算 (q_i = 1/odds_i の正規化)
            sub_b = compute_market_probabilities(sub_b, odds_col="odds", race_id_col="race_id", out_col="p_market")

            # 4. AI予測の有界射影正規化（P(Win) <= P(Top3) & sum = 1.0）
            proj_list = []
            for rid, grp in sub_b.groupby("race_id", sort=False):
                pw = grp["p_win_sig"].values
                pp = grp["p_place"].values
                proj_w = project_win_probabilities_with_top3_bounds(pw, pp, target_sum=1.0, strict=True)
                proj_list.extend(proj_w.tolist())
            sub_b["p_win_ai"] = proj_list

            # 5. 最適ブレンド重み w* の決定（過去データのみを使用・未来リーク完全禁止）
            if len(hist_oof_win_raw) >= 1000:
                best_w = find_optimal_blend_weight(
                    np.array(hist_oof_y),
                    np.array(hist_oof_win_raw),
                    np.array(hist_oof_market),
                    metric="log_loss"
                )
            else:
                # 初期ブロックは保守的重み w=0.20 (市場確率80% + AI独自20%)
                best_w = 0.20

            block_weights.append((b, tr_max_d, val_min_d, best_w))
            print(f"  ・Block {b}: 学習終了日={tr_max_d} | 評価期間={val_min_d}〜{val_max_d} | 過去OOF決定ブレンド重み w*={best_w:.2f}")

            # ブレンド確率の計算
            sub_b = blend_probabilities(sub_b, p_ai_col="p_win_ai", p_market_col="p_market", weight_ai=best_w, out_col="p_blend")

            # OOF の蓄積（次ブロックの重み探索・校正用）
            hist_oof_win_raw.extend(p_val_win_raw.tolist())
            hist_oof_market.extend(sub_b["p_market"].tolist())
            hist_oof_y.extend(y_val_win.tolist())

            eval_dfs.append(sub_b)

        df_eval = pd.concat(eval_dfs, ignore_index=True)
        df_eval["odds"] = pd.to_numeric(df_eval["odds"], errors="coerce").fillna(0.0)

        # ---------------------------------------------------------
        # [Step 4] 市場確率 vs AI独自 vs AI+市場統合 3方式の総合比較
        # ---------------------------------------------------------
        print("\n[Step 4] 予測モデル3方式（市場確率 / AI独自 / AI+市場統合）総合比較...")
        models_to_compare = [
            ("A. 市場確率モデル (1/odds_i 正規化)", "p_market"),
            ("B. AI独自予測モデル (有界射影)", "p_win_ai"),
            ("C. AI＋市場統合ブレンドモデル (新方式)", "p_blend"),
        ]

        print(f"\n  {'モデル方式':<36} | {'ROC-AUC':<8} | {'Log Loss':<9} | {'Brier':<8} | {'勝者対数損失':<12} | {'ECE(校正誤差)':<12}")
        print(f"  {'-'*36}-+-{'-'*8}-+-{'-'*9}-+-{'-'*8}-+-{'-'*12}-+-{'-'*12}")
        for m_name, col in models_to_compare:
            metrics = evaluate_probability_calibration(df_eval["target"].values, df_eval[col].values)
            w_loss = compute_winner_log_loss(df_eval, col)
            print(f"  {m_name:<34} | {metrics['auc']:<8.4f} | {metrics['log_loss']:<9.4f} | {metrics['brier_score']:<8.4f} | {w_loss:<12.4f} | {metrics['ece']:<12.4f}")

        # ---------------------------------------------------------
        # [Step 5] オッズ帯別の確率校正効果（大穴馬の確率膨張解消の検証）
        # ---------------------------------------------------------
        print("\n[Step 5] オッズ帯別の予測確率・実勝率・校正誤差の比較（100倍以上の大穴馬検証）...")
        odds_bins = [
            ("1.0 〜 2.9倍", 1.0, 3.0),
            ("3.0 〜 9.9倍", 3.0, 10.0),
            ("10.0 〜 29.9倍", 10.0, 30.0),
            ("30.0 〜 49.9倍", 30.0, 50.0),
            ("50.0 〜 99.9倍", 50.0, 100.0),
            ("100.0倍以上", 100.0, 9999.0),
        ]

        print(f"\n  {'オッズ帯':<15} | {'頭数':<6} | {'的中数':<5} | {'実勝率':<8} | {'市場勝率':<8} | {'AI独自勝率':<10} | {'AI+市場統合勝率':<14} | {'統合モデル校正誤差':<16}")
        print(f"  {'-'*15}-+-{'-'*6}-+-{'-'*5}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*14}-+-{'-'*16}")
        for o_label, o_min, o_max in odds_bins:
            sub_o = df_eval[(df_eval["odds"] >= o_min) & (df_eval["odds"] < o_max)]
            c_o = len(sub_o)
            if c_o > 0:
                h_o = int(sub_o["target"].sum())
                act_w = (h_o / c_o) * 100
                mkt_p = sub_o["p_market"].mean() * 100
                ai_p = sub_o["p_win_ai"].mean() * 100
                blend_p = sub_o["p_blend"].mean() * 100
                blend_err = abs(blend_p - act_w)
                print(f"  {o_label:<15} | {c_o:<6} | {h_o:<5} | {act_w:>6.2f}% | {mkt_p:>6.2f}% | {ai_p:>8.2f}% | {blend_p:>12.2f}% | {blend_err:>14.2f}%")

        # ---------------------------------------------------------
        # [Step 6] 期待値分布の改善検証
        # ---------------------------------------------------------
        print("\n[Step 6] 期待値分布の検証（架空EVの暴走解消確認）...")
        df_eval["ev_ai"] = df_eval["p_win_ai"] * df_eval["odds"]
        df_eval["ev_market"] = df_eval["p_market"] * df_eval["odds"]
        df_eval["ev_blend"] = df_eval["p_blend"] * df_eval["odds"]

        print(f"  ・AI独自モデル    : EV >= 1.0 が {(df_eval['ev_ai'] >= 1.0).sum():>5} 頭 ({(df_eval['ev_ai'] >= 1.0).mean()*100:.1f}%), EV >= 1.5 が {(df_eval['ev_ai'] >= 1.5).sum():>5} 頭 ({(df_eval['ev_ai'] >= 1.5).mean()*100:.1f}%)")
        print(f"  ・市場確率モデル  : EV >= 1.0 が {(df_eval['ev_market'] >= 1.0).sum():>5} 頭 ({(df_eval['ev_market'] >= 1.0).mean()*100:.1f}%), EV >= 1.5 が {(df_eval['ev_market'] >= 1.5).sum():>5} 頭 ({(df_eval['ev_market'] >= 1.5).mean()*100:.1f}%)")
        print(f"  ・AI+市場統合モデル: EV >= 1.0 が {(df_eval['ev_blend'] >= 1.0).sum():>5} 頭 ({(df_eval['ev_blend'] >= 1.0).mean()*100:.1f}%), EV >= 1.5 が {(df_eval['ev_blend'] >= 1.5).sum():>5} 頭 ({(df_eval['ev_blend'] >= 1.5).mean()*100:.1f}%)")

        # ---------------------------------------------------------
        # [Step 7] バックテスト（全頭購入と1R1頭の分離・見送り除外・最大DD起点0円）
        # ---------------------------------------------------------
        print("\n[Step 7] 厳密バックテスト（確定オッズ基準参考分析）...")
        races_grouped = df_eval.groupby("race_id")
        total_races = len(races_grouped)

        # 1. 1番人気の単勝 (1R1頭)
        fav1_bets = []
        # 2. 市場確率1位 (1R1頭)
        market1_bets = []
        # 3. AI独自確率1位 (1R1頭)
        ai1_bets = []
        # 4. AI+市場統合ブレンド確率1位 (1R1頭)
        blend1_bets = []
        # 5. 厳選投資（学習期間のみで決定した閾値: Blend EV >= 1.05 & Blend率 >= 15% & 2〜15倍）
        filter_bets = []

        for rid, grp in races_grouped:
            valid_pops = grp[grp["popularity"] > 0]
            if len(valid_pops) > 0:
                fav1_bets.append(valid_pops.sort_values("popularity").iloc[0])

            market1_bets.append(grp.sort_values("p_market", ascending=False).iloc[0])
            ai1_bets.append(grp.sort_values("p_win_ai", ascending=False).iloc[0])
            blend1_bets.append(grp.sort_values("p_blend", ascending=False).iloc[0])

            cands = grp[(grp["ev_blend"] >= 1.05) & (grp["p_blend"] >= 0.15) & (grp["odds"] >= 2.0) & (grp["odds"] <= 15.0)]
            if len(cands) > 0:
                filter_bets.append(cands.sort_values("ev_blend", ascending=False).iloc[0])

        def _calc_stats(bet_list, name="Strategy"):
            if not bet_list:
                return {
                    "name": name, "races": 0, "skip": total_races, "hits": 0, "hit_rate": 0.0,
                    "cost": 0, "payout": 0, "profit": 0, "recovery": 0.0, "max_dd": 0, "df": pd.DataFrame()
                }
            df_b = pd.DataFrame(bet_list).copy().sort_values(["date", "race_id"]).reset_index(drop=True)
            n_bets = len(df_b)
            hits = int(df_b["target"].sum())
            hit_rate = (hits / n_bets) * 100 if n_bets > 0 else 0.0

            cost = n_bets * 100
            payout = int((df_b["target"] * df_b["odds"] * 100).sum())
            profit = payout - cost
            recovery = (payout / cost) * 100 if cost > 0 else 0.0

            # 最大ドローダウン（起点0円）
            cum_profit = (df_b["target"] * df_b["odds"] * 100 - 100).cumsum()
            running_peak = np.maximum(0, cum_profit.cummax())
            drawdowns = running_peak - cum_profit
            max_dd = int(drawdowns.max()) if len(drawdowns) > 0 else 0

            return {
                "name": name,
                "races": n_bets,
                "skip": total_races - n_bets,
                "hits": hits,
                "hit_rate": round(hit_rate, 2),
                "cost": cost,
                "payout": payout,
                "profit": profit,
                "recovery": round(recovery, 2),
                "max_dd": max_dd,
                "df": df_b
            }

        res_fav1 = _calc_stats(fav1_bets, "1. 1番人気の単勝 (1R1頭)")
        res_mkt1 = _calc_stats(market1_bets, "2. 市場確率1位の単勝 (1R1頭)")
        res_ai1 = _calc_stats(ai1_bets, "3. AI独自予測1位の単勝 (1R1頭)")
        res_bld1 = _calc_stats(blend1_bets, "4. AI+市場統合ブレンド1位 (1R1頭)")
        res_filt = _calc_stats(filter_bets, "5. 厳選投資 (Blend EV>=1.05 & 率>=15%)")
        res_nobet = _calc_stats([], "6. 全レース見送り (投資ゼロ)")

        print(f"\n  {'戦略名':<36} | {'購入鞍数':<7} | {'見送り':<6} | {'的中率':<7} | {'回収率':<8} | {'総損益':<11} | {'最大DD':<9}")
        print(f"  {'-'*36}-+-{'-'*7}-+-{'-'*6}-+-{'-'*7}-+-{'-'*8}-+-{'-'*11}-+-{'-'*9}")
        for r in [res_fav1, res_mkt1, res_ai1, res_bld1, res_filt, res_nobet]:
            p_str = f"{r['profit']:+,}円"
            print(f"  {r['name']:<34} | {r['races']:<7} | {r['skip']:<6} | {r['hit_rate']:>5.1f}% | {r['recovery']:>6.1f}% | {p_str:>11} | -{r['max_dd']:,}円")

        # 月別成績 (AI+市場統合ブレンド1位)
        print("\n  【AI+市場統合ブレンド1位 月別成績】")
        df_bld = res_bld1["df"]
        df_bld["month"] = df_bld["date"].astype(str).str[:7]
        print(f"  {'年月':<10} | {'購入鞍数':<7} | {'的中数':<6} | {'的中率':<7} | {'回収率':<8} | {'損益':<10}")
        print(f"  {'-'*10}-+-{'-'*7}-+-{'-'*6}-+-{'-'*7}-+-{'-'*8}-+-{'-'*10}")
        for m_str, sub_m in df_bld.groupby("month"):
            c_m = len(sub_m)
            h_m = int(sub_m["target"].sum())
            hr_m = (h_m / c_m) * 100
            cost_m = c_m * 100
            pay_m = int((sub_m["target"] * sub_m["odds"] * 100).sum())
            prof_m = pay_m - cost_m
            rec_m = (pay_m / cost_m) * 100
            print(f"  {m_str:<10} | {c_m:<7} | {h_m:<6} | {hr_m:>5.1f}% | {rec_m:>6.1f}% | {prof_m:>+10,}円")

        # ---------------------------------------------------------
        # [Step 8] 新モデルの保存
        # ---------------------------------------------------------
        print("\n[Step 8] Phase 3C 統合モデルの保存...")
        win_model_out = repo_root / "models" / "keiba_lgbm_win_v2_28cols.joblib"
        win_model = KeibaAIWinModel(model_path=win_model_out)
        train_res = win_model.train(df_features, feature_cols=FEATURE_COLS_V2_28, save_model=True)
        print(f"  保存完了: {win_model_out}")
        print(f"  サイドカー: {win_model_out.with_suffix('.joblib.sha256')}")

        print("\n" + "=" * 80)
        print("Phase 3C 全検証工程完了")
        print("=" * 80)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_phase3c_pipeline()
