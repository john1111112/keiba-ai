# Issue Automation Safety Gate & Dispatch Architecture

## 1. 概要 (Overview)
本ドキュメントは、GitHub Issues を契機とする Antigravity の定期監視・自動連携タスクにおいて、無制限な自動実装や予期せぬコード改変を防止し、人間の明示的な承認（Human-in-the-Loop）に基づく安全な運用を担保するための設計・運用仕様書である。

> [!IMPORTANT]
> **基本安全原則 (Fail-Closed by Default)**:
> GitHub Issue の本文やコメントにどれほど詳細な作業指示や「実行して」という文言が含まれていても、**ローカルの承認リスト（Allowlist）でユーザーが明示的に `approved_by_user: true` かつ `action_mode: "execute"` を指定しない限り、コード変更・コミット・プッシュ・本番反映は絶対に実行しない（Fail-Closed）**。

---

## 2. 承認ゲート設計 (Allowlist Approval Gate)

### 2.1 設定スキーマ (`config/issue_dispatch_allowlist.json`)
```json
{
  "repository": "john1111112/keiba-ai",
  "allowed_issues": {
    "<issue_number>": {
      "approved_by_user": false,
      "action_mode": "dry_run",
      "allowed_actions": [
        "check_activity",
        "dry_run_plan"
      ],
      "disallow_reasons": [
        "Automated code modification requires explicit human approval."
      ]
    }
  }
}
```

### 2.2 判定ルール
1. **未登録 Issue**: 判定 `DISALLOWED` $\rightarrow$ 処理スキップ。
2. **登録済み・未承認 (`approved_by_user: false` または `action_mode: "dry_run"`)**: 判定 `DRY_RUN_ONLY` $\rightarrow$ Issue メタデータの取得および計画確認のみ行い、ファイル書き込み・コード変更・コミットは一切スキップ。
3. **登録済み・明示的承認 (`approved_by_user: true` かつ `action_mode: "execute"`)**: 判定 `APPROVED` $\rightarrow$ 承認されたスコープの作業のみ実行可能。

---

## 3. 状態管理・重複起動防止・再実行防止 (Concurrency & State Invariants)

### 3.1 状態管理スキーマ (`data/issue_dispatch_state.json`)
処理の実行状況を永続化し、二重実行や競合を防止する。
```json
{
  "issues": {
    "<issue_number>": {
      "title": "Issue Title",
      "state": "OPEN | CLOSED",
      "last_checked_at": "ISO_TIMESTAMP",
      "last_decision": "DRY_RUN_ONLY | APPROVED | DISALLOWED",
      "action_mode": "dry_run | execute",
      "approved_by_user": false,
      "execution_skipped": true,
      "reason": "Detailed reason"
    }
  }
}
```

### 3.2 排他制御 (File Lock)
- 処理開始時にアトミックなファイルロック（`data/.dispatch.lock`）を取得。
- 先行タスクが実行中の場合は即座にスキップし、重複プロセスによる並列実行を阻止する。
- 終了時（正常・異常問わず）に確実にロックを解除する。

### 3.3 機密情報の完全保護 (Token & Secret Sanitization)
- GitHub Personal Access Token や環境変数は、状態ファイル、ログ、コミット、Issue コメントへ出力しない。

---

## 4. Antigravity 実環境のスケジュール機能と制約事項

| 項目 | 実装環境の現状 | 運用規約 |
| :--- | :--- | :--- |
| **定期スケジュール機構** | Antigravity 組み込み `schedule` ツール（CronExpression / IsDaemon） | チャット UI バックグラウンドで稼働。高優先度通知（`SYSTEM_MESSAGE`）により自律起動。 |
| **実行頻度** | 高頻度（5分）から安定運用（1時間）へ移行推奨 | サーバー負荷とトークン消費を抑えるため、通常時は 1時間間隔（`0 * * * *`）を標準とする。 |
| **CLI 環境 (`agy`)** | `agy` コマンドは現環境の PATH に未登録 | 存在しない CLI コマンドを前提とせず、実在する Python スクリプトおよび GitHub CLI（`gh.exe`）のみを使用。 |
| **GitHub CLI** | `C:\Program Files\GitHub CLI\gh.exe` にて認証済み | `gh issue view`, `gh issue comment` 等を安全に呼び出し可能。 |

---

## 5. 初回ドライラン検証結果 (Issue #11)

- **対象**: GitHub Issue #11 (`[Antigravity] Phase 13 無料データ候補調査とリーク防止オフライン評価`)
- **承認状態**: `approved_by_user: false`, `action_mode: "dry_run"`
- **判定結果**: `DRY_RUN_ONLY`
- **動作**: Issue メタデータを取得・検証。コード改変・ブランチ作成・プルリクエスト作成は一切行わず、安全にドライランを完了。
