# 出走表CSVから発走前特徴量を保存する

実際に取得した出走表をCSVで保存した場合、手入力でJSONを組み立てずに監査可能な研究用入力へ変換できます。**CSVの値と時刻は利用者の申告に基づき、独立認証はされません。** この機能はオッズを取得せず、投票もしません。

CSVの列名（順序は任意、重複禁止）：

```csv
horse_number,bracket_number,age,weight_carry,distance,race_course,surface,condition,sex
```

実データがある場合だけ、各出走馬の値を追記してください。着順、人気、確定オッズなどの列があると読み込みを拒否します。時刻は実際の観測・受信時刻を使い、推測で補完しないでください。

```powershell
python -m scripts.import_prerace_features_csv data/my_race_card.csv --out data/research_features/race_001.json --race-id "race_001" --race-start-time "2025-10-26T15:40:00+09:00" --captured-at "2025-10-26T15:25:00+09:00" --received-at "2025-10-26T15:25:05+09:00" --source "manual_race_card" --as-of-time "2025-10-26T15:30:00+09:00" --dry-run
```

上の時刻は**形式説明用の例**です。検証が成功したら、実際の時刻を指定し `--dry-run` を外して保存します。

- CSVは厳密に9列。余計な列、馬番重複、欠損、不正な数値、タイムゾーン欠落、発走後の入力を拒否。
- 既存のJSONが異なる内容なら上書きしない。同一内容なら何度実行しても重複保存しない。
- 保存するJSONは `docs/PRERACE_FEATURE_CONTRACT.md` の仕様に準拠。
- `NO_BET`、`model_inference_allowed=false`、`asof_certified=false`。この入力だけではAIの勝率は確定しません。
