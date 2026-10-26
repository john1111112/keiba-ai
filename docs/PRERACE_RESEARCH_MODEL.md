# 発走前特徴量だけでの研究用モデル検証

`scripts/train_prerace_research.py` は、**事前取得した出走表特徴量JSON**と**別ファイルの勝敗ラベル**から、2026年の封印ホールドアウトに触れず、時系列分割でロジスティック回帰モデルの検証指標を計算します。

```powershell
python -m scripts.train_prerace_research --bundles-dir data/research_features --labels data/research_labels.json --train-before "2024-01-01T00:00:00+09:00" --validate-before "2025-01-01T00:00:00+09:00" --min-races 20 --out data/research_model_audit.json
```

これはコマンド例です。**実際の事前取得特徴量とラベルが揃っていなければ実行できません。** 架空の学習用データを自動生成しません。

入力：
- `data/research_features/` に、レースごとの `docs/PRERACE_FEATURE_CONTRACT.md` 形式のJSON。各レースの発走時刻は2025年以前。
- `data/research_labels.json` は `[{"race_id":"...","horse_number":1,"won":true}, ...]` 形式。各レースに勝者は1頭、全出走馬のラベルが必要。
- 学習期間は `race_start_time < train_before`、検証期間は `train_before <= race_start_time < validate_before`。両方に最低 `min-races` レースが必要。2026年以降の入力は**拒否**します。
- モデルには事前入力可能な数値・カテゴリのみを渡し、着順、オッズ、人気、レースID、開催日、馬番を特徴量に含めません。
- 出力はBrier score、log loss、ROC AUC、入力SHA-256、分割件数の監査JSON。モデルファイルや実際の馬券推奨は出力しません。

**制約:** 手入力の `captured_at` と `source` は独立認証されていません。学習時点の過去成績集約やオッズも未導入です。2値分類スコアはレース内で合計1の勝率とは限らず、EV算出には使用不可。 `NO_BET`、`production_inference_allowed=false` を強制します。封印した2026年の成績は読み込みません。
