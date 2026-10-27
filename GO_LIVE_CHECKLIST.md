# KEIBA AI 本番納品条件（2026-10-27）

## 完成の定義

Streamlitの画面を起動せず、Windows PCが稼働・ログインしている間、JRA開催日の全レースを自動取得・分析して、買い候補があればスマホのDiscordへ通知できること。自動購入はしない。

**個人用ベータ納品の合格条件：以下の4工程を実機で全件確認する。** モックテストのPASSは実機成功とは区別する。

| 工程 | 合格条件 | 証拠 |
| --- | --- | --- |
| ① JRA-VAN取得 | 実際の全レースのRA/SEと対象のO1単勝オッズを取得、開催情報と件数照合 | data/authorized_feed/latest_jvlink_feed.json |
| ② AI予測 | 実レースから勝率・期待値を計算し、欠損は欠損として扱う | data/last_background_scan.json の processed_races > 0 |
| ③ Discord通知 | 買い候補がWebhookに受理され、利用者のスマホで通知を確認、重複なし | alert_state.db と利用者スマホ |
| ④ 起動不要の監視 | Windows再起動後・Streamlitを開かずに毎分実行、翌開催日も継続 | KeibaAI_BackgroundMonitor、monitor_daemon_status.json |

## コード側ですでに用意済み

- JV-Linkの生データ解析・RA/SE/O1統合、28特徴量モデル・勝率・期待値
- 毎分Windowsタスク、欠損/非開催日の処理、Discord通知の失敗再試行と重複抑制
- Windows/Linux上の模擬データCIテスト
- CHECK_RELEASE.bat による読み取り専用の導入チェック

## 現時点の未確認事項

- 対象PCに32bit/64bit版JV-Link SDKがあり、正式な利用権限が使えるか
- 実開催日のJRA-VANレース・オッズ取得、モデル入力、Webhook送信が本当に成功するか
- 本人のスマホにプッシュが届くか、Windows再起動後もログイン状態で監視を再開するか

## 利用者PCでの最短導入

1. JRA-VAN Data LabのSDKと使用権限を用意（登録・無料体験は本人の許可が必要）。
2. Windowsにこのブランチのコードを配置し、INSTALL_AUTO_MONITOR.bat を一度だけ実行（専用Python環境・自動実行タスクを構築）。
3. Discord Webhookと必要なら本人のDiscordユーザーIDを通知設定に登録。
4. CHECK_RELEASE.bat を実行。PENDING項目のNEXTだけ対応。これ自体はJVInitもDiscord送信もしない。
5. JRA開催日に全R取得→AI判定→スマホ通知を確認したら、画面を閉じて再起動・ログイン後も監視することを確認。ここで納品。

**PCの電源がOFF・スリープ中は監視できない。** 現在の構成はWindowsログイン中にアプリ画面を開かず監視するもの。

今後は「実装済み」「模擬CI合格」「実機確認済み」を区別し、根拠のない完成％を表示しない。
