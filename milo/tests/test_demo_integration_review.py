"""Read-only integration probes for the Milo demo review.

These tests use injected transports and engine doubles.  They do not bind a
port, contact Ollama, or mutate the demo document workspace.
"""
import io
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_server
import drafting
from conversation import build_messages, ollama_model_options, _context_units
from demo_server import DemoEngine, demo_handler
from server import Engine, Turn


def engine_double(documents):
    engine = DemoEngine.__new__(DemoEngine)
    engine.documents = documents
    engine.last_draft = None
    engine.action_receipts = []
    engine.lock = threading.Lock()
    engine.turn_lock = threading.Lock()
    engine.turns = {}
    return engine


class Documents:
    def __init__(self, document=None):
        self.document = document

    def read(self, name):
        if self.document is None or name != self.document["name"]:
            raise AssertionError(f"unexpected document read: {name}")
        return dict(self.document)

    def list_documents(self):
        if self.document is None:
            return []
        return [{key: self.document[key] for key in ("name", "revision")}]


class DemoIntegrationReviewTests(unittest.TestCase):
    def test_api_transport_gets_current_question_only(self):
        captured = []

        class Provider:
            def stream(self, messages, cancelled):
                captured.extend(messages)
                yield "Current answer."

        turn = Turn("api-current-only", {"backend": "api"})
        messages = [
            {"role": "system", "content": "local system"},
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
            {"role": "user", "content": "REFERENCE_DATA with selected note"},
            {"role": "user", "content": "current question"},
        ]
        with patch.object(demo_server.Provider, "configured", return_value=Provider()):
            DemoEngine.generate_text(DemoEngine.__new__(DemoEngine), turn, messages)

        self.assertEqual([message["role"] for message in captured], ["system", "user"])
        self.assertEqual(captured[1]["content"], "current question")
        self.assertNotIn("previous question", json.dumps(captured))
        self.assertNotIn("previous answer", json.dumps(captured))
        self.assertNotIn("REFERENCE_DATA", json.dumps(captured))

    def test_local_turn_reads_latest_selected_document_at_stream_time(self):
        latest = {
            "name": "note.md",
            "content": "version two, written after the prior turn",
            "revision": "rev-two",
            "bytes": 41,
        }
        engine = engine_double(Documents(latest))
        turn = Turn("latest-note", {
            "backend": "local", "document": "note.md", "web": False,
            "sources": [], "model": "fixture", "mode": "conversational",
            "audition": False,
        })
        events = []
        delegated = []

        def capture_stream(_engine, captured_turn, text, wav, messages, send):
            delegated.append(dict(captured_turn.options["selected_document"]))

        with patch.object(demo_server, "decide", return_value={"kind": "conversation", "delivery": "spoken"}), \
             patch.object(Engine, "stream", new=capture_stream):
            engine.stream(turn, "what changed?", None, [], events.append)

        self.assertEqual(delegated[0]["content"], latest["content"])
        self.assertEqual(delegated[0]["revision"], "rev-two")
        context = next(event for event in events if event["type"] == "document_context")
        self.assertEqual(context["revision"], "rev-two")

    def test_local_api_route_is_only_a_proposal(self):
        engine = engine_double(Documents())
        turn = Turn("api-proposal", {
            "backend": "local", "document": None, "web": False,
            "sources": [], "model": "fixture", "mode": "conversational",
            "audition": False,
        })
        events = []
        delegated = []

        def capture_stream(_engine, captured_turn, text, wav, messages, send):
            delegated.append(captured_turn.options.get("fixed_reply"))

        plan = {"kind": "api_preview", "delivery": "spoken", "reason": "fixture"}
        with patch.object(demo_server, "decide", return_value=plan), \
             patch.object(demo_server, "api_proposal", return_value={"request_sent": False}), \
             patch.object(demo_server.Provider, "configured", side_effect=AssertionError("provider called")), \
             patch.object(Engine, "stream", new=capture_stream):
            engine.stream(turn, "use a stronger model", None, [], events.append)

        self.assertEqual(next(event for event in events if event["type"] == "api_proposal")["proposal"], {"request_sent": False})
        self.assertIn("no API request was sent", delegated[0])

    def test_cancelled_stalled_draft_does_not_hold_speech_lock_or_publish(self):
        engine = engine_double(Documents())
        turn = Turn("blocked-draft", {
            "backend": "local", "document": None, "web": False,
            "sources": [], "model": "fixture", "mode": "conversational",
            "audition": False,
        })
        entered = threading.Event()
        release = threading.Event()
        events = []

        class BlockingResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def __iter__(self):
                return self

            def __next__(self):
                entered.set()
                release.wait(2)
                return b'{"message":{"content":"late draft"},"done":true}\n'

        plan = {
            "kind": "document_draft", "delivery": "silent",
            "name": "proposal.md", "instruction": "write a proposal",
        }
        with patch.object(demo_server, "decide", return_value=plan), \
             patch.object(drafting.urllib.request, "urlopen", return_value=BlockingResponse()):
            worker = threading.Thread(
                target=engine.stream,
                args=(turn, "draft a proposal", None, [], events.append),
            )
            worker.start()
            self.assertTrue(entered.wait(1))
            turn.cancelled.set()
            time.sleep(0.05)
            self.assertTrue(worker.is_alive())
            self.assertTrue(engine.lock.acquire(blocking=False))
            engine.lock.release()
            release.set()
            worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertIsNone(engine.draft_state())
        self.assertNotIn("draft", [event["type"] for event in events])

    def test_state_returns_an_isolated_copy_of_the_shared_generated_draft(self):
        class StateDocuments:
            def list_documents(self):
                return []

        engine = engine_double(StateDocuments())
        engine.ready = threading.Event()
        engine.loading_error = None
        proposal = {
            "id": "draft", "name": "private.md", "content": "unsaved",
            "revision": None, "saved": False, "source": "local model",
        }
        self.assertTrue(engine.publish_draft(Turn("draft"), proposal))
        Handler = demo_handler(engine, 8776)

        def fetch_state():
            request = Handler.__new__(Handler)
            request.path = "/api/demo/state"
            request.allowed = lambda: True
            replies = []
            request.reply = lambda status, value, *_args: replies.append((status, value))
            Handler.do_GET(request)
            return replies[0]

        with patch.object(demo_server, "provider_status", return_value={"configured": False}):
            first = fetch_state()
            second = fetch_state()

        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]["draft"], proposal)
        self.assertEqual(second[1]["draft"], proposal)
        first[1]["draft"]["content"] = "caller mutation"
        self.assertEqual(engine.draft_state()["content"], "unsaved")

    def test_applying_old_draft_does_not_mark_newer_draft_saved(self):
        draft_a = {"id": "a", "name": "a.md", "content": "A", "revision": "ra", "saved": False}
        draft_b = {"id": "b", "name": "b.md", "content": "B", "revision": "rb", "saved": False}
        turn_a = Turn("a")
        turn_b = Turn("b")

        class InterleavingDocuments:
            def __init__(self, engine):
                self.engine = engine

            def replace(self, name, content, revision):
                # A different request finishes draft B after A's compare-and-swap
                # save but before A's handler acknowledgement.
                self.engine.publish_draft(turn_b, draft_b)
                return {"name": name, "content": content, "revision": "new", "bytes": len(content)}

        class Connection:
            def settimeout(self, _timeout):
                pass

        engine = engine_double(None)
        engine.documents = InterleavingDocuments(engine)
        self.assertTrue(engine.publish_draft(turn_a, draft_a))
        Handler = demo_handler(engine, 8776)
        request = Handler.__new__(Handler)
        request.path = "/api/demo/document"
        request.connection = Connection()
        request.headers = {
            "Content-Type": "application/json",
            "Content-Length": "0",
        }
        body = json.dumps({
            "operation": "replace", "name": "a.md", "content": "edited A",
            "revision": "ra", "draft_id": "a",
        }).encode()
        request.headers["Content-Length"] = str(len(body))
        request.rfile = io.BytesIO(body)
        request.allowed = lambda: True
        replies = []
        request.reply = lambda status, value, *_args: replies.append((status, value))

        Handler.do_POST(request)

        self.assertEqual(replies[0][0], 200)
        self.assertEqual(engine.draft_state()["id"], "b")
        self.assertFalse(engine.draft_state()["saved"])

    def test_cancelled_turn_cannot_replace_retained_draft(self):
        engine = engine_double(Documents())
        retained = {"id": "a", "name": "a.md", "content": "A", "saved": False}
        cancelled = Turn("b")
        cancelled.cancelled.set()

        self.assertTrue(engine.publish_draft(Turn("a"), retained))
        self.assertFalse(engine.publish_draft(
            cancelled,
            {"id": "b", "name": "b.md", "content": "B", "saved": False},
        ))
        self.assertEqual(engine.draft_state()["id"], "a")

    def test_bounds_aggregate_local_context_and_preserves_current_question(self):
        history = []
        for _ in range(5):
            history.append({"role": "user", "content": ("user words " * 200)[:1999]})
            history.append({
                "role": "assistant",
                "content": ("draft preview words " * 200)[:2799],
            })
        sources = [{
            "id": index,
            "title": "t" * 180,
            "url": "https://example.test/" + ("p" * 1700) + str(index),
            "excerpt": "e" * 1000,
        } for index in range(4)]
        messages = build_messages(
            "q" * 2000,
            history=history,
            web_sources=sources,
            selected_document={"name": "note.md", "content": "d" * 6000},
        )
        options = ollama_model_options("gpt-oss:20b")

        self.assertLessEqual(_context_units(messages), 4096 - 1600)
        self.assertEqual(messages[-1], {"role": "user", "content": "q" * 2000})
        self.assertEqual(options["options"]["num_ctx"], 4096)
        self.assertEqual(options["options"]["num_predict"], 1600)


if __name__ == "__main__":
    unittest.main()
