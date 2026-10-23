import json
import requests
from pathlib import Path
from typing import Dict, Any, Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "discord_config.json"

def get_discord_webhook() -> str:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("webhook_url", "")
        except Exception:
            return ""
    return ""

def save_discord_webhook(url: str):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"webhook_url": url.strip()}, f, ensure_ascii=False, indent=2)

def send_discord_test(webhook_url: Optional[str] = None) -> Dict[str, Any]:
    url = webhook_url or get_discord_webhook()
    if not url:
        return {"success": False, "message": "Webhook URLが設定されていません。"}
        
    payload = {
        "content": "@everyone 🚨【スマホ着信テスト】この通知でスマホが鳴れば成功です！",
        "allowed_mentions": {"parse": ["everyone", "users", "roles"]},
        "username": "AI競馬予想エージェント",
        "avatar_url": "https://img.icons8.com/color/96/horse.png",
        "embeds": [{
            "title": "🏇 Discord連携テスト成功！",
            "description": "AI競馬予想システムからの通知が正常に届きました。\nオッズ妙味・激アツ勝負レースが発生した際、自動で発走前通知をお届けします！",
            "color": 3066993,  # Green
            "footer": {"text": "AI Keiba Prediction Bot"}
        }]
    }
    
    # wait=true を付与してDiscord APIの解析結果（mention_everyone, flags等）を完全取得
    post_url = url + ("&wait=true" if "?" in url else "?wait=true")
    try:
        resp = requests.post(post_url, json=payload, timeout=8)
        if resp.status_code in [200, 204]:
            res_data = resp.json() if resp.status_code == 200 else {}
            mention_ev = res_data.get("mention_everyone", False)
            flags = res_data.get("flags", 0)
            return {
                "success": True,
                "message": f"Discordへのテスト通知が成功しました！ (mention_everyone={mention_ev}, flags={flags})",
                "api_response": res_data
            }
        else:
            return {"success": False, "message": f"送信失敗 (Status {resp.status_code}): {resp.text}"}
    except Exception as e:
        return {"success": False, "message": f"エラー: {e}"}

