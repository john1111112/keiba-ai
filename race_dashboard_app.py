"""Read-only Streamlit view for the authorized race snapshot and EV ranking.

This view never sends alerts or places bets; predictions must be supplied by
a separately verified model pipeline.
"""
from __future__ import annotations
import json
from pathlib import Path
import streamlit as st
import pandas as pd
from scripts.build_race_dashboard import build_dashboard

st.set_page_config(page_title="KEIBA AI | 当日レース", layout="wide")
st.title("🏇 KEIBA AI — 当日レース・期待値")
st.caption("公式データと単勝モデルの実運用検証は未完了。参考表示のみ／購入推奨ではありません。")
snapshot = st.text_input("認可データのスナップショット", "data/authorized_feed/latest.json")
predictions_path = st.text_input("単勝勝率JSON（任意）", "")
if st.button("更新") or "dashboard" not in st.session_state:
    try:
        preds = {}
        if predictions_path.strip():
            items = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
            if not isinstance(items, list):
                raise ValueError("予測データは配列形式で指定してください")
            for item in items:
                if not isinstance(item, dict) or set(item) != {"race_id", "horse_number", "win_probability"}:
                    raise ValueError("予測データの形式が不正です")
                key = (item["race_id"], item["horse_number"])
                if key in preds:
                    raise ValueError("重複した予測データ")
                preds[key] = item["win_probability"]
        st.session_state["dashboard"] = build_dashboard(snapshot, predictions=preds)
        st.session_state.pop("dashboard_error", None)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        st.session_state.pop("dashboard", None)
        st.session_state["dashboard_error"] = str(exc)
if "dashboard_error" in st.session_state:
    st.warning("表示可能なデータがありません: " + st.session_state["dashboard_error"])
if "dashboard" in st.session_state:
    report = st.session_state["dashboard"]
    a,b,c = st.columns(3)
    a.metric("対象レース", report["races"])
    b.metric("出走馬", report["runners"])
    c.metric("通知・購入", "停止中")
    if report["rows"]:
        df = pd.DataFrame(report["rows"])
        df = df.rename(columns={"race_id":"レースID","race_time":"発走日時",
          "horse_number":"馬番","horse_name":"馬名","win_odds":"単勝オッズ",
          "odds_observed_at":"オッズ観測日時","win_probability":"単勝勝率",
          "expected_value":"期待回収倍率","recommendation":"判定"})
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.info("発走前の対象レースがありません。")
