"""Tool routes speak without the model, and the unfiltered model drops disclaimers."""
import pathlib
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server  # noqa: E402
from server import MODELS, Turn, system_prompt, unfiltered_model  # noqa: E402
from conversation import UNFILTERED_NOTE, build_messages, build_system_prompt  # noqa: E402
try:
    from tests.test_tools_wiring import engine_without_init
except ModuleNotFoundError:
    from test_tools_wiring import engine_without_init


class ToolRouteTests(unittest.TestCase):
    def test_recap_is_spoken_from_the_ledger_without_the_model(self):
        engine = engine_without_init()
        engine.generate_text = lambda *args: self.fail('a recap invoked the model')
        turn = Turn('recap'); engine.register(turn); events = []
        answer = {'kind': 'recap', 'expression': 'last 2 questions', 'spoken': 'Most recently you asked: about Mongolia. Before that: about Jordan.', 'count': 2}
        with mock.patch('router.recap_answer', return_value=answer):
            engine.stream(turn, 'what have we talked about before?', None, [], events.append)
        kinds = [e['type'] for e in events]
        self.assertIn('tool', kinds); self.assertIn('done', kinds)
        tool = next(e for e in events if e['type'] == 'tool')
        self.assertEqual(tool['tool'], 'recap')
        self.assertIn('about Mongolia', ' '.join(e['text'] for e in events if e['type'] == 'sentence'))
        self.assertEqual(turn.metrics['tool'], 'recap')


class UnfilteredTests(unittest.TestCase):
    def test_the_unfiltered_model_is_offered_and_recognised(self):
        self.assertIn('gemma3-abliterated:12b-q4', MODELS)
        self.assertTrue(unfiltered_model('gemma3-abliterated:12b-q4'))
        self.assertFalse(unfiltered_model('gemma4:12b'))

    def test_prompts_carry_the_unfiltered_note_only_for_that_model(self):
        self.assertIn(UNFILTERED_NOTE, system_prompt(unfiltered=True))
        self.assertNotIn(UNFILTERED_NOTE, system_prompt())
        self.assertIn(UNFILTERED_NOTE, build_system_prompt(unfiltered=True))
        self.assertNotIn(UNFILTERED_NOTE, build_system_prompt())
        self.assertIn(UNFILTERED_NOTE, build_messages('hi', unfiltered=True)[0]['content'])
        self.assertNotIn(UNFILTERED_NOTE, build_messages('hi')[0]['content'])

    def test_engine_prompt_follows_the_selected_model(self):
        engine = engine_without_init()
        plain = engine.prepare_messages(Turn('a', {'model': 'gemma4:12b'}), 'hi', [], [])
        spicy = engine.prepare_messages(Turn('b', {'model': 'gemma3-abliterated:12b-q4'}), 'hi', [], [])
        self.assertNotIn(UNFILTERED_NOTE, plain[0]['content'])
        self.assertIn(UNFILTERED_NOTE, spicy[0]['content'])


class PersonaTests(unittest.TestCase):
    def test_experiment_prompt_has_a_point_of_view_and_knows_its_tools(self):
        for phrase in ('Have a point of view', 'never say you have no', 'reminders', 'offline library', 'hotkey assistant'):
            self.assertIn(phrase, server.SYSTEM)
        self.assertNotIn('no desktop actions', server.SYSTEM)
        self.assertIn('for live facts, and when the library has nothing', server.SYSTEM)
        self.assertNotIn('web only when asked', server.SYSTEM)

    def test_reference_block_tells_the_model_to_ignore_snippets_that_miss(self):
        from conversation import REFERENCE_PREFIX
        self.assertIn('ignore it and answer from memory without mentioning it', REFERENCE_PREFIX)
        self.assertIn('exactly as it would be typed', server.THOUGHT_RULE)

    def test_demo_prompt_falls_back_to_own_knowledge_when_snippets_miss(self):
        prompt = build_system_prompt(owner_name="Sam")
        for phrase in ('When it does not answer the question, answer from your own knowledge',
                       'never mention the library, what it lacks, or that you searched'):
            self.assertIn(phrase, prompt)

    def test_demo_engine_carries_the_web_reason_label_into_model_context(self):
        from demo_server import DemoEngine
        engine = DemoEngine.__new__(DemoEngine)
        engine.turn_lock = threading.Lock(); engine.action_receipts = []
        plan = {'route': 'web', 'query': 'Meta stock', 'band': 'live', 'reason': 'live fact'}
        turn = Turn('live', {'model': 'gemma4:12b', 'mode': 'conversational'})
        turn.plan = plan; turn.metrics['web_reason'] = 'live'
        sources = [{'id': 1, 'title': 'Meta', 'url': 'https://example.org/meta',
                    'excerpt': 'Meta trades at a current price.', 'origin': 'web'}]
        packed = engine.prepare_messages(turn, 'what is Meta stock at right now', [], sources)
        self.assertIn('web search made moments ago', '\n'.join(message['content'] for message in packed))


class PrefetchRoutingTests(unittest.TestCase):
    def test_demo_prefetch_uses_and_reuses_the_final_lookup_plan(self):
        from demo_server import DemoEngine
        from router import Router
        engine = DemoEngine.__new__(DemoEngine)
        engine.prefetch = {}; engine.turn_lock = threading.Lock(); engine.router = Router(probe=False)
        engine.lookup = mock.Mock(return_value=([], None))
        text = 'what is the weather in Rochester today'
        entry = engine.start_prefetch('recording', text)
        self.assertTrue(entry['done'].wait(1))
        prefetched_plan = engine.lookup.call_args.kwargs['plan']
        final_plan = engine.router.decide(text, False)
        self.assertEqual(prefetched_plan, final_plan)
        self.assertEqual((final_plan['route'], final_plan['band']), ('web', 'live'))
        final_turn = Turn('recording')
        reused = engine.consume_prefetch(final_turn, text, final_plan)
        self.assertIs(reused, entry)
        self.assertEqual(final_turn.metrics['prefetch'], 'hit')



class ModelPreferenceTests(unittest.TestCase):
    def test_model_is_a_shared_preference_and_old_records_gain_the_default(self):
        import json, tempfile
        from preferences import DEFAULTS, Preferences, PreferencesError
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Preferences(tmp)
            self.assertEqual(prefs.get()['model'], 'gemma4:12b')
            self.assertEqual(prefs.update({'model': 'gemma3-abliterated:12b-q4'})['model'], 'gemma3-abliterated:12b-q4')
            with self.assertRaises(PreferencesError):
                prefs.update({'model': 'not-a-model'})
        with tempfile.TemporaryDirectory() as tmp:
            legacy = {k: v for k, v in DEFAULTS.items() if k != 'model'}
            (pathlib.Path(tmp) / 'preferences.md').write_text(json.dumps(legacy), encoding='utf-8')
            self.assertEqual(Preferences(tmp).get()['model'], 'gemma4:12b')


if __name__ == '__main__':
    unittest.main()
