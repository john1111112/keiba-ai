# 発走前オッズ：最短で画面を動かす手順

このPRは既存の `data/keiba.db` を変更しません。料金が発生するAPIも利用しません。
手入力の時刻は本人申告であり、独立検証されたリアルタイムデータではありません。

## 1. JSONファイルを作成

`data/manual_snapshot.json` に以下のように保存します。**実際に確認した数値・時刻だけ**を記入してください。以下の数値は**フォーマット例であり実測データではありません**。

```json
{
  "race_id": "EXAMPLE_RACE",
  "race_start_time": "2026-10-25T15:40:00+09:00",
  "observed_at": "2026-10-25T15:30:00+09:00",
  "fetched_at": "2026-10-25T15:30:05+09:00",
  "source": "manual_screen",
  "odds": [
    {"horse_number": 1, "odds_value": 5.0},
    {"horse_number": 2, "odds_value": 12.0}
  ]
}
```

## 2. 検証だけ（DB書き込みなし）

```powershell
python -m scripts.run_manual_prerace_pipeline data/manual_snapshot.json --dry-run
```

## 3. 保存して画面を生成

```powershell
python -m scripts.run_manual_prerace_pipeline data/manual_snapshot.json --out data/prerace_dashboard.html
start data/prerace_dashboard.html
```

- 発走前の `observed_at <= fetched_at <= as_of_time < race_start_time` が必須。
- 重複取り込みは冪等。矛盾する再取り込みは拒否。
- `data/odds_history.db` のみ更新。結果・払戻データは読み込みません。
- 表示は **NO BET** 固定。AI予測の正当性、期待値、収益性は未認証。
- 自動オッズ取得は未接続。JV-Linkなど実ソース接続と発表時刻の確認が必要。
- `--as-of-time` を指定すると、指定した発走前時点までの情報だけを表示します。
