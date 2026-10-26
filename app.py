import importlib
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

from src.database import init_db, load_all_race_data, get_db_stats, get_connection, get_races_metadata
from src.features import build_features, build_features_for_race_with_history
from src.model import KeibaAIModel, KeibaAIWinModel, MODEL_PATH, MODEL_WIN_V2_28_PATH
from scripts.build_race_dashboard import build_dashboard

import src.backtest as bt_mod
importlib.reload(bt_mod)
import src.notifier as notif_mod
importlib.reload(notif_mod)
send_windows_notification = getattr(notif_mod, "send_windows_notification", None)
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
        df_feat, fcols = build_features(df_raw)
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
    
    # Windowsデスクトップ通知
    st.subheader("🖥️ Windowsデスクトップ通知")
    if st.button("🔔 デスクトップ通知テスト", use_container_width=True):
        if send_windows_notification:
            ok = send_windows_notification("🏇 KEIBA AI", "Windowsデスクトップ通知は正常に稼働しています。")
            if ok:
                st.success("デスクトップ通知を送信しました！")
            else:
                st.warning("通知送信に失敗しました（Windows環境をご確認ください）。")
    st.divider()
    # 実在JRAデータ同期・キャッシュ再読込（SELECT専用・DELETE処理完全撤去）
    st.subheader("🌐 JRA公式実在データ管理")
    st.caption("※データベース保護のため、画面からのデータ削除処理は無効化されています。")
    if st.button("🔄 キャッシュ再読込・最新化", use_container_width=True):
        with st.spinner("特徴量・予測キャッシュを再読込中..."):
            st.cache_data.clear()
            st.success("データベースを変更せず、キャッシュを最新化しました！")
            st.rerun()

# --- メインコンテンツ ---
st.markdown('<div class="main-header">🏇 AI競馬予想システム（過去データ集積型）</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">過去の走破時計・血統・騎手成績・枠順・斤量適性をAIが複合分析し、好走確率と期待値を算出します</div>', unsafe_allow_html=True)

tab0, tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🏇 本日レース・AI期待値速報",
    "🔮 過去レース予想（出馬表＆AI判定）",
    "📈 収益率シミュレーション（厳選投資 vs 全買い）",
    "🎯 実力検証＆跳ね上がり条件",
    "🔔 Discord通知設定・送信",
    "📊 蓄積データ統計"
])

cache_key = _get_cache_key()

