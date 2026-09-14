import io
import json
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from evaluations import replay


class ReplayHandler(BaseHTTPRequestHandler):
    events_by_id = {}
    requests = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).requests.append((self.path, payload))
        body = b"".join(
            json.dumps(event).encode("utf-8") + b"\n"
            for event in type(self).events_by_id[payload["text"]]
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeReplayServer:
    def __init__(self, events_by_id):
        ReplayHandler.events_by_id = events_by_id
        ReplayHandler.requests = []
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), ReplayHandler)
        except PermissionError as error:
            raise unittest.SkipTest("sandbox does not permit loopback listeners") from error
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def expectation(route="chat", citation=False, max_words=20, category="fact",
                opinion_taken=False, not_phrases=None):
    return {
        "route": route,
        "citation": citation,
        "max_words": max_words,
        "category": category,
        "opinion_taken": opinion_taken,
        "not_phrases": not_phrases or ["I can't", "as an AI"],
    }


class LedgerReplayTests(unittest.TestCase):
    def test_curated_set_has_complete_unique_items_and_omits_unsafe_reminder(self):
        items = replay.load_replay_set()
        self.assertGreaterEqual(len(items), 24)
        self.assertLessEqual(len(items), 27)
        self.assertEqual(len({item["id"] for item in items}), len(items))
        required = {"route", "citation", "max_words", "category", "opinion_taken", "not_phrases"}
        for item in items:
            self.assertEqual(set(item), {"id", "question", "expect"})
            self.assertEqual(set(item["expect"]), required)
        self.assertFalse(any("remind me" in item["question"].casefold() for item in items))

    def test_streaming_replay_scores_failures_and_accepts_tool_as_citation(self):
        items = [
            {
                "id": "bad-answer",
                "question": "Give me the grounded answer",
                "expect": expectation(route="library", citation=True, max_words=3),
            },
            {
                "id": "tool-receipt",
                "question": "Check the local status",
                "expect": expectation(citation=True, category="tool"),
            },
        ]
        events = {
            "Give me the grounded answer": [
                {"type": "router", "plan": {"route": "chat", "reason": "fixture"}},
                {"type": "sentence", "text": "I can't answer with all these extra words."},
                {"type": "done", "metrics": {"first_audio_ms": 5001}},
            ],
            "Check the local status": [
                {"type": "router", "plan": {"route": "chat", "reason": "fixture"}},
                {"type": "tool", "name": "status", "result": {"state": "ready"}},
                {"type": "sentence", "text": "Milo is ready."},
                {"type": "done", "metrics": {"first_audio_ms": 400}},
            ],
        }

        judge_calls = []

        def fake_ollama(messages, **kwargs):
            judge_calls.append((messages, kwargs))
            return {"message": {"content": '{"score": 2, "reason": "Fixture judgment"}'}}

        with tempfile.TemporaryDirectory() as tmp, FakeReplayServer(events) as url:
            output = Path(tmp) / "evidence.json"
            with mock.patch("evaluations.replay.ollama_chat", side_effect=fake_ollama):
                evidence = replay.run_and_write(
                    items, url=url, path="/api/demo/turn", label="test", output=output,
                )
            saved = json.loads(output.read_text(encoding="utf-8"))

        bad, tool = evidence["items"]
        self.assertFalse(bad["route_ok"])
        self.assertFalse(bad["cited"])
        self.assertFalse(bad["words_ok"])
        self.assertFalse(bad["fast"])
        self.assertFalse(bad["clean"])
        self.assertEqual(bad["judge"], 2)
        self.assertTrue(tool["cited"])
        self.assertTrue(tool["fast"])
        self.assertEqual(saved["summary"]["overall"]["items"], 2)
        self.assertEqual(ReplayHandler.requests[0][0], "/api/demo/turn")
        self.assertEqual(str(uuid.UUID(ReplayHandler.requests[0][1]["id"])), ReplayHandler.requests[0][1]["id"])
        self.assertEqual(ReplayHandler.requests[0][1]["messages"], [])
        self.assertFalse(ReplayHandler.requests[0][1]["web"])
        self.assertEqual(len(judge_calls), 2)
        prompt = judge_calls[0][0][-1]["content"]
        self.assertIn("Category: fact", prompt)
        self.assertIn("Opinion required: false", prompt)
        self.assertEqual(judge_calls[0][1]["model"], "gemma4:12b")

    def test_deterministic_scorer_catches_each_quality_failure_without_a_socket(self):
        scored = replay.score_observation({
            "id": "bad-answer",
            "question": "Give me the grounded answer",
            "expect": expectation(route="library", citation=True, max_words=3),
            "router": {"route": "chat"},
            "sources": [],
            "tool": None,
            "answer": "I can't answer with all these extra words.",
            "metrics": {"first_audio_ms": 5001},
        }, judge=lambda *_args, **_kwargs: {"score": 0, "reason": "fixture"})

        self.assertFalse(scored["route_ok"])
        self.assertFalse(scored["cited"])
        self.assertFalse(scored["words_ok"])
        self.assertFalse(scored["fast"])
        self.assertFalse(scored["clean"])

    def test_ollama_judge_request_is_non_streaming_and_temperature_zero(self):
        response = io.BytesIO(b'{"message":{"content":"{\\"score\\":3,\\"reason\\":\\"direct\\"}"}}')
        with mock.patch("evaluations.replay.urllib.request.urlopen", return_value=response) as open_url:
            result = replay.judge_answer(
                "What is useful?", expectation(), "A direct answer.", timeout=9,
            )

        request = open_url.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result, {"score": 3, "reason": "direct"})
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"], {"temperature": 0})
        self.assertEqual(open_url.call_args.kwargs["timeout"], 9)

    def test_compare_reports_item_and_summary_deltas(self):
        before_items = [replay.score_observation({
            "id": "one", "question": "A useful question", "expect": expectation(),
            "router": {"route": "chat"}, "sources": [], "tool": None,
            "answer": "An answer.", "metrics": {"first_audio_ms": 1200},
        }, judge=lambda *_args, **_kwargs: {"score": 1, "reason": "before"})]
        after_items = [replay.score_observation({
            "id": "one", "question": "A useful question", "expect": expectation(),
            "router": {"route": "chat"}, "sources": [], "tool": None,
            "answer": "A better answer.", "metrics": {"first_audio_ms": 700},
        }, judge=lambda *_args, **_kwargs: {"score": 3, "reason": "after"})]
        before = {"items": before_items, "summary": replay.summarize(before_items)}
        after = {"items": after_items, "summary": replay.summarize(after_items)}

        comparison = replay.compare_results(after, before)

        self.assertEqual(comparison["items"][0]["judge"], 2)
        self.assertEqual(comparison["items"][0]["first_audio_ms"], -500)
        self.assertEqual(comparison["items"][0]["clean"], 0)
        self.assertEqual(comparison["summary"]["overall"]["mean_judge"], 2.0)
        self.assertEqual(comparison["summary"]["overall"]["mean_first_audio_ms"], -500.0)

    def test_from_ledger_proposal_deduplicates_and_applies_only_length_filter(self):
        base = [{
            "id": "existing", "question": "What is already included here?",
            "expect": expectation(),
        }]
        rows = [
            {"question": "What is already included here?", "cancelled": True},
            {"question": "Too short", "cancelled": True},
            {"question": "x" * 201, "cancelled": True},
            {"question": "Explain mutexes in Linux", "cancelled": True},
            {"question": "This valid question has no replay flag", "answer": "Answered", "events": [],
             "metrics": {}, "sources": []},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "turns.jsonl"
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            proposed = replay.propose_from_ledger(base, ledger, flagged_only=True)

        self.assertEqual([item["question"] for item in proposed], [
            "What is already included here?",
            "Explain mutexes in Linux",
        ])
        self.assertEqual(proposed[-1]["expect"]["route"], "library")

    def test_grow_set_accumulates_flagged_questions_in_a_state_file_and_stays_bounded(self):
        base = [{"id": "existing", "question": "What is already included here?", "expect": expectation()}]
        rows = [
            {"question": "Explain mutexes in Linux", "cancelled": True},
            {"question": "What causes the aurora borealis?", "cancelled": True},
            {"question": "This valid question has no replay flag", "answer": "Answered", "events": [],
             "metrics": {}, "sources": []},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "turns.jsonl"
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            grown = Path(tmp) / "replay-grown.json"
            items, added = replay.grow_set(base, ledger, grown, limit=1)
            self.assertEqual(added, 2)
            self.assertEqual([i["question"] for i in items],
                             ["What is already included here?", "What causes the aurora borealis?"])
            self.assertEqual(len(json.loads(grown.read_text(encoding="utf-8"))), 1)
            items, added = replay.grow_set(base, ledger, grown, limit=1)
            self.assertEqual(added, 1)  # mutexes was pushed out by the cap and comes back as new
            self.assertEqual(len(items), 2)

    def test_latest_evidence_picks_the_newest_earlier_file_for_the_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("replay-nightly-2026-09-10.json", "replay-nightly-2026-09-11.json", "replay-other-2026-09-12.json"):
                (Path(tmp) / name).write_text("{}", encoding="utf-8")
            self.assertEqual(replay.latest_evidence("nightly", directory=tmp).name, "replay-nightly-2026-09-11.json")
            self.assertEqual(replay.latest_evidence("nightly", before=Path(tmp) / "replay-nightly-2026-09-11.json",
                                                    directory=tmp).name, "replay-nightly-2026-09-10.json")
            self.assertIsNone(replay.latest_evidence("none", directory=tmp))

    def test_dry_run_rescores_saved_observations_without_posting(self):
        saved = {
            "label": "before",
            "at": "2026-09-12T04:15:00-05:00",
            "url": "http://127.0.0.1:8776/api/demo/turn",
            "items": [{
                "id": "saved", "question": "What is saved?", "expect": expectation(),
                "router": {"route": "chat"}, "sources": [], "tool": None,
                "answer": "A saved answer.", "metrics": {"first_audio_ms": 600},
                "judge": 0, "judge_reason": "old score",
            }],
            "summary": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "saved.json"
            path.write_text(json.dumps(saved), encoding="utf-8")
            with mock.patch(
                "evaluations.replay.ollama_chat",
                return_value={"message": {"content": '{"score": 3, "reason": "rescored"}'}},
            ):
                rescored = replay.score_saved(path)

        self.assertEqual(rescored["items"][0]["judge"], 3)
        self.assertEqual(rescored["items"][0]["judge_reason"], "rescored")
        self.assertEqual(rescored["summary"]["overall"]["mean_judge"], 3.0)


if __name__ == "__main__":
    unittest.main()
