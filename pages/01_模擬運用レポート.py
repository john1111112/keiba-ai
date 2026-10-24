"""Optional Streamlit page; read-only, no prediction or live betting actions."""
import os
from pathlib import Path
import streamlit as st
from src.paper_dashboard import load_dashboard

st.set_page_config(page_title="模擬運用レポート", page_icon="📊", layout="wide")
st.title("📊 模擬運用レポート")
st.caption("検証用の記録です。購入推奨や利益保証ではありません。")

# The file must be provisioned separately on the Streamlit host. Never commit the ledger.
path = Path(os.environ.get("KEIBA_PAPER_DB", str(Path(__file__).resolve().parents[1] / "data" / "paper_trading.db")))
try:
    result = load_dashboard(path)
except Exception:
    st.error("模擬運用台帳を読み取れません。DB形式・アクセス権限を確認してください。")
    st.stop()
if result is None:
    st.info("模擬運用データはまだありません。実際の発走前予測と公式払戻を登録すると集計できます。")
    st.stop()

m = result["overall"]
cols = st.columns(4)
cols[0].metric("記録した予測", f"{m['predictions']:,}件")
cols[1].metric("精算済み", f"{m['settled']:,}件")
cols[2].metric("未精算", f"{m['unsettled']:,}件")
cols[3].metric("回収率", f"{m['roi']*100:.1f}%" if m['roi'] is not None else "未算出")
st.write(f"**仮想投資額:** {m['stake_yen']:,}円　**仮想払戻:** {m['returns_yen']:,}円　**損益:** {m['profit_yen']:+,}円")
st.caption("精算済みの記録だけを1点100円で計算。未精算の記録は回収率の分母に含めません。")
if result["models"]:
    st.subheader("モデル別の模擬成績")
    rows=[]
    for x in result["models"]:
        rows.append({"モデル":x["model_id"],"予測件数":x["predictions"],"精算済み":x["settled"],
                     "回収率(%)":round(x["roi"]*100,2) if x["roi"] is not None else None,
                     "損益(円)":x["profit_yen"]})
    st.dataframe(rows,hide_index=True,use_container_width=True)
st.warning("実際の発走前オッズの自動取得・公式結果の自動照合は未完成です。")
