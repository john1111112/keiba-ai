"""Tests use injected transport; no paid feed or network is needed."""
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

from scripts.fetch_authorized_races import validate_feed, fetch_feed, run


NOW = "2026-10-27T12:00:00+09:00"
PAYLOAD = {
    "published_at": "2026-10-27T11:59:00+09:00",
    "races": [{"race_id": "race-1", "race_time": "2026-10-27T13:00:00+09:00",
               "race_status": "upcoming",
               "horses": [{"horse_number": 1, "horse_name": "Horse",
                           "win_odds": 4.2,
                           "odds_observed_at": "2026-10-27T11:58:00+09:00"}]}]}


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.payload


class AuthorizedFeedTests(unittest.TestCase):
    def test_valid_snapshot_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run("https://example.org/authorized", "test-token", tmp,
                         now=NOW, opener=lambda request, timeout: Response(PAYLOAD))
            self.assertEqual(result["race_count"], 1)
            self.assertEqual(result["bet_status"], "NO_BET")
            archive = json.loads(Path(result["archive"]).read_text(encoding="utf-8"))
            self.assertFalse(archive["provenance_certified"])
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest[0]["race_status"], "upcoming")
            self.assertNotIn("horses", manifest[0])

    def test_retry_then_success(self):
        attempts = []
        def flaky(request, timeout):
            attempts.append(1)
            if len(attempts) == 1:
                raise URLError("temporary")
            return Response(PAYLOAD)
        result = fetch_feed("https://example.org/feed", "token", opener=flaky,
                            sleep=lambda delay: None)
        self.assertEqual(result, PAYLOAD)
        self.assertEqual(len(attempts), 2)

    def test_bad_credentials_not_retried(self):
        def forbidden(request, timeout):
            raise HTTPError("https://example.org", 401, "unauthorized", {}, None)
        with self.assertRaises(ValueError):
            fetch_feed("https://example.org/feed", "token", opener=forbidden,
                       sleep=lambda delay: None)

    def test_http_and_no_token_blocked(self):
        with self.assertRaises(ValueError):
            fetch_feed("http://example.org", "token")
        with self.assertRaises(ValueError):
            fetch_feed("https://example.org", "")

    def test_future_odds_blocked(self):
        payload = json.loads(json.dumps(PAYLOAD))
        payload["races"][0]["horses"][0]["odds_observed_at"] = "2026-10-27T12:01:00+09:00"
        with self.assertRaises(ValueError):
            validate_feed(payload, received_at=NOW)

    def test_missing_odds_not_fabricated(self):
        payload = json.loads(json.dumps(PAYLOAD))
        payload["races"][0]["horses"][0].update(win_odds=None, odds_observed_at=None)
        self.assertEqual(validate_feed(payload, received_at=NOW)["runner_count"], 1)

    def test_duplicate_races_rejected(self):
        payload = json.loads(json.dumps(PAYLOAD))
        payload["races"].append(payload["races"][0])
        with self.assertRaises(ValueError):
            validate_feed(payload, received_at=NOW)


if __name__ == "__main__":
    unittest.main()
