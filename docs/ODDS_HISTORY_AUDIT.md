# 発走前オッズ履歴の品質監査（無料・読み取り専用）

手動CSV/JSONで蓄積したオッズについて、発走前の時刻・出所・馬番・価格の品質を検査し、レースごとの記録数と不正行の理由をJSON出力します。

```powershell
python -m scripts.audit_prerace_history --db data/odds_history.db --out data/odds_quality_report.json
```

特定時刻までの情報だけに絞る場合：

```powershell
python -m scripts.audit_prerace_history --db data/odds_history.db --as-of-time "2026-10-26T15:30:00+09:00"
```

発走時刻の検証も必要な場合は、`race_starts.json` に `{"202610260811": "2026-10-26T15:40:00+09:00"}` のように実際のレースIDと発走予定時刻を記入して、`--race-starts-json race_starts.json` を指定します。これを指定しなければ、発走後混入の判定はできません。

**注意:** 記録数が多いことは、オッズの提供元・発表時刻が独立に検証されたことを意味しません。すべて `NO_BET`、`asof_certified=false` として出力します。実オッズ配信の自動接続や期待値の認証は行いません。
