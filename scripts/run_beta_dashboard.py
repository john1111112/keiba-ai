"""Read-only local web dashboard for the pre-race beta.

Uses Python stdlib only. No odds scraping, inference, betting or write APIs.
Bind to loopback only. Data is read afresh for each request.
"""
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def _safe_reports(reports_dir):
    root = Path(reports_dir)
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob("race_*.html")):
        if re.fullmatch(r"race_[0-9a-f]{20}\.html", path.name):
            found.append(path.name)
    return found


def _upcoming_rows(manifest_path, *, now=None):
    """Only display explicitly upcoming future races; never infer from results."""
    path = Path(manifest_path)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        current = now or datetime.now(timezone.utc)
        rows = {}
        for item in payload:
            if not isinstance(item, dict) or item.get("race_status") != "upcoming":
                continue
            race = item.get("race_id")
            raw = item.get("race_time") or item.get("start_time")
            if not isinstance(race, str) or not race.strip() or not isinstance(raw, str):
                continue
            start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if start.tzinfo is None or start.utcoffset() is None or start <= current:
                continue
            if race in rows and rows[race] != start:
                return []  # Ambiguous start time: fail closed
            rows[race] = start
        return sorted(rows.items(), key=lambda item: (item[1], item[0]))[:100]
    except (OSError, ValueError, TypeError):
        return []


def dashboard_html(reports_dir, *, refresh_seconds=60, manifest_path="data/upcoming_races.json", now=None):
    pages = _safe_reports(reports_dir)
    links = "".join(
        '<li><a href="/reports/' + html.escape(name, quote=True) + '">' +
        html.escape(name) + "</a></li>" for name in pages
    )
    if not links:
        links = "<li>まだレース分析レポートがありません。実際の発走前データの取得が必要です。</li>"
    upcoming = _upcoming_rows(manifest_path, now=now)
    race_rows = "".join(
        "<tr><td>" + html.escape(race) + "</td><td>" +
        html.escape(start.isoformat()) + "</td><td>NO BET</td></tr>"
        for race, start in upcoming)
    if not race_rows:
        race_rows = '<tr><td colspan="3">出走予定レースの入力がありません</td></tr>'
    now = html.escape((now or datetime.now(timezone.utc)).isoformat())
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>競馬AI β版</title><style>'
            'body{font-family:system-ui,sans-serif;background:#101725;color:#f0f3fa;'
            'max-width:900px;margin:40px auto;padding:0 18px}'
            'h1{font-size:2rem}.card{background:#202b3c;border-radius:14px;'
            'padding:20px;margin:18px 0}a{color:#90d8ff}'
            '.warning{border-left:4px solid #f4be57;padding-left:14px}'
            '</style></head><body><h1>🏇 競馬AI β版</h1>'
            '<div class="card warning"><strong>NO BET｜購入推奨は停止中</strong>'
            '<p>認証済みのリアルタイムオッズと検証済みAI勝率が未接続です。'
            '推測した勝率・期待値・おすすめレースは表示しません。</p></div>'
            '<div class="card"><h2>今後のレース（入力済み予定表）</h2>'
            '<table><thead><tr><th>レースID</th><th>発走予定</th><th>判定</th></tr></thead>'
            '<tbody>' + race_rows + '</tbody></table>'
            '<small>予定表は外部から明示的に入力された情報です。公式開催情報の自動取得ではありません。</small></div>'
            '<div class="card"><h2>レース分析レポート</h2><ul>'
            + links + '</ul></div>'
            '<div class="card"><h2>システム状態</h2>'
            '<p>データソース：未認証｜自動予測：未稼働｜通知：候補検証待ち</p>'
            f'<small>表示更新: {now} UTC／自動再読込 {refresh_seconds} 秒</small>'
            '</div></body></html>')


def make_handler(reports_dir, *, manifest_path="data/upcoming_races.json"):
    root = Path(reports_dir)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                body = dashboard_html(root, manifest_path=manifest_path).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif path == "/health":
                body = json.dumps({"status": "RUNNING", "bet_status": "NO_BET",
                                   "live_odds_connected": False,
                                   "model_certified": False}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            elif path.startswith("/reports/") and re.fullmatch(
                    r"/reports/race_[0-9a-f]{20}\.html", path):
                filename = path.removeprefix("/reports/")
                if not (root / filename).is_file():
                    self.send_error(404)
                    return
                body = (root / filename).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; style-src 'unsafe-inline'")
            else:
                self.send_error(404)
                return
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    parser = argparse.ArgumentParser(description="Local read-only Keiba AI beta dashboard")
    parser.add_argument("--reports-dir", default="data/prerace_reports")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--manifest", default="data/upcoming_races.json")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.reports_dir, manifest_path=args.manifest))
    print(f"競馬AI β版: http://127.0.0.1:{args.port}/ (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