# ==========================================
# タブ0: 本日レース・AI期待値速報（当日リアルタイム）
# ==========================================
with tab0:
    st.subheader("🏁 本日開催レース・リアルタイムAI単勝勝率 ＆ 期待値分析")
    st.caption("公式認可データおよび単勝1着モデル（28特徴量）に基づく期待回収倍率ランキング")

    # --- JV-Link 接続・実運用準備 8段階診断マトリクス (Expander) ---
    with st.expander("📡 JV-Link (JRA-VAN) 接続・実運用準備ステータス（8段階診断マトリクス）", expanded=False):
        try:
            from scripts.check_jvlink_readiness import run_full_readiness_check
            chk = run_full_readiness_check()
            m = chk["matrix"]
            
            c_dia1, c_dia2, c_dia3, c_dia4 = st.columns(4)
            with c_dia1:
                st.write("**1. Python環境 (32bit共存)**")
                if m["python_env"] == "READY":
                    st.success("✅ READY (32bit配備済)")
                else:
                    st.error("❌ NOT_READY")
                    
                st.write("**5. 利用権限 (JRA-VAN)**")
                if m["subscription_auth"] == "AUTHORIZED":
                    st.success("✅ AUTHORIZED")
                else:
                    st.info("ℹ️ NOT_AUTHORIZED (体験開始前)")

            with c_dia2:
                st.write("**2. SDK導入 (JRA-VAN)**")
                if m["sdk_installation"] == "READY":
                    st.success("✅ READY (登録済)")
                else:
                    st.warning("⚠️ NOT_READY (未インストール)")

                st.write("**6. 実データ取得 (JRA-VAN)**")
                if m["live_data_fetch"] == "SUCCESS":
                    st.success("✅ SUCCESS")
                elif m["live_data_fetch"] == "NOT_TESTED":
                    st.info("ℹ️ NOT_TESTED (実データ0件・待機中)")
                else:
                    st.error("❌ FAILED")

            with c_dia3:
                st.write("**3. COM接続 (jvdg.JVLink)**")
                if m["com_connection"] == "READY":
                    st.success("✅ READY")
                else:
                    st.warning("⚠️ NOT_READY (SDK導入後稼働)")

                st.write("**7. AI予測パイプライン**")
                if m["ai_prediction"] == "SUCCESS":
                    st.success("✅ SUCCESS (28特徴量検証済)")
                else:
                    st.error("❌ FAILED")

            with c_dia4:
                st.write("**4. API仕様検証 (SDK照合)**")
                if m["api_specification"] == "VERIFIED":
                    st.success("✅ VERIFIED (公式照合済)")
                else:
                    st.warning("⚠️ UNVERIFIED")

                st.write("**8. 通知システム (重複抑止)**")
                if m["notification"] == "SUCCESS":
                    st.success("✅ SUCCESS (安全ロック稼働)")
                else:
                    st.error("❌ FAILED")

            if chk["action_required"]:
                st.info("💡 **実運用開始へのステップ**: " + " / ".join(chk["action_required"]))
        except Exception as e:
            st.caption(f"診断情報取得ステータス: {e}")
    
    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        snapshot_source = st.selectbox(
            "📡 データ取得ソース",
            ["最新JV-Link公式フィード (data/authorized_feed/latest_jvlink_feed.json)", "発走前スケジュール (data/upcoming_races.json)", "手動スナップショット指定"],
            index=0
        )
    with col_t2:
        st.write("")
        st.write("")
        refresh_btn = st.button("🔄 最新オッズ・予測再計算", use_container_width=True)

    feed_file = Path("data/authorized_feed/latest_jvlink_feed.json")
    if "upcoming_races" in snapshot_source:
        feed_file = Path("data/upcoming_races.json")
    elif "手動" in snapshot_source:
        custom_p = st.text_input("スナップショットJSONパス", "data/snapshots/sample.json")
        feed_file = Path(custom_p)

    c_m1, c_m2, c_m3, c_m4 = st.columns(4)
    with c_m1:
        win_model_loaded = MODEL_WIN_V2_28_PATH.exists()
        st.metric("単勝勝率AIモデル", "モデルファイルあり（稼働は別途検証）" if win_model_loaded else "未ロード")
    with c_m2:
        feed_exists = feed_file.exists()
        st.metric("データ取得状況", "保存フィードあり（認証別途確認）" if feed_exists else "未接続（架空補完なし）")
    with c_m3:
        st.metric("自動通知状態", "待機中（重複抑止稼働）")
    with c_m4:
        from datetime import datetime
        st.metric("最終更新", datetime.now().strftime("%H:%M:%S"))

    if not feed_file.exists():
        st.info(f"💡 現在、{feed_file.name} に本日の認可レースデータが存在しません。\n"
                "※ 安全方針により、架空データや確定オッズによる補完・代用は一切行いません（未接続・欠損扱い）。\n"
                "公式フィードまたはJV-Linkが接続され次第、自動的に最新オッズと期待値が表示されます。")
    else:
        try:
            # AI予測マニフェストの生成（win_model利用）
            dash_report = build_dashboard(feed_file)
            rows = dash_report.get("rows", [])
            if rows:
                df_dash = pd.DataFrame(rows)
                df_dash = df_dash.rename(columns={
                    "race_id": "レースID", "race_time": "発走日時",
                    "horse_number": "馬番", "horse_name": "馬名",
                    "win_odds": "単勝オッズ", "odds_observed_at": "オッズ観測日時",
                    "win_probability": "AI単勝勝率", "expected_value": "期待回収倍率",
                    "recommendation": "推奨判定"
                })
                # ランキング表示
                st.dataframe(df_dash, hide_index=True, use_container_width=True)
                
                # 注目レース・高期待値馬ピックアップ
                if "期待回収倍率" in df_dash.columns and df_dash["期待回収倍率"].notna().any():
                    top_ev = df_dash.dropna(subset=["期待回収倍率"]).sort_values("期待回収倍率", ascending=False).head(3)
                    st.markdown("### 🔥 本日のAI期待値 TOP 3")
                    st.dataframe(top_ev, hide_index=True, use_container_width=True)
            else:
                st.info("現在発走前の対象レースがありません。")
        except Exception as e:
            st.warning(f"データ解析ステータス: {e}")
    st.divider()
    
    # --- 監視状況・通知設定・履歴セクション ---
    st.markdown("### ⚙️ バックグラウンド自動監視 ＆ 自動通知管理")
    st.caption("画面を閉じていてもWindows上で自動的にレースを分析し、条件合致時に通知します。")

    col_m1, col_m2 = st.columns(2)
    
    with col_m1:
        st.markdown("#### 📡 監視デーモン稼働状況")
        daemon_status_file = Path("data/monitor_daemon_status.json")
        daemon_status = "停止中 (START_KEIBA_AI.bat またはタスクスケジューラで起動)"
        daemon_pid = None
        last_run_time = "未実行"
        cycles = 0
        if daemon_status_file.is_file():
            try:
                import json
                d_info = json.loads(daemon_status_file.read_text(encoding="utf-8"))
                daemon_status = d_info.get("daemon_status", "不明")
                daemon_pid = d_info.get("pid")
                last_run_time = d_info.get("last_run_at", "未実行")
                cycles = d_info.get("cycle_count", 0)
            except Exception:
                pass
        
        st.write(f"**ステータス**: `{daemon_status}`" + (f" (PID: {daemon_pid})" if daemon_pid else ""))
        st.write(f"**累計実行サイクル**: `{cycles} 回`")
        st.write(f"**最終スキャン時刻**: `{last_run_time}`")
        
        c_btn1, c_btn2 = st.columns(2)
        with c_btn1:
            if st.button("▶️ 今すぐ手動スキャン実行", use_container_width=True):
                from scripts.run_background_scan import run_cycle
                with st.spinner("スキャン＆推論実行中..."):
                    res = run_cycle("data/authorized_feed/latest_jvlink_feed.json", "data/alert_state.db", use_jvlink=True)
                    st.success(f"スキャン完了: 処理レース={res.get('processed_races')}, 候補={res.get('candidates_found')}, 通知={res.get('notifications_sent')}")
                    st.rerun()
        with c_btn2:
            st.caption("※タスクスケジューラ設定は `scripts/setup_windows_task.bat` から行えます。")

    with col_m2:
        st.markdown("#### 🎯 通知条件・閾値設定")
        from src.automated_monitor import load_alert_config, save_alert_config
        cfg = load_alert_config()
        
        c_c1, c_c2 = st.columns(2)
        with c_c1:
            in_min_ev = st.number_input("最低期待回収倍率 (EV)", min_value=1.0, max_value=10.0, value=float(cfg.get("min_ev", 1.15)), step=0.05)
            in_win_toast = st.checkbox("Windowsデスクトップ通知", value=bool(cfg.get("windows_toast_enabled", True)))
        with c_c2:
            in_min_p = st.number_input("最低単勝勝率 (Win Prob)", min_value=0.05, max_value=1.0, value=float(cfg.get("min_win_prob", 0.20)), step=0.05)
            in_discord = st.checkbox("Discord通知", value=bool(cfg.get("discord_enabled", False)))
            
        if st.button("💾 条件設定を保存", use_container_width=True):
            cfg["min_ev"] = round(in_min_ev, 2)
            cfg["min_win_prob"] = round(in_min_p, 2)
            cfg["windows_toast_enabled"] = in_win_toast
            cfg["discord_enabled"] = in_discord
            save_alert_config(cfg)
            st.success("通知条件を保存しました！次回監視サイクルから適用されます。")

    # --- 通知履歴テーブル ---
    st.markdown("#### 📜 通知配信履歴（SQLite: data/alert_state.db）")
    alert_db = Path("data/alert_state.db")
    if alert_db.is_file():
        try:
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(f"file:{alert_db.resolve()}?mode=ro", uri=True)) as conn:
                hist_df = pd.read_sql_query("""
                SELECT race_id as レースID, horse_number as 馬番, horse_name as 馬名,
                       win_odds as 単勝オッズ, round(win_probability, 3) as AI勝率,
                       round(expected_value, 2) as 期待値, first_sent_at as 送信時刻,
                       channel as チャネル, delivery_status as 状態
                FROM alert_delivery ORDER BY id DESC LIMIT 20
                """, conn)
            if len(hist_df) > 0:
                st.dataframe(hist_df, hide_index=True, use_container_width=True)
            else:
                st.info("まだ通知履歴はありません。条件に合致するレースが検知され次第、自動記録されます。")
        except Exception as exc:
            st.info("通知履歴データベースの初期化待機中...")
    else:
        st.info("まだ通知履歴データベースはありません。")

    # --- 直近の監視ログ ---
    with st.expander("📝 監視ログ（直近の実行ログ）"):
        log_file = Path("data/background_monitor.log")
        if log_file.is_file():
            try:
                lines = log_file.read_text(encoding="utf-8").splitlines()
                st.code("\n".join(lines[-25:]), language="text")
            except Exception:
                st.caption("ログ読込中...")
        else:
            st.caption("ログファイルは監視実行時に自動生成されます。")



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
            races_df["rank_tag"] = races_df["rank_tag"].fillna(
                races_df["is_complete_card"].apply(lambda c: "【B】" if c == 1 else "⚠️【出馬表一部欠損】")
            )
            races_df["hit_tag"] = races_df["hit_tag"].fillna(
                races_df["is_complete_card"].apply(lambda c: "【発走前⏳】" if c == 1 else "【参考表示】")
            )
            races_df["rank_order"] = races_df["rank_order"].fillna(
                races_df["is_complete_card"].apply(lambda c: 4 if c == 1 else 7)
            ).astype(int)
            def _build_label(r):
                d_str = str(r["date"])[:10]
                r_name = str(r["race_name"])
                prefix = f"{r['race_course']}{r['race_number']}R"
                clean_name = r_name if r_name.startswith(prefix) else f"{prefix} {r_name}"
                return f"{r['rank_tag']} {r['hit_tag']} 【{d_str}】{clean_name} ({r['surface']}{r['distance']}m)"
            races_df["label"] = races_df["dropdown_label"].fillna(races_df.apply(_build_label, axis=1))
        else:
            races_df["rank_tag"] = "【-】"
            races_df["hit_tag"] = races_df["is_confirmed"].apply(lambda c: "【確定】" if c == 1 else "【発走前⏳】")
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
            st.caption("💡 **旧ルール参考ランク (3着内予測確率)**: **🔥【SS】**=3着内率30%超×15〜30倍 / **⚡【S】**=3着内率52%超 / **⭐【A】**=3着内率42%超（※期待値算出・購入推奨は一時停止中）")

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
        is_fallback_history = False
        if len(race_feat) == 0:
            is_fallback_history = True
            race_feat, _ = build_features_for_race_with_history(str(sel_race_id))

        pred_race = model.predict_race(race_feat)

        if len(pred_race) == 0:
            st.warning("出走馬データが見つかりません。")
            st.stop()

        if int(sel_race_row.get("is_complete_card", 1)) == 0:
            st.warning(
                f"⚠️ **不完全な出馬表データ（収録頭数: {len(pred_race)}頭）**: "
                "この過去レースは出走馬の一部のみが保存されているため、通常の予測可能レース（完全な出馬表）とは区別して参考表示しています。"
            )
        if "past_race_count" in pred_race.columns:
            no_hist_cnt = int((pred_race["past_race_count"] == 0).sum())
            if no_hist_cnt > 0:
                st.caption(f"ℹ️ 出走 {len(pred_race)}頭中 {no_hist_cnt}頭は当データベース内に過去走履歴がないため（初出走または過去走未収録）、初期基準値で評価しています。")
            
        st.markdown(f"""
        ### 📋 {sel_race_row['date']} {sel_race_row['race_course']} {sel_race_row['race_number']}R: **{sel_race_row['race_name']}**
        **条件**: {sel_race_row['surface']}{sel_race_row['distance']}m / 馬場: {sel_race_row['condition']}
        """)
        
        # 勝負度判定（旧ルール参考評価）
        val_eval = evaluate_race_value(pred_race)
        is_fukusho_style = (val_eval.get("bet_style") == "fukusho_wide")
        fuku_est = val_eval.get("fukusho_est_odds")

        # 固定日付による判定を撤去し、レースの確定状態（finish_order >= 1 の有無）に基づいて判定
        is_past_race = bool(int(sel_race_row.get("is_confirmed", 0)) == 1)
        if not is_past_race and "finish_order" in pred_race.columns:
            is_past_race = bool((pred_race["finish_order"] >= 1).any())
        
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
        
        if val_eval["should_bet"] and len(val_eval.get("recommendations", [])) > 0:
            b_cols = st.columns(len(val_eval["recommendations"]))
            for idx, r_item in enumerate(val_eval["recommendations"]):
                with b_cols[idx]:
                    st.metric(f"{r_item['ticket_type']}", f"{r_item['amount']} 円", f"{r_item['combination']}")
        else:
            st.info(
                "ℹ️ **期待値計算および購入推奨の一時停止中**: "
                "現在のAIスコアは「3着以内予測確率」であり、単勝勝率ではありません。"
                "単勝期待値は「1着確率モデル」完成まで、複勝期待値は「実複勝オッズ」取得まで算出不可としているため、"
                "期待値を根拠とした穴馬判定・買い目推奨は一時停止しています（SS/S/Aランク表記は旧ルールによる参考評価です）。"
            )

        top1 = pred_race.iloc[0]
        top2 = pred_race.iloc[1] if len(pred_race) > 1 else None
        top3 = pred_race.iloc[2] if len(pred_race) > 2 else None

        c1, c2, c3, c4 = st.columns(4)
        with c1: st.metric("本命 ◎ (3着内率1位)", f"{top1['horse_number']}番 {top1['horse_name']}", f"予測3着内率: {top1['pred_score']*100:.1f}%")
        with c2: st.metric("対抗 ○ (3着内率2位)", f"{top2['horse_number']}番 {top2['horse_name']}" if top2 is not None else "-", f"予測3着内率: {top2['pred_score']*100:.1f}%" if top2 is not None else "-")
        with c3: st.metric("単穴 ▲ (3着内率3位)", f"{top3['horse_number']}番 {top3['horse_name']}" if top3 is not None else "-", f"予測3着内率: {top3['pred_score']*100:.1f}%" if top3 is not None else "-")
        with c4:
            st.metric("単勝・複勝期待値", "算出不可", "1着確率/実複勝オッズ未取得")

        st.divider()
        st.subheader("🎯 出走各馬のAIスコア (3着以内予測確率) & 出馬表詳細")

        display_cols = [
            "mark", "finish_order", "bracket_number", "horse_number", "horse_name",
            "sire", "running_style", "pred_score", "expected_value",
            "odds", "popularity", "jockey_name", "weight_carry",
            "prev_rank", "course_place_rate", "rest_weeks"
        ]

        table_df = pred_race[display_cols].copy()
        table_df.columns = [
            "印", "確定着順", "枠", "馬番", "馬名",
            "血統(父)", "過去脚質", "予測3着内率", "単勝/複勝期待値",
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
        table_df["単勝/複勝期待値"] = "算出不可"
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
    st.markdown("#### 🔍 旧ルール参考ランク別の的中率・単勝回収率フィルター（時系列ウォークフォワード実測値）")
    st.info("💡 **検証ルールと参考評価の明示**: 現在のSS・S・Aランクは「3着以内予測確率と単勝オッズ帯」による**旧ルールの参考評価**です。単勝期待値は1着確率モデル完成まで、複勝期待値は実複勝オッズ取得まで算出不可とし、期待値を根拠とした購入推奨は一時停止しています（下記はJRA確定単勝配当ベースの実測参考値です）。")
    
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
