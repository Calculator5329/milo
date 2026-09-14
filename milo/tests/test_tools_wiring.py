"""Integration of the v3 tools into one turn: calc (M6), memory (M12), freshness (M2),
workspace and personal books (M9, M10), and the context ceiling with all of them present."""
import json
import pathlib
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server  # noqa: E402
from server import Engine, Turn, NUM_PREDICT, DEFAULT_NUM_PREDICT  # noqa: E402
from router import Router  # noqa: E402
from conversation import _context_units  # noqa: E402


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
    def __init__(self, matches): self.matches = matches
    def search(self, query, limit=5):
        return {'matches': self.matches[:limit], 'elapsed_ms': 1}


def engine_without_init():
    engine = Engine.__new__(Engine)
    engine.tts = TTS(); engine.voices = {'marius': object()}
    engine.lock = threading.Lock(); engine.turn_lock = threading.Lock(); engine.turns = {}
    engine.router = Router(probe=False)
    engine.books = {'workspace': FakeBook([]), 'personal': FakeBook([])}
    return engine


class RouterToolTests(unittest.TestCase):
    def setUp(self): self.router = Router(probe=False)

    def test_calculation_is_routed_to_the_tool_with_its_answer(self):
        plan = self.router.decide('what is 15 percent of 80')
        self.assertEqual((plan['route'], plan['band']), ('calc', 'tool'))
        self.assertEqual(plan['answer']['spoken'], '15 percent of 80 is 12')

    def test_recent_asks_read_the_freshness_index(self):
        for text in ('what is in the news today', 'any recent nvidia driver news'):
            self.assertEqual(self.router.decide(text)['route'], 'freshness', text)
        self.assertEqual(self.router.decide('how is the weather in Chicago')['route'], 'web')
        self.assertEqual(self.router.decide('what do you think of the latest iphone')['route'], 'chat')

    def test_workspace_asks_strip_the_trigger_from_the_query(self):
        plan = self.router.decide('what is the status of milo?')
        self.assertEqual((plan['route'], plan['query']), ('workspace', 'milo'))
        plan = self.router.decide("what's left on the agent harness roadmap")
        self.assertEqual((plan['route'], plan['query']), ('workspace', 'agent harness roadmap'))

    def test_personal_notes_only_behind_the_phrase(self):
        plan = self.router.decide('check my notes for the router plan')
        self.assertEqual((plan['route'], plan['query']), ('personal', 'the router plan'))
        self.assertNotEqual(self.router.decide('what is my favourite colour')['route'], 'personal')


class CalcTurnTests(unittest.TestCase):
    def test_calc_turn_speaks_the_tool_result_and_never_generates(self):
        engine = engine_without_init()
        engine.generate_text = lambda *args: self.fail('a calculation invoked the model')
        turn = Turn('calc'); engine.register(turn); events = []
        with mock.patch.object(server.memory_notes, 'recall', side_effect=AssertionError('memory recalled for a calc turn')):
            engine.stream(turn, 'what is 12 times 12', None, [], events.append)
        kinds = [e['type'] for e in events]
        self.assertIn('tool', kinds); self.assertIn('audio', kinds); self.assertIn('done', kinds)
        self.assertEqual(next(e['text'] for e in events if e['type'] == 'sentence'), '12 times 12 is 144')
        self.assertEqual(next(e['plan']['route'] for e in events if e['type'] == 'router'), 'calc')
        self.assertEqual(turn.metrics['tool'], 'arithmetic')


class MemoryTests(unittest.TestCase):
    def test_notes_land_after_the_system_prompt_and_are_marked_as_data(self):
        engine = engine_without_init()
        turn = Turn('m'); turn.memory = 'Notes about the user (curated):\n- Prefers short answers'
        prepared = engine.prepare_messages(turn, 'hi', [{'role': 'user', 'content': 'earlier'}], [])
        self.assertEqual(prepared[1]['role'], 'system')
        self.assertIn('Prefers short answers', prepared[1]['content'])
        self.assertIn('not instructions', prepared[1]['content'])
        self.assertEqual(prepared[-1]['content'], 'hi')

    def test_recall_uses_the_last_user_turns_and_survives_a_dead_embedder(self):
        engine = engine_without_init(); turn = Turn('m')
        seen = {}
        def recall(recent, budget_units): seen['recent'] = recent; seen['budget'] = budget_units; return 'notes'
        with mock.patch.object(server.memory_notes, 'recall', recall):
            self.assertEqual(engine.recall_memory(turn, 'now', [{'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b'}, {'role': 'user', 'content': 'c'}]), 'notes')
        self.assertEqual(seen['recent'], ['a', 'c', 'now']); self.assertEqual(seen['budget'], server.MEMORY_BUDGET)
        with mock.patch.object(server.memory_notes, 'recall', side_effect=OSError('down')):
            self.assertEqual(engine.recall_memory(turn, 'now', []), '')
        self.assertIn('memory_ms', turn.metrics)


