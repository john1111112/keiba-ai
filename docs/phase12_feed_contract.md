# Phase 12: Licensed Pre-Race Feed Contract Specification

## 1. 概要 (Overview)
本仕様書は、Phase 11 の SHA-256 原本整合性検証を土台とし、**ライセンス上利用許諾が確認された発走前オッズフィード**を安全・確実にシステムへ取り込むためのプロバイダー非依存インターフェース契約（Feed Contract）を定義する。

> [!IMPORTANT]
> **未確定事項および安全前提**:
> 現時点において、正式な外部提供元、API仕様、認証情報、およびデータ利用許諾契約は未確定である。
> 本契約仕様は、将来の正式契約に基づくフィード提供を安全に迎え入れるための抽象規約であり、**現段階でJRA公式や特定プロバイダーとの連携が存在・成立したと主張するものではない**。

---

## 2. フィード契約データモデル (Contract Schema)

フィードアダプターが取り込み処理を行う際、以下の全フィールドを網羅・検証する。

| フィールド名 | 型 | 必須 | 説明・規約 |
| :--- | :---: | :---: | :--- |
| `provider_id` | string | 必須 | プロバイダー識別子（例: `licensed-mock`, `fixture-local-test`）。未承認の名称や詐称は禁止。 |
| `source_identifier` | string | 必須 | フィクスチャファイルパスまたは論理データソース名。**APIキーやトークン等の機密情報は一切記録禁止**。 |
| `observed_at_utc` | string (ISO 8601) | 必須 | ローカルシステムがデータを受信・観測した確定UTC日時。入力データの自己申告時刻は証拠として扱わず、本時刻を排他的に採用。 |
| `upstream_published_at_utc` | string (ISO 8601) / null | 任意 | 提供元が公式メタデータとして明記している発表時刻（存在しない・不明な場合は `null`。推測による補完は絶対禁止）。 |
| `race_id` | string | 必須 | レース識別子（数字・英数字）。フィード内全レコードと完全一致が必須。 |
| `horse_number` | integer | 必須 | 馬番（1〜18の自然数）。レース内で重複禁止、欠損禁止。 |
| `odds_value` | float | 必須 | 単勝オッズ数値（1.0以上）。欠損、文字列混入、ゼロ以下は拒絶。 |
| `odds_unit` | string | 必須 | オッズ表現形式（例: `decimal_multiplier`）。日本競馬の単勝配当率表記に準拠。 |
| `data_type` | string | 必須 | データ種別。本契約では `win_odds`（単勝確定前オッズ）に限定。 |
| `license_status` | string | 必須 | ライセンス・利用許諾確認状態。`verified_licensed` のみ受入れ可。`unverified` 等は完全拒絶。 |
| `raw_sha256` | string (HEX 64) | 必須 | 受信した原本バイト列の SHA-256 チェックサム。改ざん検知の原本証明。 |
| `ingest_status` | string | 必須 | 取り込み判定結果（`accepted` または `rejected`）。 |
| `rejection_reason` | string / null | 任意 | 取り込み拒絶時の厳密な理由コード（成功時は `null`）。 |

---

## 3. 取り込み・受入れ基準 (Ingestion Invariants & Fail-Closed Rules)

アダプターは以下の不変条件（Invariants）を強制し、違反が検知された場合は **即座に例外を発生（Fail-Closed）** させて処理を中断する。

1. **ライセンスの厳格検証 (Strict License Enforcement)**:
   - `license_status` が `verified_licensed` 以外（`unverified`, `unlicensed`, 未指定など）の場合、いかなるデータも取り込みを拒絶する。
2. **時間の厳格性 (Temporal Integrity)**:
   - **観測時刻の独自記録**: フィード内の `observed_at` や `fetched_at` などの自己申告時刻を真正性の証拠として扱わず、アダプター処理実行時のローカル確定UTC時刻（`observed_at_utc`）を測定・記録する。
   - **発走前制約**: `observed_at_utc >= race_start_at` の場合、発走後データの混入（データリーク）として直ちに拒絶する。
   - **未来時刻拒絶**: `observed_at_utc > current_time + 60s` の場合、時計異常として拒絶する。
   - **提供元時刻と観測時刻の厳密分離**: 提供元の発表時刻（`upstream_published_at_utc`）と受信観測時刻（`observed_at_utc`）を同一視・混同しない。
3. **レースID & カージナリティ整合性**:
   - 入力ファイル内のすべての行が指定の `race_id` と一致しなければならない。
   - 出走馬番（`horse_number`）に重複が存在してはならない。
4. **オッズ値の健全性**:
   - 単勝オッズは 1.0 以上の正の実数でなければならず、NaN、Inf、負数、空欄は即座に拒絶する。
5. **暗号学的原本性証明 (Cryptographic Attestation)**:
   - 原本バイト列の SHA-256 ダイジェストを計算し、Phase 10 のスナップショット原本および Phase 11 のマニフェスト照合チェーンと完全に接続する。
