# Phase 17: 再現可能なpaper評価レポート・比較検証（費用0円）

## 1. 概要
本モジュール ([`scripts/phase17_reproducible_paper_report.py`](file:///C:/Users/user/.gemini/antigravity/scratch/keiba-ai/scripts/phase17_reproducible_paper_report.py)) は、競馬予測モデルおよび戦略のペーパーシミュレーション評価において、客観的・機械的かつ完全に再現可能な検証レポートを生成し、2つのレポート間で統計的比較および整合性検証を行うためのコンポーネントです。

外部有料データ・有料API・実馬券購入・自動投票・スクレイピングは一切行わず、完全ローカルかつ追加費用0円で動作します。

---

## 2. 目的と設計原則

1. **データセット完全性保証 (Dataset Fingerprinting)**:
   - レースデータセットの行・列・主要キーをソート・正規化し、SHA-256 フィンガープリントを算出。
   - 欠損値（NaN）および非有限値（Inf / -Inf）の有無を機械的に厳格検査。
   - 実行時の Git コミット SHA、Python 環境、乱数シード、時系列分割設定（Fold 数・テストレース数）をメタデータとして永続化。

2. **比較可能性不変条件 (Baseline Comparability Invariant)**:
   - 2つの評価レポート（例: モデルA vs モデルB、またはベースライン vs 改善版）を比較する際、以下の条件が完全一致していることを必須とします:
     - `dataset_fingerprint_sha256`: 同一の評価データセット
     - `random_seed`: 同一の乱数シード
     - `split_config`: 同一の時系列クロスバリデーション構成
     - `schema_version`: 互換性のあるレポートスキーマ
   - 1項目でも不一致がある場合、実験統制の破綻として比較を拒絶（`comparable: false`）し、恣意的なチェリーピッキングを防止します。

3. **不確実性と統計的独立性 (Uncertainty & Independence Awareness)**:
   - レース単位のクラスタ独立性を維持しつつ、ブートストラップ法（95% 信頼区間）による不確実性を評価。
   - 同一レース内の競走馬同士は相互依存（ゼロサム的関係）にあるため、独立同一分布（i.i.d.）の仮定が成立しない点を注記。

4. **改ざん防止ハッシュチェーン検証**:
   - 評価実行時に記録された監査ログ（JSONL）の各行について、前行ハッシュと現在行ペイロードの SHA-256 チェーンを先頭から末尾まで完全検証。

5. **利益誇張の絶対排除とゼロ円原則**:
   - 未検証データセットや合成ベンチマークに対しては、回収率（ROI）を算出不能（`roi_status: "unevaluable_unverified_data"`, `roi_mean: null`）として扱い、過大評価・宣伝表現を排除。
   - 実馬券投票や有料APIの呼び出しコードは含まず、純粋な機械学習評価メトリクス（Log Loss, Brier Score, ECE, Top-1 Accuracy）のみを検証。

---

## 3. CLI コマンド仕様

### 3.1 レポート生成 (`generate`)
```bash
python scripts/phase17_reproducible_paper_report.py generate \
  --output data/reports/phase17_report.json \
  --audit-log data/audit/phase17_audit_log.jsonl \
  --seed 42 \
  --folds 3
```

### 3.2 2レポート間の比較 (`compare`)
```bash
python scripts/phase17_reproducible_paper_report.py compare \
  --baseline data/reports/phase17_report_baseline.json \
  --candidate data/reports/phase17_report_candidate.json \
  --output data/reports/phase17_comparison.json
```

### 3.3 監査ログのハッシュチェーン検証 (`verify-log`)
```bash
python scripts/phase17_reproducible_paper_report.py verify-log \
  --audit-log data/audit/phase17_audit_log.jsonl
```

---

## 4. 入出力スキーマ仕様

### 4.1 レポート JSON (`phase17_report.json`)
```json
{
  "schema_version": "1.0.0",
  "report_timestamp_utc": "2026-10-25T12:00:00Z",
  "git_commit_sha": "d0d80e9...",
  "random_seed": 42,
  "data_integrity": {
    "dataset_fingerprint_sha256": "a3f5b...",
    "total_rows": 120,
    "total_races": 10,
    "finite_values_verified": true,
    "nan_count": 0,
    "inf_count": 0
  },
  "split_config": {
    "split_type": "purged_walk_forward_cv",
    "n_splits": 3,
    "total_races": 10
  },
  "metrics": {
    "log_loss": {"mean": 0.45, "ci_95": [0.42, 0.48]},
    "brier_score": {"mean": 0.12, "ci_95": [0.11, 0.14]},
    "ece": {"mean": 0.04, "ci_95": [0.03, 0.06]},
    "top1_accuracy": {"mean": 0.40, "ci_95": [0.35, 0.45]}
  },
  "roi_gating": {
    "roi_status": "unevaluable_unverified_data",
    "roi_mean": null,
    "disclaimer": "Real ROI cannot be evaluated without strictly verified historical betting odds."
  },
  "audit_chain_verified": true
}
```

### 4.2 比較結果 JSON (`phase17_comparison.json`)
```json
{
  "comparison_timestamp_utc": "2026-10-25T12:05:00Z",
  "comparable": true,
  "baseline_metrics": { ... },
  "candidate_metrics": { ... },
  "metric_deltas": {
    "delta_log_loss": -0.02,
    "delta_brier_score": -0.01,
    "delta_ece": -0.005,
    "delta_top1_accuracy": 0.03
  },
  "candidate_improved": true,
  "independence_caveats": "Races are treated as independent clusters. Horses within the same race are mutually dependent."
}
```

---

## 5. 安全性と免責事項
本システムは学術的・技術的検証を目的としたペーパー評価フレームワークです。実在するギャンブル・馬券購入への投資助言、あるいは将来の金銭的利益を約束するものではありません。
