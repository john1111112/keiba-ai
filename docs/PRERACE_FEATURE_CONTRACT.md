# 発走前AI特徴量の安全な入力契約（v1）

**目的:** 既存の過去レース結果テーブルや確定オッズを発走前予測へ誤用しないため、外部で事前取得した出走表情報を、時刻付きで検証する。

`scripts/validate_prerace_features.py` は次の条件を満たすJSONだけを受け付けます。

- トップレベルは `race_id`, `race_start_time`, `captured_at`, `received_at`, `source`, `features` の6項目。
- `captured_at <= received_at <= as_of_time < race_start_time`。時刻にはタイムゾーンが必須。
- 各馬は `horse_number`, `bracket_number`, `age`, `weight_carry`, `distance`, `race_course`, `surface`, `condition`, `sex` の9項目のみ。馬番重複や不正な数値は拒否。
- 着順、払戻、確定オッズ、人気、実測上がり、結果ラベル、推論後の勝率は拒否。
- 過去成績集約特徴量は**前日時点の集計証明がない限り不許可**。この契約では一律拒否する。

例（値は**形式説明用の架空例**。実データと誤認しないこと）：

```json
{
  "race_id": "example-race",
  "race_start_time": "2026-10-26T15:40:00+09:00",
  "captured_at": "2026-10-26T15:25:00+09:00",
  "received_at": "2026-10-26T15:25:02+09:00",
  "source": "manual_race_card",
  "features": [{
    "horse_number": 1,
    "bracket_number": 1,
    "age": 4,
    "weight_carry": 56,
    "distance": 1600,
    "race_course": "東京",
    "surface": "芝",
    "condition": "良",
    "sex": "牡"
  }]
}
```

```powershell
python -m scripts.validate_prerace_features data/prerace_features.json --as-of-time "2026-10-26T15:30:00+09:00" --out data/prerace_feature_audit.json
```

監査結果にSHA-256ハッシュが出ます。**形式・時刻の整合性確認は、実際の取得元の独立検証ではありません。** 現行学習モデルはこれら9項目だけで学習されておらず、特徴量の互換性が証明できません。従って `model_inference_allowed=false`、`asof_certified=false`、`NO_BET` を返し、推論は起動しません。

次に必要なのは、(1) 正式な事前配信データの利用権・発表時刻を検証するアダプタ、(2) 事前取得可能な特徴量のみでのモデル再学習、(3) 時系列の独立検証です。
