# ローカル発走前オッズ・ワンコマンド処理

JSONスナップショットをフォルダに入れるだけで、**取り込み → データ品質監査 → レース別HTMLダッシュボード**を一括実行できます。

```powershell
python -m scripts.run_local_odds_workflow data/manual_snapshots --db data/odds_history.db --out-dir data/prerace_reports --dry-run
python -m scripts.run_local_odds_workflow data/manual_snapshots --db data/odds_history.db --out-dir data/prerace_reports
```

入力形式は `docs/MANUAL_PRERACE_QUICKSTART.md` を参照。出力は `data/prerace_reports/quality_audit.json` と、レースごとの `race_<ハッシュ>.html` です。レースIDとファイル名を直接HTML出力パスに使用しません。

- 各JSONに実際の `race_id`、`race_start_time`、`observed_at`、`fetched_at`、`source`、`odds` が必要。
- 同一レースで発走予定時刻が矛盾した場合、**保存前に停止**します。
- 不正JSONはエラーを報告し、正常な別レースの処理を継続します。エラーがあれば終了コード1。
- `--dry-run` はDBもレポートも作りません。
- 同じデータの再実行は重複保存しません。入力データの独立検証はされません。
- **常に NO BET**。勝率の生成、EVの認証、オッズの自動取得、投票操作は行いません。

Windowsのタスクスケジューラでこのコマンドを定期実行することもできますが、入力JSONの取得・時刻記録は別途必要です。これを「自動オッズ取得」と呼ばないでください。

実行後は `data/prerace_reports/index.html` をブラウザで開くと、各レースの監査画面へのリンク一覧が表示されます。データはすべてローカルで、外部送信しません。

各レースのオッズ推移は `race_<ハッシュ>_timeline.json` にも出力します。`observed_at` と `fetched_at` が指定した as-of 時刻より後のレコード、発走後オッズ、確定オッズは含めません。これは研究用の時系列表示であり、独立に認証された相場履歴ではありません。
