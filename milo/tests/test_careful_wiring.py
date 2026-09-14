"""A hard question goes to the cloud model when one is configured, and falls back locally."""
import json
import pathlib
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server  # noqa: E402
import demo_server  # noqa: E402
from demo_server import DemoEngine  # noqa: E402
from providers import ProviderError  # noqa: E402
from router import Router  # noqa: E402
from server import Turn  # noqa: E402


class Chunk:
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return self
    def astype(self, _): return self
    def tobytes(self): return b'\0' * 16


class TTS:
    sample_rate = 24000
    def generate_audio_stream(self, voice, text): yield Chunk()


class FakeBook:
    def search(self, query, limit=5): return {'matches': [], 'elapsed_ms': 1}


class FakeProvider:
    def __init__(self, tokens=None, error=None):
        self.tokens, self.error, self.calls, self.last_model = tokens or [], error, [], None

    def public(self): return {'configured': True, 'model': 'fake/free'}

    def stream(self, messages, cancelled, max_tokens=500):
        self.calls.append(messages)
        if self.error: raise ProviderError(self.error)
        self.last_model = 'fake/free'
        yield from self.tokens


class FakeResponse:
    def __init__(self, text):
        self.rows = [json.dumps({'message': {'content': text}, 'done': True, 'done_reason': 'stop', 'eval_count': 9}).encode()]
    def __enter__(self): return self.rows
    def __exit__(self, *_args): return False


def demo_engine():
    engine = DemoEngine.__new__(DemoEngine)
    engine.tts = TTS(); engine.voices = {'peter_yearsley': object()}
    engine.lock = threading.Lock(); engine.turn_lock = threading.Lock(); engine.turns = {}
    engine.prefetch = {}; engine.action_receipts = []; engine.partial_lock = threading.Lock()
    engine.router = Router(probe=False)
    engine.books = {'workspace': FakeBook(), 'personal': FakeBook()}
    engine.documents = mock.Mock(); engine.documents.list_documents.return_value = []
    engine.lookup = mock.Mock(return_value=([], None))
    return engine


def options():
    return {**server.validate_options({}), 'voice': 'peter_yearsley', 'mode': 'conversational', 'backend': 'local'}


class CarefulWiringTests(unittest.TestCase):
    QUESTION = 'Think carefully: prove that the square root of two is irrational.'

    def run_turn(self, engine, provider, text=QUESTION, local_reply='Local answer here.'):
        turn = Turn('careful', options()); engine.register(turn); events = []
        with mock.patch.object(demo_server.Provider, 'configured', return_value=provider), \
                mock.patch.object(server, 'json_request', return_value=FakeResponse(local_reply)), \
                mock.patch.object(server.memory_notes, 'recall', return_value=''), \
                mock.patch.object(demo_server.reminders, 'handle', return_value=None), \
                mock.patch.dict('os.environ', {'MILO_CAREFUL': '1'}):
            engine.stream(turn, text, None, [], events.append)
        return turn, events

    def test_hard_question_is_answered_by_the_cloud_model(self):
        provider = FakeProvider(tokens=['Suppose it were rational. ', 'Then a contradiction follows.'])
        turn, events = self.run_turn(demo_engine(), provider)
        spoken = ' '.join(e['text'] for e in events if e['type'] == 'sentence')
        self.assertIn('contradiction', spoken)
        self.assertNotIn('Local answer', spoken)
        self.assertEqual(turn.metrics['careful'], 'explicit request')
        self.assertEqual(turn.metrics['provider_model'], 'fake/free')
        self.assertIn({'type': 'caption', 'text': 'Asking the cloud model'}, [{k: v for k, v in e.items() if k in ('type', 'text')} for e in events if e['type'] == 'caption'])
        sent = provider.calls[0][-1]['content']
        self.assertNotIn('Think carefully', sent)
        self.assertIn('square root of two', sent)
        self.assertIn('up to five short spoken sentences', provider.calls[0][0]['content'])

    def test_provider_failure_falls_back_to_the_local_answer(self):
        provider = FakeProvider(error='Provider request failed (HTTP 401).')
        turn, events = self.run_turn(demo_engine(), provider)
        spoken = ' '.join(e['text'] for e in events if e['type'] == 'sentence')
        self.assertIn('Local answer', spoken)
        self.assertNotIn('error', [e['type'] for e in events])
        self.assertIn('HTTP 401', turn.metrics['careful_fallback'])
        self.assertEqual(turn.options['backend'], 'local')

    def test_plain_question_stays_local_even_with_a_provider(self):
        provider = FakeProvider(tokens=['Cloud answer.'])
        turn, events = self.run_turn(demo_engine(), provider, text='What is the capital of Peru?')
        spoken = ' '.join(e['text'] for e in events if e['type'] == 'sentence')
        self.assertIn('Local answer', spoken)
        self.assertEqual(provider.calls, [])
        self.assertNotIn('careful', turn.metrics)

    def test_without_a_provider_the_hard_question_stays_local_and_is_counted(self):
        turn, events = self.run_turn(demo_engine(), None)
        spoken = ' '.join(e['text'] for e in events if e['type'] == 'sentence')
        self.assertIn('Local answer', spoken)
        self.assertEqual(turn.metrics['careful_skipped'], 'no provider')


if __name__ == '__main__':
    unittest.main()
