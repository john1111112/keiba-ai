"""Generate an offline index of race dashboards and quality statistics.

No betting signals, predictions, or certification. Paths are restricted to
files created under the selected output directory.
"""
import argparse
import html
import json
from pathlib import Path


def render_index(workflow_result):
    escape = lambda value: html.escape(str(value), quote=True)
    cards = []
    for item in workflow_result.get("dashboards", []):
        path = Path(item["html"])
        filename = path.name
        if not filename.startswith("race_") or not filename.endswith(".html") or filename != path.name:
            continue
        cards.append(
            "<tr><td>" + escape(item["race_id"]) + "</td><td>NO BET</td>"
            "<td><a href=\"" + escape(filename) + "\">監査を見る</a></td></tr>"
        )
    rows = "".join(cards) or "<tr><td colspan=\"3\">有効なレースなし</td></tr>"
    batch = workflow_result.get("batch", {})
    return """<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>競馬AI 発走前データ一覧</title><style>
body{font-family:system-ui,sans-serif;max-width:980px;margin:2rem auto;
padding:1rem;background:#0d1421;color:#f2f5fa}section{background:#1a2738;
border-radius:12px;padding:1.4rem;margin:1rem 0}a{color:#8bc5ff}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #44556b;
padding:.8rem;text-align:left}.badge{color:#ffcf7c;font-size:2rem;font-weight:bold}
</style><h1>競馬AI｜発走前オッズ監査一覧</h1>
<section><div class="badge">NO BET</div><p>手動記録の未認証オッズ。投票推奨・期待値認証なし。</p>
<p>処理ファイル数: """ + escape(batch.get("files_found", 0)) + """</p>
<p>新規記録数: """ + escape(batch.get("inserted_rows", 0)) + """</p>
<p>拒否ファイル数: """ + escape(batch.get("rejected_files", 0)) + """</p></section>
<section><h2>レース別監査</h2><table><thead><tr><th>レースID</th>
<th>判定</th><th>レポート</th></tr></thead><tbody>""" + rows + """</tbody></table></section>
<section><a href="quality_audit.json">品質監査JSON</a></section></html>"""


def write_index(workflow_result, output_dir):
    target = Path(output_dir) / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_index(workflow_result), encoding="utf-8")
    return str(target)


def main():
    parser = argparse.ArgumentParser(description="Render offline NO BET race dashboard index")
    parser.add_argument("workflow_json", help="Saved workflow report JSON")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    report = json.loads(Path(args.workflow_json).read_text(encoding="utf-8"))
    print(json.dumps({"index": write_index(report, args.out_dir),
                      "bet_status": "NO_BET"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
