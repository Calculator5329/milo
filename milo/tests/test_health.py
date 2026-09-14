import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from health.collect import collect
from health.server import make_server, render_page


class Response:
    def __init__(self, status=200, body=b""):
        self.status = status
        self.body = body

    def read(self):
        return self.body

    def getcode(self):
        return self.status


class HealthCollectionTests(unittest.TestCase):
    def test_collects_units_models_gpu_library_freshness_whisper_and_ledger(self):
        now = dt.datetime(2026, 9, 12, 18, 30, tzinfo=dt.timezone.utc)
        unit_output = "\n".join(
            [
                "ActiveState=active",
                "SubState=running",
                "ExecMainStartTimestamp=Sat 2026-09-12 17:00:00 UTC",
                "NRestarts=2",
            ]
        )
        units = {
            ("systemctl", "--user", "show", name, "-p", "ActiveState,SubState,ExecMainStartTimestamp,NRestarts"): unit_output
            for name in (
                "milo-experiment",
                "milo-demos",
                "milo-corner-demo",
                "kiwix-library",
                "whisper-server",
                "ollama",
            )
        }
        gpu_command = (
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        )
        units[gpu_command] = "3210, 16384, 42\n"

        def runner(command):
            return units[tuple(command)]

        def fetch(url, timeout=5):
            if url.endswith("/api/ps"):
                return 200, json.dumps({"models": [{"name": "gemma4:12b", "size": 123, "size_vram": 456, "expires_at": "2026-09-12T19:00:00Z"}]}).encode()
            if url.endswith("/api/tags"):
                return 200, json.dumps({"models": [{"name": "gemma4:12b"}, {"name": "llama3:8b"}]}).encode()
            if "catalog/v2/entries" in url:
                return 200, b"<feed xmlns='urn:atom'><entry><title>Book A</title></entry><entry><title>Book B</title></entry></feed>"
            if url.endswith("127.0.0.1:8178/"):
                return Response(status=204)
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freshness = root / "freshness.db"
            freshness.touch()
            import os

            os.utime(freshness, (now.timestamp() - 90, now.timestamp() - 90))
            ledger = root / "turns.jsonl"
            rows = [
                {"at": "2026-09-12T18:00:00+00:00", "app": "corner", "answer": "", "events": [], "metrics": {}},
                {"at": "2026-09-12T18:05:00+00:00", "app": "browser", "answer": "ok", "events": [], "metrics": {}},
                {"at": "2026-09-11T18:05:00+00:00", "app": "old", "answer": "", "events": [], "metrics": {}},
            ]
            ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            with patch("health.collect.DEFAULT_FRESHNESS_PATH", freshness):
                data = collect(now=now, runner=runner, fetch=fetch, ledger_path=ledger)

        self.assertTrue(data["units"]["ok"])
        self.assertEqual(data["units"]["items"]["milo-experiment"]["uptime_seconds"], 5400)
        self.assertEqual(data["models"]["resident"][0]["name"], "gemma4:12b")
        self.assertEqual(data["models"]["pulled_count"], 2)
        self.assertEqual(data["gpu"]["items"][0]["memory_used"], 3210)
        self.assertEqual(data["library"]["count"], 2)
        self.assertEqual(data["library"]["titles"], ["Book A", "Book B"])
        self.assertEqual(data["whisper"]["status"], 204)
        self.assertEqual(data["freshness"]["age_seconds"], 90)
        self.assertEqual(data["ledger"]["today_count"], 2)
        self.assertEqual(data["ledger"]["recent_flags"][0]["app"], "corner")
        self.assertNotIn("answer", data["ledger"]["recent_flags"][0])

    def test_every_probe_fails_soft(self):
        def broken_runner(command):
            raise RuntimeError("synthetic runner failure")

        def broken_fetch(url, timeout=5):
            raise OSError("synthetic fetch failure")

        with tempfile.TemporaryDirectory() as temp:
            with patch("health.collect.DEFAULT_FRESHNESS_PATH", Path(temp) / "missing.db"):
                data = collect(runner=broken_runner, fetch=broken_fetch, ledger_path=Path(temp))

        for name in ("units", "models", "gpu", "library", "whisper", "ledger"):
            self.assertFalse(data[name]["ok"], name)
            self.assertTrue(data[name]["error"], name)
        self.assertTrue(data["freshness"]["ok"])
        self.assertEqual(data["freshness"]["status"], "not built")


class HealthServerTests(unittest.TestCase):
    def test_page_renders_when_a_probe_fails(self):
        page = render_page({"units": {"ok": False, "error": "unit timeout"}})
        self.assertIn("unit timeout", page)
        self.assertIn('http-equiv="refresh" content="30"', page)
        self.assertIn("Today's ledger", page)

    def test_api_health_returns_json(self):
        expected = {"units": {"ok": True, "items": {}}}
        try:
            server = make_server(0, collector=lambda: expected)
        except PermissionError as error:
            self.skipTest(f"sandbox cannot bind loopback sockets: {error}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/health", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
