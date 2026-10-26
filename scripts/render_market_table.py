"""Render verified-as-of *data availability*, not betting advice, as HTML."""
from __future__ import annotations
import html


def render_market_table(timeline, movement=None):
    esc = lambda v: html.escape(str(v), quote=True)
    latest = {}
    for snapshot in timeline.get("observations", []):
        for horse in snapshot.get("horses", []):
            latest[horse["horse_number"]] = (horse, snapshot["observed_at"])
    changes = {row["horse_number"]: row for row in (movement or {}).get("changes", [])}
    rows = []
    for number, (horse, observed) in sorted(latest.items()):
        change = changes.get(number)
        change_text = ("{:+.1f}%".format(change["change_percent"])
                       if change else "—")
        rows.append("<tr><td>" + esc(number) + "</td><td>" +
                    esc(horse["win_odds"]) + "</td><td>" + esc(change_text) +
                    "</td><td>" + esc(observed) + "</td><td>" +
                    esc(horse["fetched_at"]) + "</td></tr>")
    body = "".join(rows) or '<tr><td colspan="5">取得済みオッズなし</td></tr>'
    return ("""<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>競馬AI オッズ一覧（監査用）</title>
<style>body{font:16px system-ui;background:#0d1421;color:#eee;max-width:1100px;
margin:2rem auto;padding:1rem}table{width:100%;border-collapse:collapse}
td,th{padding:.8rem;border-bottom:1px solid #536074;text-align:left}
small{color:#b9c5d4}.warn{color:#ffd17a}</style>
<h1>発走前オッズ一覧</h1><p class="warn">NO BET — 未認証データ。
勝率・期待値・購入推奨ではありません。</p>
<p>レースID: """ + esc(timeline.get("race_id", "")) + """</p>
<table><thead><tr><th>馬番</th><th>単勝オッズ</th><th>変動</th>
<th>発表時刻</th><th>取得時刻</th></tr></thead><tbody>""" +
body + """</tbody></table><small>オッズ変動は比較可能な2時点がある場合のみ表示。
時刻は発表元の記録と取得記録に基づきます。</small></html>""")
