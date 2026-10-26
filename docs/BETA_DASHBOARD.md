# 競馬AI β版ローカル画面

Python標準ライブラリのみで起動する、読み取り専用のローカル画面です。Windowsのブラウザから開けます。

Windowsではリポジトリ直下の **`START_KEIBA_AI.bat` をダブルクリック**するとブラウザが開き、画面サーバーが起動します。Python 3のインストールが必要です。バッチファイルのウィンドウを閉じると画面サーバーも終了します。Windows実機での確認は未実施です。

コマンドから起動する場合は、リポジトリのルートで実行：

```powershell
python -m scripts.run_beta_dashboard
```

ブラウザで `http://127.0.0.1:8765/` を開きます。画面は60秒ごとに自動更新され、`scripts/run_local_odds_workflow.py` が出力した既存のレース分析HTMLを一覧表示します。ローカルPC以外からはアクセスできません。停止はCtrl+C。

現時点で表示できるのは**手動取得データの分析レポートとシステムの未接続状態**です。リアルタイムオッズ取得、校正済みAI勝率、期待値による購入候補表示はまだ接続されていません。画面は架空の予測値を表示せず、`NO BET` を明示します。実機での起動確認は別途必要です。

## 発走前オッズ一覧の表示条件

`data/upcoming_races.json` に `race_status: upcoming`、`race_id`、タイムゾーン付きの `race_time` または `start_time` が登録されており、`data/odds_history.db` に対応する単勝オッズが存在する場合のみ表示します。提供元の観測時刻とローカル取得時刻がともに現在以前かつ発走以前で、取得後10分以内の値だけを表示します。確定オッズは除外します。古いオッズを最新値として見せないため、10分を超えたら一覧から消えます。これらは**時刻整合性の検査**であり、データの独立した真偽認証ではありません。

## Windowsのバックグラウンド自動スキャン

画面を開かなくても5分ごとに出走予定レースの発走30・10・5分前を確認するためのタスクを用意しています。**デフォルトではインストールされません。** Windows PowerShellでリポジトリ直下から次を実行してください。

```powershell
# まず設定を確認（変更なし）
.\scripts\install_windows_scan_task.ps1

# ログイン中のWindowsで5分ごとに実行
.\scripts\install_windows_scan_task.ps1 -Install -IntervalMinutes 5

# 削除
.\scripts\install_windows_scan_task.ps1 -Remove
```

スキャン結果は `data/last_background_scan.json` に保存されます。開催予定表がなければ `NO_UPCOMING_MANIFEST`、実オッズ提供元が未接続なら取得なしとして記録します。**このタスクは通知・予測・購入を実行しません。** Windowsへのログイン中のみ実行する構成で、Windows実機検証は未実施です。自動通知については `WINDOWS_UNATTENDED_ALERTS.md` を参照してください。現在は出所を独立認証できないため通知は停止中です。

画面の「バックグラウンド確認」欄では、`data/last_background_scan.json` の最終スキャン状態（例：`COMPLETED`、`SCAN_BLOCKED`）を確認できます。これは実行処理の状態であり、オッズや予測の真偽・精度を証明するものではありません。予定表が壊れている場合もタスクは `SCAN_BLOCKED` として結果を保存し、架空のレースや購入推奨を生成しません。

## 出走予定ファイルの安全な形式

`data/upcoming_races.json` はJSON配列で、各要素には `race_id`、`race_status`、`race_time`（または `start_time`）のみを指定します。例：

```json
[
  {"race_id":"example-upcoming-race","race_status":"upcoming","race_time":"2099-01-01T15:00:00+09:00"}
]
```

この例は**形式説明用の架空ID**であり、実在のレースではありません。運用時は実際の開催情報を入力してください。結果順位、確定オッズ、AI予測などの列を混在させるとスキャンは拒否します。
