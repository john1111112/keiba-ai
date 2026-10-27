# CSVから発走前オッズを取り込む（無料・手動記録）

この機能は、利用者が**発走前に実際に確認した単勝オッズ**をCSVで取り込み、診断ダッシュボードを作るものです。データの提供時刻は独立検証されません。**自動オッズ配信ではなく、AI予測も実行しません。常に NO BET です。**

CSVファイル `data/my_odds.csv` をUTF-8で作成します。

```csv
horse_number,odds_value
1,4.5
2,12.0
3,6.8
```

Windows PowerShellでプロジェクト直下から、以下のように実行します。時刻は実際の確認時刻に変更してください。**例示の時刻を実データとして使用しないでください。**

```powershell
python -m scripts.import_prerace_csv data/my_odds.csv --race-id 202610260811 --race-start-time "2026-10-26T15:40:00+09:00" --observed-at "2026-10-26T15:30:00+09:00" --fetched-at "2026-10-26T15:30:05+09:00" --source manual_screen --dry-run
```

問題がなければ `--dry-run` を外すと、別DB `data/odds_history.db` に追記し `data/prerace_dashboard.html` を生成します。既存の `data/keiba.db` は変更しません。

- `observed_at`: 画面上でオッズが観測されたと利用者が申告する時刻
- `fetched_at`: その情報を取得・記録したと利用者が申告する時刻
- `race_start_time`: 発走予定時刻
- いずれもタイムゾーン必須。 `observed_at <= fetched_at < race_start_time` を満たす必要があります。
- 同一レース・同一馬・同一観測時刻の重複登録は冪等、値が変わった場合は一括拒否。
- CSVのカラムは `horse_number,odds_value` の2列のみです。
