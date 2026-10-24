# Phase 5 模擬運用：統合配布版

## できること
- 既存モデルを用いた予測保存の補助コード、予測用特徴量スナップショット、履歴オッズの時点検証
- 模擬運用台帳への記録（SQLite、追記のみ）
- 確認済みの単勝払戻CSVを検証してから取り込む（初期設定は dry-run）
- Streamlit `pages/01_模擬運用レポート.py` で集計を閲覧する（読み取り専用）
- CLIで模擬運用の状態を確認する

## まだできないこと
- JRAなどからの発走前オッズ・出走表・確定払戻の自動取得
- 実レースの無人運用と本番の回収率保証
- ZIPを作っただけでGitHubやStreamlitを自動更新すること

## インストールと確認
既存プロジェクトをバックアップしてから、同名の相対パスにファイルを配置する。Python依存パッケージは元プロジェクトの requirements.txt に従う。

```bash
python -m unittest discover -s scripts -p 'test_*.py' -q
python -m scripts.paper_operations --ledger data/paper_trading.db
python -m scripts.import_verified_settlements --ledger data/paper_trading.db --csv /path/to/verified.csv
# 内容を目視確認した後だけ適用
python -m scripts.import_verified_settlements --ledger data/paper_trading.db --csv /path/to/verified.csv --apply
```

払戻CSVの必須列: `race_id,horse_number,prediction_at,won,official_payout_yen,settled_at,source_url,verified_by`。
`prediction_at` と `settled_at` はタイムゾーン付きISO 8601、払戻金額は**100円当たりの確定払戻金額**。`source_url` は出典を記録するためのもので、URLの存在・内容を自動照合する機能ではない。**公式照合は人間が実施する必要がある。**

## 注意
`data/paper_trading.db` は個別に管理する。GitHub公開リポジトリへDB、Webhook、トークンを追加しない。Streamlit Community CloudのローカルSQLiteは再起動・再デプロイで永続性を保証できないため、実運用では外部の永続ストレージが必要。

GitHubへの書き込みは連携アプリの403エラーが解決していないため、**このZIPは未デプロイ**。
