import json
import time
import requests
import pandas as pd
from pathlib import Path
from typing import Dict, Any, Optional, List
from src.betting_strategy import evaluate_race_value

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "discord_config.json"
NOTIFIED_PATH = Path(__file__).resolve().parent.parent / "data" / "notified_races.json"

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
            "description": "AI競馬予想システムからの通知が正常に届きました。\nAIがSSランク・Sランクと予想した時点で、サイトを開く前に自動でDiscordへ一括プッシュ通知をお届けします！",
            "color": 3066993,
            "footer": {"text": "AI Keiba Prediction Bot"}
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
    
    if "SSランク" in rank:
        color = 10181046  # Purple / Magenta
    elif "Sランク" in rank:
        color = 15158332  # Red
    elif "Aランク" in rank:
        color = 15844367  # Gold
    else:
        color = 3447003   # Blue
    
    ev_disp = f"{honmei['expected_value']}倍" if honmei.get("expected_value") is not None else "算出不可(1着確率モデル未構築)"
    fields = [
        {
            "name": "👑 本命馬 (◎)",
            "value": f"**{honmei['horse_number']}番 {honmei['horse_name']}**\n3着内予測確率: **{honmei['pred_score']}%** | 単勝オッズ: **{honmei['odds']}倍** | 単勝期待値: **{ev_disp}**",
            "inline": False
        }
    ]
    
    if top_ana and top_ana.get("expected_value") is not None:
        fields.append({
            "name": "💎 推奨 穴馬 (☆)",
            "value": f"**{top_ana['horse_number']}番 {top_ana['horse_name']}**\n単勝オッズ: **{top_ana['odds']}倍**",
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
        
    prefix_icon = "🔥" if "SSランク" in rank else "⚡"
    payload = {
        "content": f"@everyone 🚨【AI予想算出・即座通知】{prefix_icon} {rank} の勝負レースが検出されました！",
        "allowed_mentions": {"parse": ["everyone", "users", "roles"]},
        "username": "AI競馬予想エージェント",
        "avatar_url": "https://img.icons8.com/color/96/horse.png",
        "embeds": [{
            "title": f"{prefix_icon}【AI勝負レース予測通知】{race_info.get('date', '')} {race_info.get('race_course', '')}{race_info.get('race_number', '')}R: {race_info.get('race_name', '')}",
            "description": f"**判定**: `{rank}`\n**条件**: {race_info.get('surface', '')}{race_info.get('distance', '')}m / 馬場: {race_info.get('condition', '')}\n**理由**: {val_eval.get('reason', '')}",
            "color": color,
            "fields": fields,
            "footer": {"text": "AI Keiba Bot • 予想算出時自動プッシュ通知"}
        }]
    }
    
    post_url = url + ("&wait=true" if "?" in url else "?wait=true")
    try:
        resp = requests.post(post_url, json=payload, timeout=8)
        if resp.status_code in [200, 204]:
            res_data = resp.json() if resp.status_code == 200 else {}
            return {
                "success": True,
                "message": "勝負レース通知をDiscordに送信しました！",
                "api_response": res_data
            }
        else:
            return {"success": False, "message": f"送信失敗: {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"通信エラー: {e}"}

def get_notified_races() -> List[str]:
    if NOTIFIED_PATH.exists():
        try:
            with open(NOTIFIED_PATH, "r", encoding="utf-8") as f:
                return [str(x) for x in json.load(f)]
        except Exception:
            return []
    return []

def mark_race_as_notified(race_id: str):
    notified = get_notified_races()
    if str(race_id) not in notified:
        notified.append(str(race_id))
        NOTIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTIFIED_PATH, "w", encoding="utf-8") as f:
            json.dump(notified, f, ensure_ascii=False, indent=2)

def mark_races_as_notified_bulk(race_ids: List[str]):
    notified = set(get_notified_races())
    for rid in race_ids:
        notified.add(str(rid))
    NOTIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(NOTIFIED_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(list(notified)), f, ensure_ascii=False, indent=2)

def send_s_rank_alert(race_info: dict, val_eval: dict, webhook_url: str = None) -> dict:
    """SSランク・Sランクの勝負レースをDiscordに通知"""
    rank = val_eval.get("rank", "")
    if "Sランク" not in rank:
        return {"success": False, "message": "このレースはSS/Sランクではないため通知をスキップしました"}
        
    res = send_race_alert(race_info, val_eval, webhook_url)
    if res.get("success") and "race_id" in race_info:
        mark_race_as_notified(race_info["race_id"])
    return res

def notify_all_predicted_ss_s_races(df_feat: pd.DataFrame, model=None, webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """
    【重要】ユーザーが画面でレースを選択するのを待たず、
    AIが発走前レースの予想を算出した時点で、SSランク・Sランクと判定された全レースを即座にDiscordへ一括プッシュ通知する
    """
    url = webhook_url or get_discord_webhook()
    if not url:
        return {"success": False, "notified_count": 0, "message": "Webhook URL未設定"}

    notified_set = set(get_notified_races())
    # 発走前（未確定: finish_order == 0）レースのみを対象（日付固定を撤去し確定状態で判定）
    upcoming_df = df_feat[df_feat["finish_order"] == 0].copy()
    if len(upcoming_df) == 0:
        return {"success": True, "notified_count": 0, "message": "対象の発走前レースなし"}

    ss_items = []
    s_items = []
    newly_notified_ids = []

    for rid, group in upcoming_df.groupby("race_id"):
        rid_str = str(rid)
        if rid_str in notified_set:
            continue
        if model is not None:
            pred_race = model.predict_race(group)
        else:
            pred_race = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
        if len(pred_race) == 0:
            continue

        val_eval = evaluate_race_value(pred_race)
        rank_str = val_eval.get("rank", "")
        if "SSランク" in rank_str or "Sランク" in rank_str:
            r0 = pred_race.iloc[0]
            r_info = {
                "race_id": rid_str,
                "date": str(r0.get("date", ""))[:10],
                "race_course": str(r0.get("race_course", "")),
                "race_number": int(r0.get("race_number", 1)),
                "race_name": str(r0.get("race_name", "")),
                "surface": str(r0.get("surface", "")),
                "distance": int(r0.get("distance", 1600)),
                "condition": str(r0.get("condition", "良"))
            }
            if "SSランク" in rank_str:
                ss_items.append((r_info, val_eval))
            else:
                s_items.append((r_info, val_eval))
            newly_notified_ids.append(rid_str)

    if not newly_notified_ids:
        return {"success": True, "notified_count": 0, "message": "すべてのSS/Sランク予想は既に通知済みです"}

    # 1. まず全SSランク・Sランクの一括サマリー通知を送信
    summary_fields = []
    if ss_items:
        ss_lines = []
        for r_info, v_eval in ss_items:
            hm = v_eval["honmei"]
            ss_lines.append(
                f"🔥 **{r_info['date']} {r_info['race_course']}{r_info['race_number']}R {r_info['race_name']}**\n"
                f"　┗ ◎ **{hm['horse_number']}番 {hm['horse_name']}** (好走率 **{hm['pred_score']}%** / 単勝 **{hm['odds']}倍** / 推奨単勝 **{hm['kelly_amount']}円**)"
            )
        summary_fields.append({
            "name": f"🔥【SSランク】高勝率×15倍以上特大配当レース（計{len(ss_items)}鞍）",
            "value": "\n".join(ss_lines),
            "inline": False
        })

    if s_items:
        s_lines = []
        for r_info, v_eval in s_items[:12]:
            hm = v_eval["honmei"]
            s_lines.append(
                f"⚡ **{r_info['date']} {r_info['race_course']}{r_info['race_number']}R {r_info['race_name']}** "
                f"➜ ◎ **{hm['horse_number']}番 {hm['horse_name']}** (好走率 **{hm['pred_score']}%** / **{hm['odds']}倍**)"
            )
        if len(s_items) > 12:
            s_lines.append(f"…他 {len(s_items) - 12} 鞍のSランク勝負レース")
        summary_fields.append({
            "name": f"⚡【Sランク】超高勝率・絶対本命レース（計{len(s_items)}鞍）",
            "value": "\n".join(s_lines),
            "inline": False
        })

    summary_payload = {
        "content": f"@everyone 🚨【AI予想算出・自動一括通知】発走前レースのAI予想が完了し、**🔥SSランク {len(ss_items)}鞍 / ⚡Sランク {len(s_items)}鞍** が認定されました！",
        "allowed_mentions": {"parse": ["everyone", "users", "roles"]},
        "username": "AI競馬予想エージェント",
        "avatar_url": "https://img.icons8.com/color/96/horse.png",
        "embeds": [{
            "title": "🏇【AI自動予想完了】SSランク・Sランク勝負レース 一括速報",
            "description": "サイトを開いてレースを選択しなくても、AIがSSランク・Sランクと判定した時点で自動的にプッシュ通知しています。",
            "color": 10181046 if ss_items else 15158332,
            "fields": summary_fields,
            "footer": {"text": "AI Keiba Bot • 予想算出時自動プッシュ通知"}
        }]
    }

    post_url = url + ("&wait=true" if "?" in url else "?wait=true")
    try:
        requests.post(post_url, json=summary_payload, timeout=8)
    except Exception:
        pass

    # 2. 特に注目度の高いSSランク全鞍の個別詳細カード（買い目・資金配分付き）も即座に送信
    for r_info, v_eval in ss_items:
        time.sleep(0.4)
        send_race_alert(r_info, v_eval, url)

    # 全SS/Sランクを通知済みとして永続化
    mark_races_as_notified_bulk(newly_notified_ids)
    return {
        "success": True,
        "notified_count": len(newly_notified_ids),
        "ss_count": len(ss_items),
        "s_count": len(s_items),
        "message": f"AI予想算出時点で SSランク{len(ss_items)}鞍・Sランク{len(s_items)}鞍 をDiscordへ自動通知しました！"
    }

def check_and_auto_notify_s_rank(race_info: dict, val_eval: dict, webhook_url: str = None) -> Optional[dict]:
    """互換用（個別レース閲覧時の遅延通知は廃止し、一括予想時通知に統一）"""
    return None


import platform
import subprocess

def send_windows_notification(title: str, message: str) -> bool:
    """Windowsデスクトップトースト通知（BalloonTip/Toast）を送信する"""
    if platform.system() != "Windows":
        return False
    import os
    code = r'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Visible = $true
$icon.BalloonTipTitle = $env:KEIBA_ALERT_TITLE
$icon.BalloonTipText = $env:KEIBA_ALERT_MESSAGE
$icon.ShowBalloonTip(10000)
Start-Sleep -Seconds 4
$icon.Dispose()
'''
    env = os.environ.copy()
    env["KEIBA_ALERT_TITLE"] = title
    env["KEIBA_ALERT_MESSAGE"] = message
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except Exception:
        return False
