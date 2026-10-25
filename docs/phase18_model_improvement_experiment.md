# Phase 18: 競馬予測モデル精度改善のオフライン実験（費用0円）

## 1. 概要
本モジュール ([`scripts/phase18_model_improvement_experiment.py`](file:///C:/Users/user/.gemini/antigravity/scratch/keiba-ai/scripts/phase18_model_improvement_experiment.py)) は、競馬予測モデルそのものの精度（確率的識別力・校正力）を改善するため、パージ済み時系列分割（Walk-Forward CV）下でベースラインモデルと改善候補モデルを客観的に比較・検証するオフライン実験パイプラインです。

Phase 17 の再現性・改ざん防止監査基盤をそのまま再利用し、外部有料API・有料データ契約・実馬券投票・スクレイピングを一切行わず、完全ローカル（追加費用0円）で動作します。

---

## 2. 評価対象モデル

| モデル識別子 | モデル種別 | 説明 |
| :--- | :--- | :--- |
| `baseline_uniform` | ベースライン (無情報) | 出走頭数 $N$ に対して一様分布 $1/N$ を割り当てる事前分布。 |
| `baseline_implied_odds` | ベースライン (市場情報) | 発走前オッズの逆数をレース内で合計 1.0 に正規化したインプライド勝率。 |
| `raw_uncalibrated` | ベースライン (既存モデル) | 馬ごとの実力スコアに対する個別シグモイド変換（レース内合計が 1.0 にならない非正規化予測）。 |
| `candidate_in_race_softmax` | 改善候補 1 | レース内全頭のスコアに対して数値的に安定な Softmax を適用し、勝率分布（合計 1.0）へ正規化したモデル。 |
| `candidate_calibrated_softmax` | 改善候補 2 (採用候補) | Walk-Forward の Train 期間内（過去データ）からさらに分離した Calibration セットで最適温度パラメータ $\tau$ を学習し、レース内 Softmax を適用した校正済みモデル。 |

---

## 3. 厳格な時系列評価プロトコル

1. **Purged Walk-Forward Cross Validation**:
   - レース単位のクラスタ独立性を維持し、レース発走時刻順に時系列ソート。
   - レース間のデータ重複・リークを完全に排除した $K$ 分割 Walk-Forward を実行。
2. **Train / Calibration / Test の完全分離**:
   - 各 Fold において、学習期間の前半（70%）を特徴量・モデル学習に、後半（30%）を校正用（Calibration）に分離。
   - 未来の Test Fold をモデル選択やハイパーパラメータ調整に一切関与させない（データリーク・過剰適合の防止）。
3. **評価指標**:
   - **Log Loss (Cross Entropy)**: 確率予測の総合的厳密性
   - **Brier Score**: 平均二乗確率誤差
   - **Expected Calibration Error (ECE)**: 信頼度と実績勝率の乖離度
   - **Top-1 Accuracy**: レース内予測確率第1位の馬の勝率
   - 各指標について 95% ブートストラップ信頼区間を算出

---

## 4. モデル採否判定基準 (Model Selection & Decision)

最終評価において、以下の条件をすべて満たした場合にのみ候補モデルを **`ACCEPTED`**（採用）と判定します:
- **$\Delta \text{Log Loss} < 0$**: ベースライン（`raw_uncalibrated`）に対して Log Loss が減少（予測精度向上）。
- **$\Delta \text{Brier Score} \le 0$**: Brier Score が悪化しないこと。
- **$\Delta \text{ECE} \le 0.02$**: 確率校正の極端な悪化がないこと。

1条件でも満たさない場合、または改善が再現しない場合は、誇張を排除し明確に **`REJECTED_NO_IMPROVEMENT`** と判定します。

---

## 5. CLI コマンド仕様

### 5.1 実験の実行 (`run`)
```bash
python scripts/phase18_model_improvement_experiment.py run \
  --seed 42 \
  --races 60 \
  --folds 3 \
  --output-report data/reports/phase18_experiment_report.json \
  --output-audit-log data/audit/phase18_audit_log.jsonl
```

### 5.2 実験結果サマリーの表示 (`summary`)
```bash
python scripts/phase18_model_improvement_experiment.py summary \
  --report data/reports/phase18_experiment_report.json
```

---

## 6. 安全性と免責事項
- **ROI ゲーティング**: 真正な発走前オッズ・検証済み公式払戻・外部公認データが未接続であるため、回収率（ROI）は算出不可（`roi_status: "unevaluable_unverified_data"`, `roi: null`）として永続化されます。
- **合成ベンチマークの免責**: 本実験は合成データ（Synthetic Benchmark）環境下でのアルゴリズム的・統計的検証であり、実競馬市場における金銭的利益や勝ち馬投票券の的中を保証するものではありません。
- **実馬券・自動投票の禁止**: 本プロジェクトは自動投票機能や馬券購入APIとの接続コードを一切含みません。
