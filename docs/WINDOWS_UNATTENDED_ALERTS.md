# Windows常駐通知（β版・ローカル）

`scripts/run_unattended_alerts.py` は**アプリ画面を開かずに**実行できる通知判定ランナーです。別プロセスとしてWindowsタスクスケジューラから定期実行できます。

**重要:** 現在のシステムには、独立検証済みのリアルタイムオッズ提供元も、校正済み本番AIモデルもありません。したがって、実データから自動的に有効な通知候補を生成する処理はまだありません。候補ファイルに虚偽の `*_certified=true` を書いて本番運用しないでください。

## 実行例

まずは副作用のないドライランで確認：

```powershell
python -m scripts.run_unattended_alerts --candidates data/verified_alert_candidates.json
```

認証済みデータ提供元とモデルが完成・検証された後、Windowsのログオン中に限り：

```powershell
python -m scripts.run_unattended_alerts --candidates data/verified_alert_candidates.json --deliver
```

`--deliver` がない限り通知もSQLite書き込みも行いません。通知済み候補は `data/alert_state.db` に保存し、同じレース・馬番への再通知を抑制します。オッズ取得後2分以内、発走30〜5分前、期待値115%以上、認証済み時刻・モデル・取得元を満たす場合のみ通知可能。現時点では安全のため **NO BET** です。

## Windowsタスクスケジューラでの定期実行

通知処理自体は、タスクスケジューラから `python.exe -m scripts.run_unattended_alerts --candidates ... --deliver` を呼び出す設計です。作業フォルダをリポジトリのルートに指定してください。タスクは「ユーザーがログオンしている場合のみ実行」を選びます。バルーン通知はWindows環境・通知設定によって表示されない場合があり、実機検証が必要です。

**注意:** 現在は自動取得や候補生成が未実装なので、タスクを設定してもおすすめレースが自動的に発見されるわけではありません。PCの電源OFFやスリープ中は実行されません。通知表示に失敗した場合は失敗数を返し、同じ候補への再試行を抑制します。
