"""Authenticated, opt-in race feed transport. No scraping, no synthetic data.

The endpoint must be a contractually authorized HTTPS JSON feed returning:
{"published_at":"ISO+TZ","races":[{"race_id":"...", "race_time":"ISO+TZ",
"race_status":"upcoming","horses":[{"horse_number":1,"horse_name":"...",
"win_odds":4.2,"odds_observed_at":"ISO+TZ"}]}]}
No odds are treated as certified solely because they arrived over HTTPS.
"""
from __future__ import annotations
import argparse
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp")
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone required")
    return value.astimezone(timezone.utc)


def validate_feed(payload, *, received_at):
    now = timestamp(received_at)
    if not isinstance(payload, dict) or set(payload) != {"published_at", "races"}:
        raise ValueError("Expected published_at and races only")
    published = timestamp(payload["published_at"])
    if published > now:
        raise ValueError("Future feed publication")
    races = payload["races"]
    if not isinstance(races, list) or len(races) > 1000:
        raise ValueError("Invalid races list")
    ids = set()
    for race in races:
        if not isinstance(race, dict) or set(race) != {"race_id", "race_time", "race_status", "horses"}:
            raise ValueError("Invalid race schema")
        rid = race["race_id"]
        if not isinstance(rid, str) or not rid.strip() or len(rid) > 128 or rid in ids:
            raise ValueError("Invalid or duplicate race ID")
        ids.add(rid)
        start = timestamp(race["race_time"])
        if race["race_status"] not in ("upcoming", "cancelled", "postponed"):
            raise ValueError("Invalid race status")
        if race["race_status"] == "upcoming" and start <= now:
            raise ValueError("Upcoming race already started")
        horses = race["horses"]
        if not isinstance(horses, list) or len(horses) > 30:
            raise ValueError("Invalid runners")
        numbers = set()
        for horse in horses:
            if not isinstance(horse, dict) or set(horse) != {
                "horse_number", "horse_name", "win_odds", "odds_observed_at"
            }:
                raise ValueError("Invalid horse schema")
            n = horse["horse_number"]
            if type(n) is not int or not 1 <= n <= 30 or n in numbers:
                raise ValueError("Invalid or duplicate horse number")
            numbers.add(n)
            if not isinstance(horse["horse_name"], str) or not horse["horse_name"].strip():
                raise ValueError("Missing horse name")
            odds = horse["win_odds"]
            if odds is not None:
                import math
                if type(odds) not in (int, float) or not math.isfinite(odds) or odds < 1:
                    raise ValueError("Invalid win odds")
                observed = timestamp(horse["odds_observed_at"])
                if observed > now or observed > published or observed >= start or now >= start:
                    raise ValueError("Not a valid pre-race odds observation")
            elif horse["odds_observed_at"] is not None:
                raise ValueError("Odds timestamp without odds")
    return {"status": "VALIDATED_UNCERTIFIED", "race_count": len(races),
            "runner_count": sum(len(r["horses"]) for r in races),
            "bet_status": "NO_BET"}


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".feed-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirected feed endpoints are not allowed")


def fetch_feed(url, token, *, attempts=3, timeout=10, sleep=time.sleep, opener=None):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Authorized HTTPS feed URL required")
    if not token:
        raise ValueError("Missing feed API token")
    if not 1 <= attempts <= 5 or not 1 <= timeout <= 30:
        raise ValueError("Invalid retry/timeout configuration")
    opener = opener or urllib.request.build_opener(_NoRedirect()).open
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json", "User-Agent": "KEIBA-AI-AuthorizedFeed/1.0"})
    for attempt in range(attempts):
        try:
            with opener(request, timeout=timeout) as response:
                content = response.read(2_000_001)
                if len(content) > 2_000_000:
                    raise ValueError("Feed exceeds size limit")
                return json.loads(content.decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 401, 403, 404):
                raise ValueError("Feed authorization or endpoint rejected") from exc
            if attempt == attempts - 1:
                raise RuntimeError("Feed unavailable after retries") from exc
            sleep(min(2 ** attempt, 8))
    raise RuntimeError("Feed unavailable")


def run(url, token, out_dir, *, attempts=3, now=None, opener=None, sleep=time.sleep):
    received = now or datetime.now(timezone.utc).isoformat()
    payload = fetch_feed(url, token, attempts=attempts, opener=opener, sleep=sleep)
    report = validate_feed(payload, received_at=received)
    folder = Path(out_dir)
    # Timestamped immutable history; no fabricated odds, no certified inference.
    stamp = timestamp(received).strftime("%Y%m%dT%H%M%S%fZ")
    archive = folder / "snapshots" / (stamp + ".json")
    if archive.exists():
        raise FileExistsError("Snapshot timestamp collision")
    atomic_json(archive, {"received_at": received, "feed": payload,
                          "provenance_certified": False})
    manifest = [{"race_id": r["race_id"], "race_status": r["race_status"],
                 "race_time": r["race_time"]} for r in payload["races"]]
    atomic_json(folder / "upcoming_races.json", manifest)
    atomic_json(folder / "last_feed_status.json", {**report, "received_at": received,
                                                    "archive": str(archive)})
    return {**report, "received_at": received, "archive": str(archive),
            "manifest": str(folder / "upcoming_races.json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Authorized HTTPS race/runner/odds feed")
    parser.add_argument("--url", default=os.getenv("KEIBA_FEED_URL"))
    parser.add_argument("--out-dir", default="data/authorized_feed")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("Set KEIBA_FEED_URL to your licensed feed endpoint")
    result = run(args.url, os.getenv("KEIBA_FEED_TOKEN"), args.out_dir,
                 attempts=args.attempts)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
