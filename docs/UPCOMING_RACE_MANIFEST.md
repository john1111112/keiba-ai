# Windows: 発走前レース一覧（手入力・事前確認）

`data/upcoming_races.json` に実際のレースIDと発走予定時刻を記入します。
このファイルは**予定表**であり、オッズや結果を含みません。時刻はタイムゾーン必須。

```json
[
  {
    "race_id": "EXAMPLE_ONLY",
    "race_status": "upcoming",
    "race_time": "2026-10-25T15:40:00+09:00"
  }
]
```

例のレースID・時刻はダミーです。実際のレースに差し替えてください。
`race_status` がないレースは処理せず、過去の着順から出走前と推測しません。

```powershell
python -m scripts.run_prerace_auto_recorder --schedule-scan --features data/upcoming_races.json --db data/odds_history.db --all-timings
```

このコマンドは現在時刻が発走30分前・10分前・5分前の指定窓に入った時のみ監査記録します。
現時点で全自動オッズプロバイダーは未接続のため、オッズは欠損として記録されます。
実際のオッズは別途 `python -m scripts.run_manual_prerace_pipeline` で本人確認済みのJSONを取り込みます。
記録だけではAI予測の時刻・特徴量の正当性は認証されません。表示は **NO BET** です。

## 安全上の制約

- `--features` の既定値は `data/upcoming_races.json` です。過去の `features.parquet` は拒否します。
- `race_status` は `upcoming`、`cancelled`、`postponed` のみ許可し、後二者は記録対象外です。
- オッズ保存はSQLiteの書き込みトランザクションで重複時刻を確認し、既存の同一レース・馬番・取得時刻を再保存しません。
- 未認証の予測特徴量からAI推論を実行せず、発走前のEVは認証しません。
