# Phase 16: Paper-Only Quality Gates & Walk-Forward Robustness

## 1. 概要 (Overview)
本仕様書は、Phase 15 のウォークフォワード検証およびハッシュチェーン監査基盤を土台として、**ペーパー運用における包括的な品質ゲート（Paper Quality Gates）**、**厳密な理由コードによるフェイルクローズ制御**、および**ブートストラップ法を用いた頑健性診断（Robustness Diagnostics）** を定義・実装したものである。

> [!WARNING]
> **ゼロコストおよび合成ベンチマーク免責事項**:
> - 本フェーズは追加費用0円の完全ローカル環境で動作し、有料API契約・スクレイピング・外部課金は一切行わない。
> - 生成・出力される評価メトリクスおよび信頼区間は合成ベンチマークフィクスチャによるものであり、**実運用における市場成績や実際の馬券払戻を意味するものではない**。
> - 未検証のオッズや架空の払戻に基づく ROI の主張は行わず、公式データが揃わない場合はすべて「評価不可」と表示する。

---

## 2. 自律自動起動の実証記録 (Autonomous Ingestion Verification)

本 Issue #18 は、ユーザーによる事前の継続開発承認（Issue #14 コメント）に基づき、**ユーザーが Antigravity チャットに手動指示を送信することなく、定期タスク `task-4295`（Iteration 2）のタイマー契機によって自動検出・承認判定・実装着手**が行われた。

- **トリガー元**: 組み込み定期スケジュール `task-4295`（Cron: `0 * * * *`）
- **検出時刻**: 2026-10-25 12:00:00 JST (`2026-10-25T03:00:00Z`)
- **承認ゲート判定**: `decision: "APPROVED"`, `execution_authorized: true`
- **安全統制**: ローカル Allowlist（`config/issue_dispatch_allowlist.json`）によるフェイルクローズ制御が正常に機能し、未承認 Issue の無断実行や二重実行を確実に遮断。

---

## 3. Paper-Only 品質ゲート設計 (Quality Gate Architecture)

予測の実行およびペーパー意思決定の前に、すべてのレコードに対して以下の不変条件を厳格に検査し、1つでも違反がある場合は必ず **`decision: "SKIP"`** として処理を安全停止する。

### 3.1 理由コード体系 (Reason Codes)

| 理由コード | 分類 | 判定基準・不変条件 |
| :--- | :--- | :--- |
| `PASSED` | 正常 | 全品質ゲートを合格 |
| `INVALID_SOURCE_PROVENANCE` | データ出所 | 許可リスト外の出所（未確認スクレイピング等） |
| `POST_START_FEATURE_OBSERVATION` | 時間整合性 | 特徴量観測時刻 $\ge$ レース発走時刻 |
| `FEATURE_CUTOFF_VIOLATION` | 時間整合性 | 特徴量観測時刻 $>$ 特徴量締切時刻 |
| `POST_START_ODDS_OBSERVATION` | 時間整合性 | オッズ観測時刻 $\ge$ レース発走時刻 |
| `OBSERVATION_AFTER_PREDICTION` | 時間整合性 | オッズ観測時刻 $>$ 予測生成時刻 |
| `POST_START_PREDICTION` | 時間整合性 | 予測生成時刻 $\ge$ レース発走時刻 |
| `FUTURE_TIMESTAMP_REJECTED` | 時間整合性 | 現在時刻から 60 秒以上先の未来時刻 |
| `STALE_ODDS_SNAPSHOT` | オッズ健全性 | 予測時刻より 48 時間以上古いオッズ |
| `INVALID_OR_MISSING_ODDS` | オッズ健全性 | オッズ欠損、NaN、または 1.0 未満 |
| `MISSING_MODEL_VERSION` | モデル健全性 | モデル ID / バージョン情報の欠損 |
| `INVALID_PROBABILITY_VALUE` | 確率健全性 | 確率が NaN、負数、または 1.0 超過 |
| `RACE_PROBABILITY_SUM_ANOMALY` | レース整合性 | レース内全頭の確率総和が 1.0 $\pm 0.15$ を逸脱 |
| `DUPLICATE_HORSE_NUMBER` | レース整合性 | レース内に出走馬番の重複が存在 |
| `RACE_ID_MISMATCH` | レース整合性 | レコード間でレース ID が不一致 |
| `HASH_CHAIN_INTEGRITY_BREACH` | 監査整合性 | 監査ログの SHA-256 ハッシュ連鎖が断絶 |

---

## 4. ウォークフォワード頑健性診断 (Robustness Diagnostics)

評価メトリクスに対する標本変動の不確実性を定量化するため、ブートストラップ法（リサンプリング $B=200$ 回、固定乱数シード）により 95% 信頼区間を推定する。

- **Log Loss 95% 信頼区間**: $[LL_{2.5\%}, LL_{97.5\%}]$
- **Brier Score 95% 信頼区間**: $[BS_{2.5\%}, BS_{97.5\%}]$
- **サンプル数不足時**: データ数が 10 件未満の場合は `status: "unevaluable_insufficient_samples"` として算出を安全にスキップ。
