# 複数レースの手動オッズJSONを一括登録する

手元で収集した発走前オッズJSONを、`data/manual_snapshots/` に保存します。形式は `docs/MANUAL_PRERACE_QUICKSTART.md` に記載されたものと同じです。

```powershell
python -m scripts.import_odds_folder data/manual_snapshots --db data/odds_history.db --dry-run
python -m scripts.import_odds_folder data/manual_snapshots --db data/odds_history.db --out data/import_report.json
python -m scripts.audit_prerace_history --db data/odds_history.db --out data/odds_quality_report.json
```

- ファイル名順に処理。すべてのJSONを検証し、問題のあるファイル名と理由をレポートします。
- 不正ファイルは削除・変更せず、そのファイルのみ登録しません。ほかの正常ファイルは登録できます。
- 失敗ファイルがある場合は終了コード1を返します。修正後の再実行が可能です。
- 同じ内容の再取り込みは重複登録せず、矛盾するオッズは拒否します。
- 発走時刻・観測時刻・取得時刻はJSON内に必須です。現在時刻からの推定・補完は行いません。
- 利用者申告の手動オッズであり、提供元や発表時刻の独立検証はされていません。**常に NO BET** です。

この機能は**ワンショット一括処理**です。JV-Linkや外部サイトへの自動接続・ポーリング・賭け操作は行いません。
