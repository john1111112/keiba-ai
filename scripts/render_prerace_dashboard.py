"""Generate an offline, read-only pre-race dashboard; never imply a bet is approved."""
import argparse
import html
import json
from pathlib import Path

from scripts.prerace_readiness import race_readiness


def render_dashboard(report):
    escape = lambda x: html.escape(str(x), quote=True)
    alerts = report.get("odds_drop_alerts", [])
    items = "".join(
        "<tr><td>" + escape(a.get("horse_number")) + "</td><td>" +
        escape(a.get("previous_odds")) + "</td><td>" +
        escape(a.get("latest_odds")) + "</td><td>" +
        escape(a.get("drop_pct")) + "%</td></tr>"
        for a in alerts
    ) or '<tr><td colspan="4">有効な急落シグナルなし</td></tr>'
    return """<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>競馬AI 発走前監査</title><style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:1rem;
background:#0d1421;color:#f2f5fa}section{background:#1a2738;padding:1.4rem;
border-radius:12px;margin:1rem 0}.badge{color:#ffcf7c;font-weight:bold;font-size:2rem}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #44556b;
padding:.8rem;text-align:left}small{color:#b9c5d4}</style>
<h1>競馬AI｜発走前データ監査</h1><section><div class="badge">NO BET</div>
<p>データの完全性・取得元・AI予測が未認証のため購入推奨は行いません。</p>
<p>レースID: """ + escape(report.get("race_id", "")) + """</p>
<p>判定理由: """ + escape(report.get("reason", "UNKNOWN")) + """</p></section>
<section><h2>発走前の取得状況</h2><p>有効オッズ馬数: """ + escape(report.get("eligible_odds_horses", 0)) + """</p>
<p>予測レコード数: """ + escape(report.get("eligible_prediction_rows", 0)) + """</p>
<p>未認証・手入力オッズ: """ + escape(report.get("manual_or_uncertified_odds", 0)) + """</p>
<small>これらの数値は収益性・予測精度を保証しません。</small></section>
<section><h2>オッズ変動（参考情報のみ）</h2>
<table><thead><tr><th>馬番</th><th>前回</th><th>直近</th><th>下落率</th></tr></thead>
<tbody>""" + items + """</tbody></table>
<small>オッズ急落は大口投票の証拠ではありません。</small></section>
<section><h2>監査時刻</h2><p>as-of: """ + escape(report.get("as_of_time", "")) + """</p>
<p>発走予定: """ + escape(report.get("race_start_time", "")) + """</p></section>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--as-of-time", required=True)
    parser.add_argument("--race-start-time", required=True)
    parser.add_argument("--out", default="data/prerace_dashboard.html")
    args = parser.parse_args()
    report = race_readiness(args.db, args.race_id, as_of_time=args.as_of_time,
                            race_start_time=args.race_start_time)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_dashboard(report), encoding="utf-8")
    print(json.dumps({"status": "NO_BET", "html": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