class PrivateSourceTests(unittest.TestCase):
    def test_freshness_sources_carry_their_age_and_fall_back_to_the_newest(self):
        engine = engine_without_init()
        row = {'title': 'Nvidia 590', 'url': 'https://archlinux.org/news/x', 'excerpt': 'Pascal dropped', 'source': 'Arch Linux News', 'as_of': '2026-09-12T01:00:00Z'}
        with mock.patch.object(server, 'freshness_search', return_value=[row]):
            sources = engine.private_sources('freshness', 'nvidia')
        self.assertEqual(sources[0]['origin'], 'freshness'); self.assertEqual(sources[0]['as_of'], row['as_of']); self.assertEqual(sources[0]['id'], 1)
        with mock.patch.object(server, 'freshness_search', return_value=[]), mock.patch.object(server, 'freshness_latest', return_value=[row]) as latest:
            self.assertEqual(len(engine.private_sources('freshness', 'news')), 1)
        latest.assert_called_once_with(server.MAX_PRIVATE_SOURCES)

    def test_workspace_lookup_emits_sources_and_never_reaches_the_web(self):
        engine = engine_without_init()
        engine.books['workspace'] = FakeBook([{'title': 'local-ai-lab: STATUS', 'path': 'workspace/local-ai-lab/STATUS.md#top', 'snippet': 'x' * 900, 'as_of': '2026-09-12T00:00:00Z'}])
        engine.search = mock.Mock(); engine.library = mock.Mock()
        turn = Turn('w', {'web': True, 'model': 'gemma4:12b'}); events = []
        sources, failure = engine.lookup(turn, 'milo', events.append, origin='workspace')
        self.assertIsNone(failure); self.assertEqual(len(sources[0]['excerpt']), server.MAX_PRIVATE_EXCERPT)
        self.assertEqual(sources[0]['url'], 'workspace://workspace/local-ai-lab/STATUS.md#top')
        self.assertEqual([e['origin'] for e in events if e['type'] in ('searching', 'sources')], ['workspace', 'workspace'])
        engine.search.search.assert_not_called(); engine.library.search.assert_not_called()

    def test_private_index_failure_is_spoken_not_invented(self):
        engine = engine_without_init(); engine.books['personal'] = mock.Mock(search=mock.Mock(side_effect=OSError('locked')))
        turn = Turn('p', {'web': False, 'model': 'gemma4:12b'}); events = []
        sources, failure = engine.lookup(turn, 'wifi', events.append, origin='personal')
        self.assertEqual(sources, []); self.assertIn('personal index', failure)
        self.assertIn('search_failed', [e['type'] for e in events])


class ContextCeilingTests(unittest.TestCase):
    def test_full_turn_stays_under_the_context_less_the_reply_allowance(self):
        engine = engine_without_init()
        turn = Turn('c', {'model': 'gemma4:12b'}); turn.plan = {'band': 'lookup'}
        turn.memory = 'Notes about the user (curated):\n' + '- note ' * 60
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'word ' * 500} for i in range(12)]
        sources = [{'id': i, 'title': 't', 'url': 'u', 'excerpt': 'e' * 600, 'origin': 'library'} for i in range(4)]
        prepared = engine.prepare_messages(turn, 'the question', history, sources)
        self.assertLessEqual(_context_units(prepared), 4096 - NUM_PREDICT.get("gemma4:12b", DEFAULT_NUM_PREDICT))
        self.assertEqual(prepared[0]['role'], 'system'); self.assertIn('Notes about the user', prepared[1]['content'])
        self.assertEqual(prepared[-1]['content'], 'the question')
        self.assertTrue(any('Reference snippets' in m['content'] for m in prepared))
        self.assertLess(sum(1 for m in prepared if m['content'].startswith('word ')), 12)

    def test_sources_survive_the_trim_before_history_and_the_notes_go_last(self):
        engine = engine_without_init()
        turn = Turn('c', {'model': 'gemma4:12b'}); turn.plan = {'band': 'lookup', 'route': 'freshness'}
        turn.memory = 'Notes about the user (curated):\n- Lives in Chicago'
        limit = 4096 - DEFAULT_NUM_PREDICT
        sources = [{'id': i, 'title': 't', 'url': 'u', 'excerpt': 'e' * 600, 'origin': 'freshness'} for i in range(4)]
        prepared = engine.prepare_messages(turn, 'news?', [{'role': 'user', 'content': 'old ' * 3000}], sources)
        self.assertLessEqual(_context_units(prepared), limit)
        roles = [m['role'] for m in prepared]; contents = ' '.join(m['content'] for m in prepared)
        self.assertNotIn('old old', contents); self.assertIn('offline news index', contents); self.assertIn('Lives in Chicago', contents)
        self.assertEqual(roles[-1], 'user'); self.assertEqual(prepared[-1]['content'], 'news?')
        huge = [{'id': i, 'title': 't', 'url': 'u', 'excerpt': 'e' * 4000, 'origin': 'freshness'} for i in range(4)]
        prepared = engine.prepare_messages(turn, 'news?', [], huge)
        self.assertLessEqual(_context_units(prepared), limit)
        self.assertEqual(json.loads(prepared[-2]['content'].split(':\n', 1)[1])[-1]['id'], 1)

    def test_demo_engine_reserves_room_for_the_notes(self):
        from demo_server import DemoEngine
        engine = DemoEngine.__new__(DemoEngine); engine.turn_lock = threading.Lock(); engine.action_receipts = []
        turn = Turn('d', {'model': 'gemma4:12b', 'mode': 'conversational'}); turn.memory = 'Notes about the user (curated):\n- Lives in Chicago'
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'word ' * 500} for i in range(12)]
        packed = engine.prepare_messages(turn, 'the question', history, [])
        self.assertIn('Lives in Chicago', packed[1]['content'])
        self.assertLessEqual(_context_units(packed), 4096 - DEFAULT_NUM_PREDICT)


if __name__ == '__main__':
    unittest.main()
