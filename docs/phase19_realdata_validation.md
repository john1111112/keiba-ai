# Phase 19: 実競馬データによる予測精度検証・不足データ監査（費用0円）

## 1. 概要
本モジュール ([`scripts/phase19_realdata_validation.py`](file:///C:/Users/user/.gemini/antigravity/scratch/keiba-ai/scripts/phase19_realdata_validation.py)) は、ローカルに保持されている歴史的競馬データセット ([`data/keiba.db`](file:///C:/Users/user/.gemini/antigravity/scratch/keiba-ai/data/keiba.db)) を**完全読み取り専用（Read-Only）**で棚卸し・監査し、発走前確定特徴量のみを用いた客観的な予測精度検証と、実データに存在する構造的欠損・ライセンス制約を洗い出すためのコンポーネントです。

外部有料API・有料データ契約・無断スクレイピング・実馬券購入・自動投票は一切行わず、完全ローカル（追加費用0円）で動作します。

---

## 2. データベース棚卸しサマリー (`data/keiba.db`)

- **アクセスモード**: `file:data/keiba.db?mode=ro` (SQLite Read-Only URI)
- **総レース数**: 3,174 レース（正常単一勝者レース: 3,040 レース、同着・未確定等: 134 レース）
- **総出走頭数**: 43,358 頭
- **日付範囲**: 2022年9月19日 〜 2026年10月25日（約4年分）
- **テーブル構成**:
  - `races` (3,174行): レースメタデータ（距離、コース、芝/ダート、馬場状態等）
  - `race_results` (43,358行): 出走馬ごと成績・特徴量・確定結果
  - `payouts` (6,512行): 確定払戻金レコード
  - `ai_tracking_history` (3,100行): 過去の予測ログ

---

## 3. 特徴量の時点適格性（Pre-race vs Post-race Leakage）

機械学習モデルの過剰適合および未来情報リークを防止するため、全カラムの観測時点を厳格に分類・監査しています。

### 3.1 発走前適格特徴量 (`PRE_RACE_ELIGIBLE`)
- `bracket_number` (枠番)
- `horse_number` (馬番)
- `sex` (性別) / `age` (馬齢)
- `weight_carry` (負担斤量)
- `distance` (距離) / `surface` (芝・ダート) / `race_course` (競馬場)
- `horse_weight` (馬体重) / `horse_weight_diff` (増減) ※発走約60分前確定
- `rest_weeks` (休養週数)

### 3.2 事後リーク列 (`POST_RACE_LEAKAGE` - 特徴量への使用厳禁)
- `finish_order` (着順 - 予測目的変数)
- `time_str` / `time_sec` (走破タイム)
- `margin_str` (着差)
- `last_3f` (上がり3Fタイム)
- `corner_4_pos` (4コーナー通過順)
- `running_style` (結果から事後ラベリングされた脚質)
- `odds` / `popularity` (確定オッズ・確定人気 - 発走前スナップショット時刻の証明がないため、特徴量および市場インプライド確率ベースラインへの使用禁止)

---

## 4. 時系列評価プロトコルと合成データとの区別

1. **Purged Walk-Forward Cross Validation**:
   - 完走レースを日付順にソートし、Train（過去）/ Calibration（過去中間）/ Test（未来）に完全分離。
   - レース単位のクラスタ独立性を維持し、同一レース内の全頭確率分布（合計 1.0）を評価。
2. **合成ベンチマークと実データの明確な分離**:
   - **合成ベンチマーク (Synthetic Fixture)**: 単体テスト・回帰テスト・アルゴリズム検証用（Phase 18 での改善は合成環境下における理論的改善）。
   - **実競馬データ (Historical Keiba Data)**: 実際の市場ノイズや複雑性を含む客観的検証用。合成データでの改善幅を実競馬に外挿・混同しないことを明記。

---

## 5. Phase 20 に向けた不足データ・制約の棚卸し報告

| 不足項目 | 現状ステータス | 影響 | Phase 20 で必要なアクション |
| :--- | :--- | :--- | :--- |
| **発走前オッズの時系列スナップショット** | `MISSING` | 発走直前の市場インプライド勝率との公正な対決比較が不可。 | 発走10分前・5分前等の暗号学的タイムスタンプ付きオッズログの収集機構。 |
| **公認データライセンス・出所証明** | `UNAUTHENTICATED` | ローカルSQLiteキャッシュは公的配布や商用利用の許諾がない。 | JRA-VAN等の正規許諾APIへの公式接続または学術利用許諾の確立。 |
| **公式確定払戻の暗号学的検証** | `UNAUTHENTICATED` | ペーパー取引の回収率（ROI）を信頼性を持って算出不可（ROI=null）。 | 公式払戻データの検証チェーン連携。 |

---

## 6. CLI コマンド仕様

### 6.1 データベース棚卸しの表示 (`inventory`)
```bash
python scripts/phase19_realdata_validation.py inventory --db data/keiba.db
```

### 6.2 実データ Walk-Forward 評価の実行 (`evaluate`)
```bash
python scripts/phase19_realdata_validation.py evaluate \
  --db data/keiba.db \
  --races 120 \
  --folds 3 \
  --output-report data/reports/phase19_realdata_report.json \
  --output-audit-log data/audit/phase19_audit_log.jsonl
```

---

## 7. 安全性と免責事項
- **ROI ゲーティング**: 真正な発走前オッズ・検証済み公式払戻・外部公認データが未接続であるため、回収率（ROI）は算出不可（`roi_status: "unevaluable_unverified_data"`, `roi: null`）として安全に制御されます。
- **データ・モデル保全**: 本番データベース ([`data/keiba.db`](file:///C:/Users/user/.gemini/antigravity/scratch/keiba-ai/data/keiba.db)) および本番モデル (`models/*.joblib`) は一切変更されていません（読み取り専用）。
- **実馬券・自動投票の禁止**: 本プロジェクトは自動投票機能や馬券購入コードを一切含みません。
