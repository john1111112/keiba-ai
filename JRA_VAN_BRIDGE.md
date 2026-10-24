# 競馬AI — JRA-VAN接続前の安全な受け口

これは **JV-Linkの通信実装ではありません**。JRA-VAN SDK同梱の公式Pythonサンプル等から、利用条件を満たした形で出力した発走前単勝オッズCSVを取り込むためのアダプターです。実データの取得・SDKの動作は未検証です。

## 既存リポジトリへの追加
`src/jv_odds_bridge.py` と `tests/test_jv_odds_bridge.py` を同じパスに配置。既存 `src/odds_history.py` の `odds_timeline` スキーマに合わせています。既存ファイルの上書きは不要です。

## CSV形式
`race_id,horse_number,odds_value,observed_at,fetched_at,start_at,source`

時刻は `2026-10-25T14:00:00+09:00` のようなタイムゾーン付きISO 8601。`source` は `jra_van` または `sample`。発走前に実際に観測・取得した記録のみ `jra_van` としてください。**公式提供元の確認が取れていないCSVを `jra_van` と偽装しないこと**。

例（テスト専用、実レースの事実を表さない）:
`202610250811,3,4.5,2026-10-25T14:00:00+09:00,2026-10-25T14:01:00+09:00,2026-10-25T15:00:00+09:00,sample`

## 実行
```
python -m unittest discover -s tests -p 'test_jv_odds_bridge.py' -v
python -m src.jv_odds_bridge path/to/odds.csv --db data/odds_history.db
```
テスト用の架空オッズを取り込む場合のみ `--allow-sample` を指定。**実データと混在するDBでサンプルを扱わない**ことを推奨します。

## 注意
- `observed_at <= fetched_at <= 現在` および `fetched_at < start_at` を検証します。発走時刻はCSV提供側の正確な値が必要です。
- 同一race/horse/observed/sourceの再登録を防ぎますが、**同一発表時刻で値が異なる矛盾レコードの検知・自動訂正は未実装**です。
- 既存の `get_latest_odds_as_of` は `fetched_at` や `data_status` でフィルタしないため、**現状のまま実運用の予測に使用しないでください**。取得時刻と `sample_only` の除外を組み込む必要があります。
- JRA-VANからの取得、公式SDKでの検証、GitHubへの反映、Streamlitへの反映は未実施。
- JRA-VAN利用規約に従い、第三者向けのデータ再配布はしないでください。
