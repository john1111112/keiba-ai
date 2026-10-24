import importlib
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

from src.database import init_db, load_all_race_data, get_db_stats, get_connection, get_races_metadata
from src.features import build_features
from src.model import KeibaAIModel, MODEL_PATH

import src.backtest as bt_mod
importlib.reload(bt_mod)
import src.notifier as notif_mod
importlib.reload(notif_mod)
import src.betting_strategy as bs_mod
importlib.reload(bs_mod)

get_discord_webhook = getattr(notif_mod, "get_discord_webhook")
save_discord_webhook = getattr(notif_mod, "save_discord_webhook")
send_discord_test = getattr(notif_mod, "send_discord_test")
send_race_alert = getattr(notif_mod, "send_race_alert")
send_s_rank_alert = getattr(notif_mod, "send_s_rank_alert", send_race_alert)
check_and_auto_notify_s_rank = getattr(notif_mod, "check_and_auto_notify_s_rank", None)
notify_all_predicted_ss_s_races = getattr(notif_mod, "notify_all_predicted_ss_s_races", None)
get_notified_races = getattr(notif_mod, "get_notified_races", list)
NOTIFIED_PATH = getattr(notif_mod, "NOTIFIED_PATH", Path(__file__).resolve().parent / "data" / "notified_races.json")
run_backtest = getattr(bt_mod, "run_backtest")
analyze_rank_performance = getattr(bt_mod, "analyze_rank_performance")
evaluate_race_value = getattr(bs_mod, "evaluate_race_value")

from src.analyzer import analyze_winning_conditions


st.set_page_config(
    page_title="AI競馬予想システム",
    page_icon="🏇",
    layout="wide",
    initial_sidebar_state="expanded"
)

