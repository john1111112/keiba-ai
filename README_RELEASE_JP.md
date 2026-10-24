# 競馬AI Phase 5 統合コード配布版（未デプロイ）

現行GitHub `john1111112/keiba-ai` のコードを基に、模擬運用・払戻照合・ダッシュボード・発走前オッズCSV取り込み・JV-Link連携用の準備モジュール・時点安全性修正を1つに整理しました。

**このZIPは差分コード配布用です。** 既存の `data/`、`models/`、認証情報は含めていません。既存リポジトリのバックアップを取ってから適用してください。現行本番への適用・実レースでの動作確認・JV-Link実通信は未実施です。

## 確認

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
# 以下はDB不要の主要テストを個別実行
python -m unittest discover -s scripts -p 'test_paper_trading.py' -v
python -m unittest discover -s scripts -p 'test_verified_settlement.py' -v
python -m unittest discover -s scripts -p 'test_pre_race_odds_import.py' -v
python -m scripts.import_pre_race_odds --help
```

## 未解決
- GitHubへの書き込みは連携権限403で不可。Streamlit本番も未更新。
- JRA-VAN加入前のため公式SDK・実データ接続は未確認。
- SQLiteの永続化、実際の予測モデルの精度、データライセンスの確認が必要。
- ファイルの重複・既存環境との互換性を完全保証するものではありません。

関連説明：PHASE5_INTEGRATION.md / ODDS_IMPORT.md / JRA_VAN_BRIDGE.md

## この環境での検証（2026-10-24）
- `tests/` の9件合格。
- `scripts/` の主要テスト37件合格（7+2+3+6+7+6+6）。
- 全スクリプトテスト61件を試したが4件は元の `data/keiba.db` やバックアップマニフェストがないためセットアップ失敗。失敗内容は `test_results.log` を参照。
- 実運用や実データ接続の検証ではない。
