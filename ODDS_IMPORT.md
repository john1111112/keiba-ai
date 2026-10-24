# Phase 5：発走前単勝オッズCSV取込パッチ

既存Phase 5統合版のルートへ `scripts/import_pre_race_odds.py` と `scripts/test_pre_race_odds_import.py` を配置してください。既存の `src/odds_history.py` を利用し、既存の競馬DBは変更しません。

## CSV必須列
`race_id,horse_number,odds_value,observed_at,fetched_at,race_start_at,source`

- `observed_at`: 提供元がオッズを観測・公表した時刻
- `fetched_at`: 実際に取得した時刻（推定や遡及記録ではない）
- `race_start_at`: 実際の発走予定時刻（検証対象。正確性は入力元に依存）
- 時刻は必ずタイムゾーン付きISO8601形式（例：`2026-10-24T13:00:00+09:00`）。
- CSVは利用権限を持つ信頼できるデータ提供元から作成すること。`source` の真正性を本ツールは証明できません。
- オッズは単勝、1.0以上の有限数のみ。払戻確定値やレース後の値を混ぜないこと。

## 実行
```
python -m scripts.import_pre_race_odds --csv data/pre_race_odds.csv --db data/odds_history.db
python -m scripts.import_pre_race_odds --csv data/pre_race_odds.csv --db data/odds_history.db --apply
python -m unittest scripts.test_pre_race_odds_import -v
```

初期設定は検証のみ。`--apply` を付けた場合のみ別DBへ書き込みます。CSV全行を検証後にトランザクション登録し、同一レコードの再実行はスキップします。外部APIへのアクセス、出走表取得、モデル予測起動、払戻照合、GitHubデプロイは**含まれません**。