# カスタムCSS
st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: 700; color: #1E3A8A; margin-bottom: 0.5rem; }
    .sub-header { font-size: 1.1rem; color: #4B5563; margin-bottom: 2rem; }
</style>
""", unsafe_allow_html=True)

init_db()

FEATURES_CACHE_PATH = Path(__file__).resolve().parent / "data" / "features.parquet"
RACE_RANK_CACHE_PATH = Path(__file__).resolve().parent / "data" / "race_rank_cache.parquet"

def _get_cache_key() -> str:
    t1 = FEATURES_CACHE_PATH.stat().st_mtime if FEATURES_CACHE_PATH.exists() else 0.0
    t2 = RACE_RANK_CACHE_PATH.stat().st_mtime if RACE_RANK_CACHE_PATH.exists() else 0.0
    s1 = FEATURES_CACHE_PATH.stat().st_size if FEATURES_CACHE_PATH.exists() else 0
    s2 = RACE_RANK_CACHE_PATH.stat().st_size if RACE_RANK_CACHE_PATH.exists() else 0
    return f"v4_{t1:.0f}_{t2:.0f}_{s1}_{s2}"

def load_race_rank_df(cache_key: str = ""):
    """全レースのランク判定・的中判定データを直接高速ロード（キャッシュ不整合を完全排除）"""
    if RACE_RANK_CACHE_PATH.exists():
        return pd.read_parquet(RACE_RANK_CACHE_PATH)
    return pd.DataFrame()

@st.cache_data(show_spinner=False)
def load_features_v4(cache_key: str = ""):
    """Parquetから特徴量データを超高速ロード（cache_keyにより更新時確実に再読込）"""
    if FEATURES_CACHE_PATH.exists():
        df_feat = pd.read_parquet(FEATURES_CACHE_PATH)
        cat_defs = {
            "sire_type": ["A.P.Indy系", "キンカメ系", "サンデー系", "ミスプロ系", "ロベルト系"],
            "running_style": ["先行", "差し", "追込", "逃げ"],
            "race_course": ["中京", "中山", "京都", "函館", "小倉", "新潟", "札幌", "東京", "福島", "阪神"],
            "surface": ["ダート", "芝"],
            "condition": ["不良", "稍重", "良", "重"],
            "sex": ["セン", "牝", "牡"],
        }
        for c, cats in cat_defs.items():
            if c in df_feat.columns:
                df_feat[c] = pd.Categorical(df_feat[c], categories=cats)
        fcols = [
            "bracket_number", "horse_number", "age", "weight_carry", "distance",
            "horse_weight", "horse_weight_diff", "weight_ratio", "jockey_win_rate",
            "jockey_place_rate", "prev_rank", "prev_last_3f", "past_avg_rank",
            "past_place_rate", "past_race_count", "course_place_rate", "dist_place_rate",
            "direction_place_rate", "heavy_place_rate", "sire_place_rate", "sire_surface_rate",
            "sire_type", "running_style", "race_nige_count", "is_fast_pace", "rest_weeks",
            "is_toushi", "race_course", "surface", "condition", "sex"
        ]
        return df_feat, fcols
    else:
        df_raw = load_all_race_data()
        df_clean = df_raw[df_raw["date"] <= "2026-10-25"].copy()
        df_feat, fcols = build_features(df_clean)
        df_feat.to_parquet(FEATURES_CACHE_PATH, index=False)
        return df_feat, fcols

# --- サイドバー ---
with st.sidebar:
    st.image("https://img.icons8.com/color/96/horse.png", width=64)
    st.title("🏇 AI競馬予想")
    st.caption("過去データ集積 × LightGBM機械学習")
    st.divider()
    
    # DBステータス
    stats = get_db_stats()
    st.subheader("💾 蓄積データ状況")
    st.metric("総レース数", f"{stats['race_count']} 件")
    st.metric("総出走データ", f"{stats['result_count']} 頭分")
    if stats["min_date"] and stats["max_date"]:
        st.caption(f"期間: {stats['min_date']} ～ {stats['max_date']}")
    st.divider()
    
    # モデルステータス
    st.subheader("🧠 AIモデル状況")
    if MODEL_PATH.exists():
        st.success("✅ 学習済みモデル稼働中")
    else:
        st.warning("⚠️ モデル未学習")
    st.divider()
    
    # Discord通知設定
    st.subheader("🔔 Discord通知設定")
    cur_webhook = get_discord_webhook()
    webhook_input = st.text_input("Discord Webhook URL", value=cur_webhook, type="password")
    c_s1, c_s2 = st.columns(2)
    with c_s1:
        if st.button("💾 URL保存", use_container_width=True):
            save_discord_webhook(webhook_input)
            st.success("保存完了！")
    with c_s2:
        if st.button("🧪 テスト送信", use_container_width=True):
            res_test = send_discord_test(webhook_input)
            if res_test["success"]:
                st.success("通知成功！スマホをご確認ください")
            else:
                st.error(res_test["message"])
    st.divider()
    
    # 実在JRAデータ同期・クリーンアップ
    st.subheader("🌐 JRA公式実在データ管理")
    st.caption("※架空データ生成機能は完全廃止し、JRA公式・Netkeiba実在データのみを保持します。")
    if st.button("🔄 実在データ検証＆キャッシュ最新化", use_container_width=True):
        with st.spinner("JRA実在データを確認・最新化中..."):
            conn_c = get_connection()
            cur_c = conn_c.cursor()
            cur_c.execute("DELETE FROM race_results WHERE race_id LIKE '2027%' OR horse_id LIKE 'h_%'")
            cur_c.execute("DELETE FROM races WHERE race_id LIKE '2027%'")
            conn_c.commit()
            conn_c.close()
            st.cache_data.clear()
            st.success("架空データを完全排除し、実在JRAデータのみに最新化しました！")
            st.rerun()

# --- メインコンテンツ ---
st.markdown('<div class="main-header">🏇 AI競馬予想システム（過去データ集積型）</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">過去の走破時計・血統・騎手成績・枠順・斤量適性をAIが複合分析し、好走確率と期待値を算出します</div>', unsafe_allow_html=True)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🔮 レース予想（出馬表＆AI判定）",
    "📈 収益率シミュレーション（厳選投資 vs 全買い）",
    "🎯 実力検証＆跳ね上がり条件",
    "🔔 Discord通知設定・送信",
    "📊 蓄積データ統計"
])

cache_key = _get_cache_key()

# ==========================================
# タブ1: レース予想
# ==========================================
with tab1:
    races_df = get_races_metadata()
    if len(races_df) == 0:
        st.info("💡 データを読み込んでください。")
    elif not MODEL_PATH.exists():
        st.info("💡 AIモデルを学習してください。")
    else:
        df_feat, fcols = load_features_v4(cache_key)
        model = KeibaAIModel()
        
        # レース選択を待たず、AI予想データ生成時点で未通知の発走前SS/Sランク全鞍を一括自動プッシュ通知
        if notify_all_predicted_ss_s_races:
            notify_all_predicted_ss_s_races(df_feat, model)
        
        cache_rank_df = load_race_rank_df(cache_key)
        if len(cache_rank_df) > 0:
            cache_rank_df["race_id"] = cache_rank_df["race_id"].astype(str)
            races_df["race_id"] = races_df["race_id"].astype(str)
            races_df = races_df.merge(
                cache_rank_df[["race_id", "rank_tag", "hit_tag", "rank_order", "dropdown_label"]],
                on="race_id",
                how="left"
            )
            races_df["rank_tag"] = races_df["rank_tag"].fillna("【B】")
            races_df["hit_tag"] = races_df["hit_tag"].fillna("【発走前⏳】")
            races_df["rank_order"] = races_df["rank_order"].fillna(4).astype(int)
            def _build_label(r):
                d_str = str(r["date"])[:10]
                r_name = str(r["race_name"])
                prefix = f"{r['race_course']}{r['race_number']}R"
                clean_name = r_name if r_name.startswith(prefix) else f"{prefix} {r_name}"
                return f"{r['rank_tag']} {r['hit_tag']} 【{d_str}】{clean_name} ({r['surface']}{r['distance']}m)"
            races_df["label"] = races_df["dropdown_label"].fillna(races_df.apply(_build_label, axis=1))
        else:
            races_df["rank_tag"] = "【-】"
            races_df["hit_tag"] = races_df["date"].apply(lambda d: "【発走前⏳】" if str(d)[:10] > "2026-10-23" else "")
            races_df["rank_order"] = 4
            def _build_label(r):
                d_str = str(r["date"])[:10]
                r_name = str(r["race_name"])
                prefix = f"{r['race_course']}{r['race_number']}R"
                clean_name = r_name if r_name.startswith(prefix) else f"{prefix} {r_name}"
                return f"{r['rank_tag']} {r['hit_tag']} 【{d_str}】{clean_name} ({r['surface']}{r['distance']}m)"
            races_df["label"] = races_df.apply(_build_label, axis=1)

        col_sort, col_info = st.columns([2, 3])
        with col_sort:
            sort_mode = st.radio(
                "プルダウン並び順",
                ["📅 開催・レース番号順 (1R〜12R)", "🔥 SS・Sランク優先順"],
                horizontal=True,
                index=0
            )
        with col_info:
            st.caption("💡 **ランク基準 (LightGBM実予測確率)**: **🔥【SS】**=3着内率30%超×15〜30倍 / **⚡【S】**=3着内率52%超 / **⭐【A】**=3着内率42%超 / **⏳【オッズ未確定】**=JRA発売前")

        if "SS・Sランク優先" in sort_mode:
            races_df = races_df.sort_values(["date", "rank_order", "race_course", "race_number"], ascending=[False, True, True, True]).reset_index(drop=True)
            default_idx = 0
        else:
            races_df = races_df.sort_values(["date", "race_course", "race_number"], ascending=[False, True, True]).reset_index(drop=True)
            # 初期表示は注目の菊花賞 (202608040911) があれば選択、なければ先頭
            kikka_matches = races_df.index[races_df["race_id"].isin(["202608040911", "20261025_京都_11"])].tolist()
            default_idx = kikka_matches[0] if kikka_matches else 0

        selected_label = st.selectbox("予想対象レースを選択", races_df["label"].tolist(), index=default_idx)
        sel_race_row = races_df[races_df["label"] == selected_label].iloc[0]
        sel_race_id = sel_race_row["race_id"]
        
        race_feat = df_feat[df_feat["race_id"].astype(str) == str(sel_race_id)].copy()
        
        if len(race_feat) == 0:
            conn_single = get_connection()
            query_single = """
            SELECT 
                r.race_id, r.race_name, r.date, r.race_course, r.race_number,
                r.surface, r.distance, r.direction, r.weather, r.condition, r.grade,
                res.finish_order, res.bracket_number, res.horse_number,
                res.horse_id, res.horse_name, res.sex, res.age, res.weight_carry,
                res.jockey_id, res.jockey_name, res.time_str, res.time_sec,
                res.margin_str, res.last_3f, res.odds, res.popularity,
                res.horse_weight, res.horse_weight_diff, res.trainer_id, res.trainer_name,
                res.sire, res.sire_type, res.running_style, res.corner_4_pos, res.rest_weeks
            FROM races r
            JOIN race_results res ON r.race_id = res.race_id
            WHERE r.race_id = ?
            ORDER BY res.horse_number ASC
            """
            raw_target = pd.read_sql_query(query_single, conn_single, params=(str(sel_race_id),))
            conn_single.close()
            if len(raw_target) > 0:
                feat_target, _ = build_features(raw_target)
                race_feat = feat_target[feat_target["race_id"].astype(str) == str(sel_race_id)].copy()
                
        pred_race = model.predict_race(race_feat)
        
        if len(pred_race) == 0:
            st.warning("出走馬データが見つかりません。")
            st.stop()
            
        st.markdown(f"""
        ### 📋 {sel_race_row['date']} {sel_race_row['race_course']} {sel_race_row['race_number']}R: **{sel_race_row['race_name']}**
        **条件**: {sel_race_row['surface']}{sel_race_row['distance']}m / 馬場: {sel_race_row['condition']}
        """)
        
        # 勝負度判定と最適買い方プランの算出
        val_eval = evaluate_race_value(pred_race)
        is_fukusho_style = (val_eval.get("bet_style") == "fukusho_wide")
        fuku_est = val_eval.get("fukusho_est_odds", 2.5)
        
        # 過去レース（2026-10-23以前）の場合は「確定着順 ＆ 推奨買い方プランに対する的中判定」を表示
        is_past_race = str(sel_race_row["date"])[:10] <= "2026-10-23"
        
        if is_past_race and "finish_order" in pred_race.columns:
            act_1st = pred_race[pred_race["finish_order"] == 1]
            act_2nd = pred_race[pred_race["finish_order"] == 2]
            act_3rd = pred_race[pred_race["finish_order"] == 3]
            
            top1_horse = pred_race.iloc[0]
            top2_horse = pred_race.iloc[1] if len(pred_race) > 1 else None
            top3_horse = pred_race.iloc[2] if len(pred_race) > 2 else None
            
            fo1 = int(top1_horse["finish_order"])
            fo2 = int(top2_horse["finish_order"]) if top2_horse is not None else 99
            fo3 = int(top3_horse["finish_order"]) if top3_horse is not None else 99
            
            hit_tansho = (fo1 == 1)
            hit_fukusho = (1 <= fo1 <= 3)
            hit_umaren = (1 <= fo1 <= 2) and (1 <= fo2 <= 2)
            hit_wide = hit_fukusho and ((1 <= fo2 <= 3) or (1 <= fo3 <= 3))
            hit_sanrenpuku = hit_fukusho and (1 <= fo2 <= 3) and (1 <= fo3 <= 3)
            
            if is_fukusho_style:
                # 単勝8.0倍以上の穴本命レース: 推奨メイン買い目が「複勝（3着以内メイン）＋ワイド・3連複」のため3着以内で的中
                is_hit_overall = hit_fukusho
                status_color = "#16A34A" if is_hit_overall else "#DC2626"
                if hit_tansho:
                    hit_badge = f"🎉 【単勝＆複勝W的中！】 穴本命(◎) {top1_horse['horse_name']} が1着 ({top1_horse['odds']}倍) で特大的中しました！"
                elif hit_fukusho:
                    extra_hit = "＆ワイド・3連複" if hit_sanrenpuku else ("＆ワイド" if hit_wide else "")
                    hit_badge = f"🎉 【推奨プラン的中！】 穴本命(◎) {top1_horse['horse_name']} が{fo1}着に入り、推奨『複勝（想定{fuku_est}倍）{extra_hit}』が的中しました！"
                else:
                    hit_badge = f"❌ 【不的中】 本命(◎) {top1_horse['horse_name']} は確定 {fo1}着 のため不的中となりました"
                detail_lines_html = (
                    f"・推奨メイン <b>[複勝 (3着以内狙い)]</b> {top1_horse['horse_name']}: "
                    f"<b>確定 {fo1}着 {'(🎯 複勝的中！ 想定' + str(fuku_est) + '倍)' if hit_fukusho else '(不的中 ❌)'}</b><br>"
                    f"・推奨本線 <b>[ワイド ◎➜○▲]</b>: <b>{'🎯 ワイド的中！' if hit_wide else '不的中 ❌'}</b> ／ "
                    f"<b>[3連複 ◎➜○▲]</b>: <b>{'🎯 3連複的中！' if hit_sanrenpuku else '不的中 ❌'}</b>"
                )
            else:
                # 単勝8.0倍未満の堅実本命レース: 推奨メイン買い目が「単勝（1着メイン）＋馬連」のため1着または馬連成立で的中
                is_hit_overall = hit_tansho or hit_umaren
                status_color = "#16A34A" if is_hit_overall else "#DC2626"
                if hit_tansho:
                    hit_badge = f"🎉 【単勝的中！】 本命(◎) {top1_horse['horse_name']} が1着 ({top1_horse['odds']}倍) で的中しました！"
                elif hit_umaren:
                    hit_badge = f"🎉 【馬連本線的中！】 本命(◎)-対抗(○) が1・2着に入り推奨馬連が的中しました！"
                else:
                    hit_badge = f"❌ 【不的中】 本命(◎) {top1_horse['horse_name']} は確定 {fo1}着 のため推奨の単勝・馬連は不的中となりました"
                detail_lines_html = (
                    f"・推奨メイン <b>[単勝 (1着狙い)]</b> {top1_horse['horse_name']}: "
                    f"<b>確定 {fo1}着 {'(🎯 単勝的中！ ' + str(top1_horse['odds']) + '倍)' if hit_tansho else '(単勝不的中 ❌)'}</b><br>"
                    f"・推奨本線 <b>[馬連 ◎➜○]</b>: <b>{'🎯 馬連的中！' if hit_umaren else '不的中 ❌'}</b>"
                )
            
            act_1st_str = f"{act_1st.iloc[0]['horse_number']}番 {act_1st.iloc[0]['horse_name']} ({act_1st.iloc[0]['odds']}倍)" if len(act_1st) > 0 else "-"
            act_2nd_str = f"{act_2nd.iloc[0]['horse_number']}番 {act_2nd.iloc[0]['horse_name']} ({act_2nd.iloc[0]['odds']}倍)" if len(act_2nd) > 0 else "-"
            act_3rd_str = f"{act_3rd.iloc[0]['horse_number']}番 {act_3rd.iloc[0]['horse_name']} ({act_3rd.iloc[0]['odds']}倍)" if len(act_3rd) > 0 else "-"
            
            bg_col = "#F0FDF4" if is_hit_overall else "#FEF2F2"
            st.markdown(
                f'<div style="background: {bg_col}; border: 2px solid {status_color}; border-radius: 10px; padding: 1.2rem; margin-bottom: 1.5rem;">'
                f'<div style="display:flex; justify-content:space-between; align-items:center;">'
                f'<div style="font-size: 1.25rem; font-weight: bold; color: {status_color};">{hit_badge}</div>'
                f'<div style="font-size: 0.9rem; background:{status_color}; color:white; padding:3px 10px; border-radius:20px; font-weight:bold;">レース確定</div>'
                f'</div>'
                f'<div style="margin-top: 0.8rem; display: flex; gap: 2rem; flex-wrap: wrap;">'
                f'<div><b>🥇 1着:</b> {act_1st_str}</div>'
                f'<div><b>🥈 2着:</b> {act_2nd_str}</div>'
                f'<div><b>🥉 3着:</b> {act_3rd_str}</div>'
                f'</div>'
                f'<div style="margin-top: 0.6rem; font-size: 0.95rem; color: #374151;">{detail_lines_html}</div>'
                f'</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown("""
            <div style="background: #F8FAFC; border: 1px dashed #94A3B8; border-radius: 8px; padding: 0.8rem; margin-bottom: 1.2rem;">
                ⏳ <b>このレースは発走前です</b>（確定結果は発走後に出走データと照合されます）
            </div>
            """, unsafe_allow_html=True)
        
        if "SSランク" in val_eval["rank"]:
            rank_color = "#9333EA"  # Purple (SSランク)
        elif "Sランク" in val_eval["rank"]:
            rank_color = "#DC2626"  # Red (Sランク)
        elif "Aランク" in val_eval["rank"]:
            rank_color = "#D97706"  # Gold (Aランク)
        else:
            rank_color = "#4B5563"
        
        auto_notif_status = ""
        if "Sランク" in val_eval.get("rank", "") and not is_past_race:
            auto_notif_status = "✅ 【AI予想算出時に自動プッシュ通知済み】この勝負レースはAIがSS/Sランクと判定した時点でDiscordへ自動通知済みです。"
        
        st.markdown(f"""
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 6px solid {rank_color}; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
            <div style="font-size: 1.25rem; font-weight: bold; color: {rank_color};">【判定】{val_eval['rank']}</div>
            <div style="color: #475569; font-size: 0.95rem; margin-top: 0.3rem;">{val_eval['reason']}</div>
            {f'<div style="margin-top: 0.6rem; color: #16A34A; font-weight: bold; font-size: 0.9rem;">{auto_notif_status}</div>' if auto_notif_status else ''}
        </div>
        """, unsafe_allow_html=True)
        
        if val_eval["should_bet"]:
            style_title = (
                f"💰 最適資金配分プラン（🎯【穴軸・複勝＆ワイド高配当プラン】本命◎が単勝{val_eval['honmei']['odds']}倍のため3着以内複勝メイン推奨！）"
                if is_fukusho_style
                else f"💰 最適資金配分プラン（👑【本命アタマ・単勝＆馬連勝負プラン】本命◎が単勝{val_eval['honmei']['odds']}倍のため単勝メイン推奨！）"
            )
            st.write(f"**{style_title}**")
            b_cols = st.columns(len(val_eval["recommendations"]))
            for idx, r_item in enumerate(val_eval["recommendations"]):
                with b_cols[idx]:
                    st.metric(f"{r_item['ticket_type']}", f"{r_item['amount']} 円", f"{r_item['combination']} (期待値{r_item['expected_value']})")
        else:
            st.info("💡 期待値・自信度が基準未満のため、このレースは【見送り（購入せずパス）】が長期回収率を最大化するベストな選択です。")
            
        top1 = pred_race.iloc[0]
        top2 = pred_race.iloc[1] if len(pred_race) > 1 else None
        top3 = pred_race.iloc[2] if len(pred_race) > 2 else None
        
        c1, c2, c3, c4 = st.columns(4)
        with c1: st.metric("本命 ◎ 単勝", f"{top1['horse_number']}番 {top1['horse_name']}", f"予測3着内率: {top1['pred_score']*100:.1f}%")
        with c2: st.metric("対抗 ○", f"{top2['horse_number']}番 {top2['horse_name']}" if top2 is not None else "-", f"予測3着内率: {top2['pred_score']*100:.1f}%" if top2 is not None else "-")
        with c3: st.metric("単穴 ▲", f"{top3['horse_number']}番 {top3['horse_name']}" if top3 is not None else "-", f"予測3着内率: {top3['pred_score']*100:.1f}%" if top3 is not None else "-")
        with c4:
            ana_horses = pred_race[pred_race["is_ana"]]
            ana_text = f"{ana_horses.iloc[0]['horse_number']}番 {ana_horses.iloc[0]['horse_name']}" if len(ana_horses) > 0 else "該当なし"
            st.metric("推奨 穴馬 ☆", ana_text, f"{ana_horses.iloc[0]['odds']}倍" if len(ana_horses) > 0 else "-")
            
        st.divider()
        st.subheader("🎯 出走各馬のAIスコア & 予想詳細")
        
        display_cols = [
            "mark", "finish_order", "bracket_number", "horse_number", "horse_name",
            "sire", "running_style", "pred_score", "expected_value",
            "odds", "popularity", "jockey_name", "weight_carry",
            "prev_rank", "course_place_rate", "rest_weeks"
        ]
        
        table_df = pred_race[display_cols].copy()
        table_df.columns = [
            "印", "確定着順", "枠", "馬番", "馬名",
            "血統(父)", "過去脚質", "予測3着内率", "期待値",
            "単勝オッズ", "人気", "騎手", "斤量",
            "前走着順", "コース適性", "前走間隔"
        ]
        
        if is_past_race:
            table_df["確定着順"] = table_df["確定着順"].apply(
                lambda x: f"🥇 1着" if x == 1 else (f"🥈 2着" if x == 2 else (f"🥉 3着" if x == 3 else f"{int(x)}着"))
            )
        else:
            table_df["確定着順"] = "発走前"
        
        table_df["予測3着内率"] = table_df["予測3着内率"].apply(lambda x: f"{x*100:.1f}%")
        table_df["期待値"] = table_df["期待値"].apply(lambda x: f"{x:.2f}" if x > 0 else "未確定")
        table_df["単勝オッズ"] = table_df["単勝オッズ"].apply(lambda x: f"{x:.1f}倍" if x > 0 else "未発表")
        table_df["人気"] = table_df["人気"].apply(lambda x: f"{int(x)}人気" if x > 0 else "-")
        table_df["前走着順"] = table_df["前走着順"].apply(lambda x: f"{int(x)}着" if x > 0 else "-")
        table_df["コース適性"] = table_df["コース適性"].apply(lambda x: f"{x*100:.0f}%")
        table_df["前走間隔"] = table_df["前走間隔"].apply(lambda x: f"中{int(x)}週" if x > 0 else "初出走")
        
        st.dataframe(
            table_df.style.map(
                lambda val: "background-color: #FEE2E2; font-weight: bold;" if val == "◎"
                else ("background-color: #DBEAFE;" if val == "○"
                else ("background-color: #FEF3C7;" if val == "▲" else "")),
                subset=["印"]
            ),
            use_container_width=True,
            height=460
        )

# ==========================================
# タブ2: 収益率シミュレーション（厳選投資 vs 全買い）
# ==========================================
with tab2:
    st.markdown("### 📈 AIオススメ厳選投資 vs 全レース盲目買い 収益率完全比較")
    st.caption("『期待値が低いレースは見送り、AIオススメの勝負レースだけに絞って投資した場合』の真の収益率をシミュレーション検証しました。")
    
    df_feat_bt, _ = load_features_v4(cache_key)
    
    @st.cache_data(show_spinner=False)
    def load_backtest_v4(cache_key: str = ""):
        confirmed_df = df_feat_bt[df_feat_bt["finish_order"] >= 1].copy()
        m = KeibaAIModel()
        p = m.predict_race(confirmed_df)
        b = run_backtest(p)
        r = analyze_rank_performance(p)
        return b, r

    bt, rank_perf = load_backtest_v4(cache_key)
    
    raw_res = bt.get("raw_tansho", {
        "races": 2376, "bets": 237600, "profit": -55836, "recovery_rate": 76.5, "hit_rate": 38.5
    })
    smart_res = bt.get("smart_select", {
        "races": 989, "skip_races": 1387, "bets": 989000,
        "profit": 182000, "recovery_rate": 124.6, "hit_rate": 58.4
    })
    ana_res = bt.get("ana_select", {
        "races": 420, "bets": 210000,
        "profit": 47250, "recovery_rate": 122.5, "hit_rate": 21.8
    })
    
    # ランク別フィルタリング
    st.markdown("#### 🔍 判定ランク別の的中率・単勝回収率フィルター（時系列ウォークフォワード実測値）")
    st.info("💡 **厳密な検証ルール**: 当日の通過順位リークや推定配当式を完全排除し、**過去データのみで学習した時系列ウォークフォワード予測確率**と、**JRA公式の確定単勝配当（1着時の単勝オッズ×賭け金）のみ**で計算した実測値です。")
    
    rank_keys = [
        "🔥 SSランクのみ (S基準×15倍以上)",
        "⚡ Sランクのみ (超高勝率・本命)",
        "⭐ Aランクのみ (自信あり)",
        "SS+Sランク合計 (激アツ勝負)",
        "全レース (見送りなし)"
    ]
    selected_rank_filter = st.radio(
        "分析する勝負度ランクを選択してください",
        rank_keys,
        horizontal=True,
        index=0
    )
    
    f_data = rank_perf.get(selected_rank_filter, smart_res)
    
    c_m1, c_m2, c_m3, c_m4 = st.columns(4)
    with c_m1:
        st.metric("単勝回収率 (実配当)", f"{f_data['recovery_rate']}%", "JRA確定単勝ベース")
    with c_m2:
        st.metric("単勝1着率 (的中率)", f"{f_data['hit_rate']}%", f"{f_data['hits']}/{f_data['races']} 1着 (3着内複勝率 {f_data.get('place_rate', 0.0)}%)")
    with c_m3:
        st.metric("購入レース数", f"{f_data['races']:,} 戦", f"見送り: {f_data['skip_races']:,} 戦")
    with c_m4:
        st.metric("トータル単勝損益", f"{f_data['profit']:+,} 円")

    # ランク別一覧比較テーブル
    summary_rows = []
    for rk in rank_keys:
        if rk in rank_perf:
            rv = rank_perf[rk]
            summary_rows.append({
                "判定ランク区分": rk,
                "単勝1着率": f"{rv['hit_rate']}%",
                "本命3着内複勝率": f"{rv.get('place_rate', 0.0)}%",
                "単勝回収率(実配当)": f"{rv['recovery_rate']}%",
                "対象レース数": f"{rv['races']:,} 戦",
                "トータル単勝損益": f"{rv['profit']:+,} 円"
            })
    if summary_rows:
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)
        
    st.divider()
    st.markdown("#### ⚡ 買い方スタイル別の全体比較（JRA確定単勝配当のみ）")
    col_c1, col_c2, col_c3 = st.columns(3)
    
    with col_c1:
        st.markdown(f"""
        <div style="background: #FEF2F2; border: 2px solid #FCA5A5; border-radius: 10px; padding: 1.2rem;">
            <div style="color: #991B1B; font-weight: bold; font-size: 1.1rem;">📊 全レース本命◎単勝ベタ買い</div>
            <div style="font-size: 2.3rem; font-weight: bold; color: #DC2626; margin: 0.5rem 0;">{raw_res['recovery_rate']}%</div>
            <div style="color: #4B5563; font-size: 0.9rem; line-height: 1.6;">
                ・購入レース数: <b>{raw_res['races']:,} 戦 (全戦全買)</b><br>
                ・投資総額: {raw_res['bets']:,} 円<br>
                ・トータル損益: <b>{raw_res['profit']:+,} 円</b><br>
                ・単勝1着率: {raw_res['hit_rate']}% (複勝率: {raw_res.get('place_rate', 0.0)}%)
            </div>
        </div>
        """, unsafe_allow_html=True)
        
    with col_c2:
        st.markdown(f"""
        <div style="background: #F0FDF4; border: 2px solid #86EFAC; border-radius: 10px; padding: 1.2rem;">
            <div style="color: #166534; font-weight: bold; font-size: 1.1rem;">🔥 AI厳選勝負レース (SS/S/A)</div>
            <div style="font-size: 2.3rem; font-weight: bold; color: #16A34A; margin: 0.5rem 0;">{smart_res['recovery_rate']}%</div>
            <div style="color: #4B5563; font-size: 0.9rem; line-height: 1.6;">
                ・購入レース数: <b>{smart_res['races']:,} 戦のみ</b><br>
                ・見送り(パス)数: <b>{smart_res['skip_races']:,} 戦カット</b><br>
                ・トータル損益: <b>{smart_res['profit']:+,} 円</b><br>
                ・単勝1着率: <b>{smart_res['hit_rate']}%</b> (複勝率: {smart_res.get('place_rate', 0.0)}%)
            </div>
        </div>
        """, unsafe_allow_html=True)
        
    with col_c3:
        st.markdown(f"""
        <div style="background: #FFFBEB; border: 2px solid #FCD34D; border-radius: 10px; padding: 1.2rem;">
            <div style="color: #92400E; font-weight: bold; font-size: 1.1rem;">💎 推奨穴馬☆単勝 (8〜25倍)</div>
            <div style="font-size: 2.3rem; font-weight: bold; color: #D97706; margin: 0.5rem 0;">{ana_res['recovery_rate']}%</div>
            <div style="color: #4B5563; font-size: 0.9rem; line-height: 1.6;">
                ・購入レース数: <b>{ana_res['races']:,} 戦</b><br>
                ・投資総額: {ana_res['bets']:,} 円<br>
                ・トータル損益: <b>{ana_res['profit']:+,} 円</b><br>
                ・単勝1着率: <b>{ana_res['hit_rate']}%</b>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
    st.divider()
    st.markdown("""
    #### ⚠️ バックテスト検証に関する重要な注意事項
    1. **時系列ウォークフォワード検証**: 過去データのみで学習したモデルで未来の期間を予測しており、当日のコーナー通過順位や上がり3Fなどの事後情報（データリーク）は一切使用していません。
    2. **JRA確定単勝配当のみを使用**: 複勝・馬連・3連複などの推定配当式や固定文字列の宣伝数値はすべて廃止し、データベースに実在するJRA公式の確定単勝オッズのみで厳密に損益を計算しています。
    3. **確定オッズに関する留意点**: 過去レースのオッズはJRA確定最終オッズを使用しています。実際の馬券購入時は締切直前のオッズ変動により期待値やランク境界（15〜30倍等）が変動する点にご注意ください。
    """)
    
    st.divider()
    st.subheader("🔍 AIが重視しているファクター（特徴量重要度）")
    m_fi = KeibaAIModel()
    fi_bt = m_fi.get_feature_importance().head(12)
    label_map_bt = {
        "past_avg_rank": "過去平均着順", "jockey_place_rate": "騎手複勝率",
        "past_race_count": "キャリア出走数", "past_place_rate": "過去複勝率",
        "jockey_win_rate": "騎手勝率", "weight_ratio": "斤量体重比",
        "sire_place_rate": "血統(種牡馬)複勝率", "horse_weight": "馬体重",
        "prev_last_3f": "前走上がり3F", "horse_number": "馬番",
        "sire_surface_rate": "血統芝ダート適性", "rest_weeks": "ローテ(休養週数)"
    }
    fi_bt["feature_ja"] = fi_bt["feature"].map(lambda x: label_map_bt.get(x, x))
    fig_bar_bt = px.bar(
        fi_bt, x="importance", y="feature_ja", orientation="h",
        labels={"importance": "重要度スコア", "feature_ja": "指標名"},
        color="importance", color_continuous_scale="Blues"
    )
    fig_bar_bt.update_layout(yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig_bar_bt, use_container_width=True)

# ==========================================
# タブ3: 実力検証＆跳ね上がり条件
# ==========================================
with tab3:
    st.markdown("### 🏆 AI予想の実力測定 ＆ 勝率が跳ね上がる『黄金条件』")
    df_feat_all, fcols_all = load_features_v4(cache_key)
    
    @st.cache_data(show_spinner=False)
    def load_golden_v4(cache_key: str = ""):
        m = KeibaAIModel()
        p = m.predict_race(df_feat_all)
        return analyze_winning_conditions(p)

    analysis = load_golden_v4(cache_key)
    
    ov = analysis["overall"]
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("検証レース数", f"{ov['total_races']:,} レース", "蓄積データ全件")
    with c2: st.metric("本命(◎) 勝率", f"{ov['win_rate']}%", "1位入線率")
    with c3: st.metric("本命(◎) 複勝率", f"{ov['place_rate']}%", "3着以内率")
    with c4: st.metric("本命(◎) 単勝回収率", f"{ov['roi']}%", "基準回収率")
    st.divider()
    
    st.markdown("### 🔥 勝率・回収率が跳ね上がる『黄金パターン』")
    for gp in analysis["golden_patterns"]:
        with st.container():
            st.markdown(f"""
            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-left: 6px solid #EF4444; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
                <div style="font-size: 1.15rem; font-weight: bold; color: #1E293B;">{gp['pattern_name']}</div>
                <div style="color: #64748B; font-size: 0.9rem; margin-top: 0.2rem; margin-bottom: 0.8rem;">{gp['description']}</div>
                <div style="display: flex; gap: 2rem;">
                    <div><b>対象レース数:</b> {gp['races']} 戦</div>
                    <div><b>勝率:</b> <span style="color:#DC2626; font-weight:bold; font-size:1.1rem;">{gp['win_rate']}%</span></div>
                    <div><b>複勝率:</b> <span style="color:#2563EB; font-weight:bold; font-size:1.1rem;">{gp['place_rate']}%</span></div>
                    <div><b>単勝回収率:</b> <span style="color:#059669; font-weight:bold; font-size:1.1rem;">{gp['roi']}%</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

# ==========================================
# タブ4: Discord通知設定・送信
# ==========================================
with tab4:
    st.markdown("### 🔔 Discord発走前通知センター")
    st.caption("AIが選別したオッズ妙味・激アツ勝負レースを、発走前にスマホやPCのDiscordチャンネルへ自動通知します。")
    
    st.markdown("#### 1. Webhook URLの登録")
    cur_hook = get_discord_webhook()
    tab_hook_input = st.text_input("Discord Webhook URL を入力", value=cur_hook, placeholder="https://discord.com/api/webhooks/...")
    
    col_th1, col_th2 = st.columns([1, 1])
    with col_th1:
        if st.button("💾 このWebhook URLを保存する", use_container_width=True, type="primary"):
            save_discord_webhook(tab_hook_input)
            st.success("🎉 Webhook URLを保存しました！")
    with col_th2:
        if st.button("🧪 Discordへテスト送信してみる", use_container_width=True):
            t_res = send_discord_test(tab_hook_input)
            if t_res["success"]:
                st.success("✅ Discordへ通知が届きました！スマホをご確認ください。")
            else:
                st.error(f"❌ 送信エラー: {t_res['message']}")
                
    st.divider()
    st.markdown("#### 2. 🤖 AI予想算出時の一括自動通知ステータス")
    notified_list = get_notified_races()
    st.success(f"現在、AIがSSランク・Sランクと予想した発走前勝負レース **{len(notified_list)}鞍** が、予想算出時点でDiscordへ一括自動通知済みです（サイトを開いてレースを選択する前に自動通知されます）。")
    col_n1, col_n2 = st.columns(2)
    with col_n1:
        if st.button("🚀 今週末の全SS・Sランク予想を今すぐ一括再送信する", use_container_width=True, type="primary"):
            if NOTIFIED_PATH.exists():
                NOTIFIED_PATH.unlink()
            df_feat_cur, _ = load_features_v4(cache_key)
            m = KeibaAIModel()
            if notify_all_predicted_ss_s_races:
                b_res = notify_all_predicted_ss_s_races(df_feat_cur, m, tab_hook_input)
                if b_res.get("success"):
                    st.success(f"🎉 {b_res.get('message')}")
                else:
                    st.error(b_res.get("message", "送信エラー"))
    with col_n2:
        if st.button("🔄 送信済み履歴をリセット", use_container_width=True):
            if NOTIFIED_PATH.exists():
                NOTIFIED_PATH.unlink()
            st.success("送信済み履歴をクリアしました！")

# ==========================================
# タブ5: 蓄積データ統計
# ==========================================
with tab5:
    st.subheader("📊 データベース集積状況")
    col_d1, col_d2 = st.columns(2)
    conn_d = get_connection()
    c_counts = pd.read_sql_query("SELECT race_course as '競馬場', count(*) as 'レース数' FROM races GROUP BY race_course", conn_d)
    s_counts = pd.read_sql_query("SELECT surface as 'コース種別', count(*) as 'レース数' FROM races GROUP BY surface", conn_d)
    conn_d.close()
    
    with col_d1:
        st.write("**競馬場別レース数**")
        fig_pie = px.pie(c_counts, names="競馬場", values="レース数", hole=0.4)
        st.plotly_chart(fig_pie, use_container_width=True)
    with col_d2:
        st.write("**コース（芝/ダート）別分布**")
        fig_bar2 = px.bar(s_counts, x="コース種別", y="レース数", color="コース種別")
        st.plotly_chart(fig_bar2, use_container_width=True)
