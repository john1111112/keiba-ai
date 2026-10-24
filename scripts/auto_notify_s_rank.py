"""
S-Rank 勝負レース自動監視・Discord通知スクリプト
バックグラウンドで動作し、未送信のSランク勝負レースを自動検知して即座にDiscordへプッシュ送信します。
"""
import sys
import time
import pandas as pd
from pathlib import Path

# プロジェクトルートを追加
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.notifier import get_discord_webhook, send_s_rank_alert, get_notified_races, mark_race_as_notified
from src.model import KeibaAIModel
from src.betting_strategy import evaluate_race_value

FEATURES_CACHE_PATH = project_root / "data" / "features.parquet"

def scan_and_notify_s_rank():
    webhook_url = get_discord_webhook()
    if not webhook_url:
        print("[SKIP] Discord Webhook URLが未設定です。")
        return 0

    if not FEATURES_CACHE_PATH.exists():
        print("[SKIP] 特徴量キャッシュ(features.parquet)が見つかりません。")
        return 0

    df_feat = pd.read_parquet(FEATURES_CACHE_PATH)
    cat_cols = ["race_course", "surface", "condition", "sex", "sire_type", "running_style"]
    for c in cat_cols:
        if c in df_feat.columns:
            df_feat[c] = df_feat[c].astype("category")

    # 発走前（未確定: finish_order == 0）のレースを対象（固定日付を撤去）
    future_races = df_feat[df_feat["finish_order"] == 0].copy()
    if len(future_races) == 0:
        return 0

    model = KeibaAIModel()
    model.load()

    notified = [str(x) for x in get_notified_races()]
    sent_count = 0

    # レースごとに判定
    for race_id, group in future_races.groupby("race_id"):
        if str(race_id) in notified:
            continue

        pred = model.predict_race(group)
        val = evaluate_race_value(pred)

        if "Sランク" in val.get("rank", ""):
            r = group.iloc[0]
            r_info = {
                "race_id": str(race_id),
                "date": str(r["date"])[:10],
                "race_course": r["race_course"],
                "race_number": int(r["race_number"]),
                "race_name": r["race_name"],
                "surface": r["surface"],
                "distance": int(r["distance"]),
                "condition": r["condition"]
            }
            print(f"[FOUND S-RANK] {r_info['date']} {r_info['race_course']}{r_info['race_number']}R {r_info['race_name']}")
            res = send_s_rank_alert(r_info, val, webhook_url)
            if res.get("success"):
                mark_race_as_notified(race_id)
                sent_count += 1
                print(f"[SENT] Discordにプッシュ通知を自動送信しました！")
            else:
                print(f"[ERROR] 送信失敗: {res.get('message')}")

    return sent_count

if __name__ == "__main__":
    print("--- Sランク勝負レース 自動監視スキャン開始 ---")
    count = scan_and_notify_s_rank()
    print(f"--- スキャン終了: 新規送信 {count} 件 ---")