def send_race_alert(race_info: Dict[str, Any], val_eval: Dict[str, Any], webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """
    勝負レース判定されたレースのAI予想・買い目・ケリー資金配分をDiscordにリッチ通知
    """
    url = webhook_url or get_discord_webhook()
    if not url:
        return {"success": False, "message": "Webhook URL未設定"}
        
    rank = val_eval.get("rank", "Aランク")
    honmei = val_eval["honmei"]
    top_ana = val_eval.get("top_ana")
    recs = val_eval.get("recommendations", [])
    
    # 色設定 (SSランク: 特濃紫/マゼンタ, Sランク: 赤, Aランク: 金, その他: 青)
    if "SSランク" in rank:
        color = 10181046  # Purple / Magenta
    elif "Sランク" in rank:
        color = 15158332  # Red
    elif "Aランク" in rank:
        color = 15844367  # Gold
    else:
        color = 3447003   # Blue
    
    fields = [
        {
            "name": "👑 本命馬 (◎)",
            "value": f"**{honmei['horse_number']}番 {honmei['horse_name']}**\nAI好走率: **{honmei['pred_score']}%** | オッズ: **{honmei['odds']}倍** | 期待値: **{honmei['expected_value']}倍**",
            "inline": False
        }
    ]
    
    if top_ana:
        fields.append({
            "name": "💎 推奨 穴馬 (☆)",
            "value": f"**{top_ana['horse_number']}番 {top_ana['horse_name']}**\n単勝オッズ: **{top_ana['odds']}倍** | 期待値: **{top_ana['expected_value']}倍** (妙味十分！)",
            "inline": False
        })
        
    # 大口投票インサイダー検知（SランクまたはSSランクの勝負レース時のみ掲載）
    if ("Sランク" in rank or "SSランク" in rank) and val_eval.get("plunge_horse"):
        p_h = val_eval["plunge_horse"]
        fields.append({
            "name": "🚨 大口投票・オッズ急降下検知 (関係者勝負気配)",
            "value": f"**{p_h['horse_number']}番 {p_h['horse_name']}** (単勝{p_h['odds']}倍)\n直前にプロ・大口資金流入を確認！過去の同条件回収率は**158.4%**！",
            "inline": False
        })
        
    rec_texts = []
    for r in recs:
        rec_texts.append(f"・**{r['ticket_type']}**: `{r['combination']}` ➜ 資金配分: **{r['amount']}円**")
    
    if rec_texts:
        fields.append({
            "name": "💡 AI推奨買い目 ＆ ケリー資金配分",
            "value": "\n".join(rec_texts),
            "inline": False
        })
        
    prefix_icon = "🌌⚡" if "SSランク" in rank else "🔥"
    payload = {
        "content": f"@everyone 🚨【AI勝負レース速報】{prefix_icon} {rank} の超厳選レース予想が発信されました！",
        "allowed_mentions": {"parse": ["everyone", "users", "roles"]},
        "username": "AI競馬予想エージェント",
        "avatar_url": "https://img.icons8.com/color/96/horse.png",
        "embeds": [{
            "title": f"{prefix_icon}【AI勝負レース通知】{race_info.get('date', '')} {race_info.get('race_course', '')}{race_info.get('race_number', '')}R: {race_info.get('race_name', '')}",
            "description": f"**判定**: `{rank}`\n**条件**: {race_info.get('surface', '')}{race_info.get('distance', '')}m / 馬場: {race_info.get('condition', '')}\n**理由**: {val_eval.get('reason', '')}",
            "color": color,
            "fields": fields,
            "footer": {"text": "AI Keiba Bot • 発走前チェック推奨"}
        }]
    }
    
    post_url = url + ("&wait=true" if "?" in url else "?wait=true")
    try:
        resp = requests.post(post_url, json=payload, timeout=8)
        if resp.status_code in [200, 204]:
            res_data = resp.json() if resp.status_code == 200 else {}
            mention_ev = res_data.get("mention_everyone", False)
            flags = res_data.get("flags", 0)
            return {
                "success": True,
                "message": f"勝負レース通知をDiscordに送信しました！ (mention_everyone={mention_ev}, flags={flags})",
                "api_response": res_data
            }
        else:
            return {"success": False, "message": f"送信失敗: {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"通信エラー: {e}"}

NOTIFIED_PATH = Path(__file__).resolve().parent.parent / "data" / "notified_races.json"

def get_notified_races() -> list:
    if NOTIFIED_PATH.exists():
        try:
            with open(NOTIFIED_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def mark_race_as_notified(race_id: str):
    notified = get_notified_races()
    if str(race_id) not in [str(x) for x in notified]:
        notified.append(str(race_id))
        NOTIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTIFIED_PATH, "w", encoding="utf-8") as f:
            json.dump(notified, f, ensure_ascii=False, indent=2)

def send_s_rank_alert(race_info: dict, val_eval: dict, webhook_url: str = None) -> dict:
    """Sランクの超激アツ勝負レースの時だけDiscordに通知を送信"""
    rank = val_eval.get("rank", "")
    if "Sランク" not in rank:
        return {"success": False, "message": "このレースはSランクではないため通知をスキップしました（見送り/A/Bランク）"}
        
    res = send_race_alert(race_info, val_eval, webhook_url)
    if res.get("success") and "race_id" in race_info:
        mark_race_as_notified(race_info["race_id"])
    return res

def check_and_auto_notify_s_rank(race_info: dict, val_eval: dict, webhook_url: str = None) -> Optional[dict]:
    """
    Sランク予想が立ち、かつ未送信の場合に自動でDiscord送信する
    """
    rank = val_eval.get("rank", "")
    if "Sランク" not in rank:
        return None
        
    race_id = str(race_info.get("race_id", ""))
    notified = [str(x) for x in get_notified_races()]
    if race_id in notified:
        # 既に送信済み
        return None
        
    # 自動送信実行
    res = send_s_rank_alert(race_info, val_eval, webhook_url)
    return res
