# Phase 14: Probability Calibration & Paper Decision Audit

## 1. 概要 (Overview)
本仕様書は、Phase 13 のリークフリー時系列オフライン評価基盤を土台として、**単勝AIモデルの確率予測に対する校正（Calibration）**、**信頼性評価（ECE / Brier / Log Loss）**、および**ペーパー運用意思決定の再現可能な監査ログ記録**を設計・実装したものである。

> [!WARNING]
> **ゼロコスト・非運用免責事項**:
> - 本フェーズは追加費用0円の完全ローカル環境で動作し、有料API契約・スクレイピング・外部課金は一切行わない。
> - 生成・出力される評価メトリクスおよび意思決定ログは合成ベンチマークフィクスチャによるものであり、**実運用における市場成績や実際の馬券払戻を意味するものではない**。
> - 未検証のオッズや架空の払戻に基づく ROI の主張は行わず、公式データが揃わない場合はすべて「評価不可」と表示する。

---

## 2. 確率予測校正のアーキテクチャ (Calibration Architecture)

### 2.1 厳格な3分割時系列分離 (Purged 3-Way Split)
時系列データにおけるデータリークを完全に防止するため、データを以下の3期間に厳密に時系列順で分離する。

$$\text{Train (学習)} \quad \longrightarrow \quad \text{Calibration (校正器学習)} \quad \longrightarrow \quad \text{Test (最終評価)}$$

- **不変条件 (Invariants)**:
  1. $\max(\text{Train Start}) < \min(\text{Calibration Start}) \le \max(\text{Calibration Start}) < \min(\text{Test Start})$
  2. 同一の `race_id` が Train、Calibration、Test の複数期間に跨がってはならない（Purging）。
  3. 各行の特徴量観測時刻は、発走時刻および締切時刻よりも過去でなければならない（`feature_observed_at < cutoff_at < race_start_at`）。

### 2.2 確率完全性 (Probability Integrity & Sum-to-One)
- 欠損値（NaN、None）、負数、1.0超過の確率値を厳格に棄却。
- 各レース内の出走全頭の予測確率総和が 1.0 に正規化されていることを検証。

### 2.3 プラット・スケーリング (Platt Scaling)
- Calibration 期間の予測確率ロジットに対して単変量シグモイド校正器を適合：
  $$P_{\text{calibrated}} = \sigma(a \cdot \text{logit}(P_{\text{raw}}) + b)$$
- 校正後、各レース内の確率総和が厳密に 1.0 となるよう Softmax / レース単位正規化を実施。

### 2.4 信頼性診断指標
- **Log Loss (交差エントロピー損失)**
- **Brier Score (平均二乗誤差)**
- **Expected Calibration Error (ECE)**: 予測確率を 10 ビンに分割し、ビンの平均信頼度と実的中率の加重絶対誤差を測定。

---

## 3. ペーパー運用 意思決定監査ログ (Paper Decision Audit Log)

自動投票や実資金取引は一切行わず、ペーパー運用の意思決定プロセスの透明性と再現性を確保するため、以下の構造化監査ログ（JSONL）を記録する。

### 3.1 監査フィールド
- `race_id`: レース識別子
- `horse_number`: 出走馬番
- `prediction_at`: 予測時刻 (UTC)
- `feature_cutoff_at`: 特徴量締切時刻 (UTC)
- `race_start_at`: 発走予定時刻 (UTC)
- `odds_value`: 発走前オッズ (未検証時は警告)
- `odds_observed_at`: オッズ観測時刻 (独自UTC測定)
- `model_id`: 予測モデル識別子
- `prob_calibrated`: 校正済み単勝確率
- `expected_value`: 期待値 ($EV = P_{\text{calibrated}} \times \text{Odds}$)
- `decision`: 意思決定 (`BET` または `SKIP`)
- `reason`: 判断理由 (`ev_threshold_met`, `probability_below_threshold`, `expected_value_below_threshold`, `missing_odds` 等)

### 3.2 厳格な ROI ゲーティング
- 合成データ、または公式検証済みの払戻が存在しないデータについては、**`roi_status: "unevaluable_unverified_data"`、`roi: null`** とし、架空の回収率を算出・標榜することをシステム的に遮断する。
