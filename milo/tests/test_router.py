import unittest

from router import Router, TitleProbe, _band, _key_terms, _topic, analyse


class FakeProbe:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def run(self, topic):
        self.calls.append(topic)
        return {'hits': list(self.hits), 'books': 3, 'terms': [topic], 'ms': 4}


class TopicTests(unittest.TestCase):
    def test_scaffolding_is_stripped(self):
        self.assertEqual(_topic('Hey Milo, can you tell me about the Battle of Hastings?'), 'Battle of Hastings')
        self.assertEqual(_topic('what is the capital of Mongolia'), 'capital of Mongolia')
        self.assertEqual(_topic('how do I track a remote branch in git'), 'track a remote branch in git')

    def test_key_terms_prefer_proper_nouns_and_try_singulars(self):
        self.assertEqual(_key_terms('capital of Mongolia')[0], 'Mongolia')
        self.assertIn('hamstring', _key_terms('hamstrings'))


class BandTests(unittest.TestCase):
    def band(self, text):
        return _band(analyse(text))[0]

    def test_factual_questions_look_up(self):
        for text in ('What is the capital of Mongolia?', 'Who was Genghis Khan?', 'Tell me about the Mona Lisa',
                     'How many moons does Jupiter have?', 'How do I track a remote branch in git?'):
            self.assertEqual(self.band(text), 'lookup', text)

    def test_talk_and_commands_stay_in_chat(self):
        for text in ('hey milo', 'thanks!', 'What do you think about my plan?', 'Remind me to stretch at 5',
                     'what is 12 times 8', 'Tell me a joke', 'should I learn rust or go'):
            self.assertEqual(self.band(text), 'chat', text)

    def test_uncertain_questions_get_a_probe(self):
        for text in ('Why is the sky blue?', 'How many calories are in a peanut butter sandwich?'):
            self.assertEqual(self.band(text), 'maybe', text)


class RouterTests(unittest.TestCase):
    def test_explicit_requests_win(self):
        router = Router(probe=FakeProbe([]))
        receipt = router.decide('look up mutex locks')
        self.assertEqual((receipt['route'], receipt['query'], receipt['band']), ('library', 'mutex locks', 'explicit'))
        self.assertEqual(router.decide('anything at all', explicit_web=True)['route'], 'web')

    def test_lookup_band_skips_the_probe(self):
        probe = FakeProbe(['Mongolia'])
        receipt = Router(probe=probe).decide('What is the capital of Mongolia?')
        self.assertEqual(receipt['route'], 'library')
        self.assertEqual(receipt['query'], 'capital of Mongolia')
        self.assertEqual(probe.calls, [])
        self.assertIsNone(receipt['probe'])

    def test_maybe_band_follows_the_probe(self):
        hit = Router(probe=FakeProbe(['Rayleigh scattering'])).decide('Why is the sky blue?')
        self.assertEqual(hit['route'], 'library')
        self.assertIn('Rayleigh scattering', hit['reason'])
        miss = Router(probe=FakeProbe([])).decide('Why is the sky blue?')
        self.assertEqual(miss['route'], 'chat')
        self.assertIn('no library title matched', miss['reason'])
        self.assertEqual(miss['probe']['hits'], [])

    def test_probe_can_be_switched_off(self):
        receipt = Router(probe=False).decide('Why is the sky blue?')
        self.assertEqual((receipt['route'], receipt['probe']), ('chat', None))

    def test_chat_receipts_carry_a_reason_and_timing(self):
        receipt = Router(probe=False).decide('thanks milo')
        self.assertEqual(receipt['route'], 'chat')
        self.assertTrue(receipt['reason'])
        self.assertIsInstance(receipt['ms'], int)

    def test_phrases_that_name_the_web_route_directly_to_it(self):
        cases = {
            'search the web for the best pizza in Rochester': 'the best pizza in Rochester',
            'google systemd timers': 'systemd timers',
            'search google for local voice assistants': 'local voice assistants',
            'search online for Rochester pizza': 'Rochester pizza',
            'look online for Pocket TTS': 'Pocket TTS',
            'check online for the train status': 'the train status',
            'find online local weather radar': 'local weather radar',
            'search the internet for Python 3.15': 'Python 3.15',
            'what does the internet say about Gemma 4': 'Gemma 4',
            'web search systemd timers': 'systemd timers',
        }
        router = Router(probe=False)
        for text, query in cases.items():
            with self.subTest(text=text):
                receipt = router.decide(text)
                self.assertEqual(
                    (receipt['route'], receipt['band'], receipt['query'], receipt['reason']),
                    ('web', 'explicit', query, 'asked for the web'),
                )

    def test_library_phrases_and_generic_lookups_stay_library_first(self):
        router = Router(probe=False)
        for text in (
            'search your library for the Roman Republic',
            'look in the library for the Roman Republic',
            'look up the Roman Republic',
            'search for the Roman Republic',
        ):
            with self.subTest(text=text):
                self.assertEqual(router.decide(text)['route'], 'library')

    def test_live_facts_route_to_web_but_news_stays_in_freshness(self):
        router = Router(probe=False)
        live = {
            'what is Meta stock at right now': 'Meta stock',
            'what is the weather in Rochester today': 'weather in Rochester',
            'what is the S&P 500 index level': 'S&P 500 index level',
            'what is the exchange rate for dollars to euros': 'exchange rate for dollars to euros',
            'who won the Knicks game': 'won the Knicks game',
            'is Wegmans open now': 'Wegmans open now',
            'what is the current time in Ulaanbaatar': 'current time in Ulaanbaatar',
        }
        for text, query in live.items():
            with self.subTest(text=text):
                receipt = router.decide(text)
                self.assertEqual(
                    (receipt['route'], receipt['band'], receipt['query'], receipt['reason']),
                    ('web', 'live', query, 'live fact'),
                )
        news = router.decide('what happened in the news this week')
        self.assertEqual(news['route'], 'freshness')
        self.assertNotEqual(news['band'], 'live')


class TitleMatchTests(unittest.TestCase):
    def test_exact_and_namespaced_titles_match_but_phrases_do_not(self):
        from router import _title_matches
        self.assertTrue(_title_matches('Sky', 'sky'))
        self.assertTrue(_title_matches('Pinyin/Sky', 'sky'))
        self.assertTrue(_title_matches('Wikijunior:Monad', 'monad'))
        self.assertTrue(_title_matches('Monad (functional programming)', 'monad'))
        self.assertFalse(_title_matches('Wikijunior:Green and Sky Blue Animal Alphabet', 'sky blue'))


class ProbeTests(unittest.TestCase):
    def test_probe_reads_catalog_and_title_hits_with_a_budget(self):
        calls = []

        def fetch(path, timeout):
            calls.append((path, timeout))
            if path.startswith('/catalog'):
                return '<a href="/content/wikipedia_en_top_maxi_2026-06/A"></a><a href="/content/devdocs_en_git_2026-07/x"></a>'
            return '[{"value": "Sky", "kind": "path"}, {"value": "Sky blue animal alphabet", "kind": "path"}, {"value": "sky", "kind": "pattern"}]'

        probe = TitleProbe(fetch=fetch, budget=0.5)
        result = probe.run('sky')
        self.assertEqual(result['hits'], ['Sky'])
        self.assertEqual(result['books'], 1)
        self.assertTrue(all(t <= 1.0 for _, t in calls))

    def test_a_book_that_misses_the_budget_is_skipped_for_a_while(self):
        calls = []

        def fetch(path, timeout):
            calls.append(path)
            if path.startswith('/catalog'):
                return '<a href="/content/wikipedia_en_top_maxi_2026-06/A"></a><a href="/content/wiktionary_en_all_nopic_2026-08/A"></a>'
            if 'wiktionary' in path:
                raise TimeoutError('timed out')
            return '[]'

        probe = TitleProbe(fetch=fetch, budget=0.5)
        probe.run('sky')
        self.assertTrue(any('wiktionary' in c for c in calls))
        calls.clear()
        probe.run('sky')
        self.assertFalse(any('wiktionary' in c for c in calls))
        self.assertTrue(any('wikipedia' in c for c in calls))

    def test_probe_survives_a_dead_server(self):
        def fetch(path, timeout):
            raise OSError('down')
        result = TitleProbe(fetch=fetch, budget=0.05).run('sky')
        self.assertEqual((result['hits'], result['books']), ([], 0))



class EvalRoundTests(unittest.TestCase):
    """Fixes from the 2026-09-13 question-bank review: web verbs anywhere in the sentence, a live
    lexicon for markets and crypto, worded arithmetic to the calculator, and pronoun follow-ups."""

    def setUp(self):
        self.router = Router(probe=False)

    def test_web_verbs_route_to_the_web_with_a_clean_query(self):
        cases = {
            'Can you look up on the internet who owns Anthropic?': 'who owns Anthropic',
            'Web search: cheapest flights from Rochester to Denver.': 'cheapest flights from Rochester to Denver',
            'Look it up online: how long do lithium batteries last?': 'how long do lithium batteries last',
            'Find on the web the population of Rochester New York.': 'the population of Rochester New York',
        }
        for text, query in cases.items():
            plan = self.router.decide(text)
            self.assertEqual((plan['route'], plan['band'], plan['query']), ('web', 'explicit', query), text)
        # A bare 'search for' still reads the library first; the web fallback covers a miss.
        plan = self.router.decide('Search for reviews of the Framework laptop.')
        self.assertEqual((plan['route'], plan['query']), ('library', 'reviews of the Framework laptop'))

    def test_markets_and_crypto_are_live_facts(self):
        for text in ("How is the S&P 500 doing today?", "What's Bitcoin trading at?", 'Is the Nasdaq up today?'):
            plan = self.router.decide(text)
            self.assertEqual((plan['route'], plan['band']), ('web', 'live'), text)
        self.assertEqual(self.router.decide("What's Bitcoin trading at?")['query'], 'Bitcoin price')

    def test_worded_arithmetic_and_conversions_reach_the_calculator(self):
        for text in ("What's the square root of 144?", "What's 20 percent off of 150 dollars?",
                     'How many kilometres is 30 miles?', 'How many ounces are in a pound?'):
            plan = self.router.decide(text)
            self.assertEqual(plan['route'], 'calc', text)

    def test_pronoun_follow_up_carries_the_previous_subject(self):
        history = [{'role': 'user', 'content': 'Who wrote The Old Man and the Sea?'},
                   {'role': 'assistant', 'content': 'Ernest Hemingway wrote it. I found that on the web.'}]
        plan = self.router.decide('When was he born?', False, history)
        self.assertEqual(plan['route'], 'library')
        self.assertEqual(plan['query'], 'Ernest Hemingway born')
        self.assertEqual(plan['carried'], 'Ernest Hemingway')
        history = [{'role': 'user', 'content': 'Who painted the Mona Lisa?'},
                   {'role': 'assistant', 'content': 'Leonardo da Vinci painted the Mona Lisa.'}]
        self.assertEqual(self.router.decide('Where is it now?', False, history)['carried'], 'Leonardo da Vinci Mona Lisa')
        self.assertNotIn('carried', self.router.decide('When was he born?'))
        self.assertNotIn('carried', self.router.decide('Who wrote Dune?', False, history))

    def test_time_and_date_come_from_the_real_clock(self):
        import datetime, zoneinfo
        now = datetime.datetime(2026, 9, 13, 21, 52, tzinfo=zoneinfo.ZoneInfo('America/New_York'))
        from router import clock_answer
        self.assertEqual(clock_answer('What time is it?', now)['spoken'], "It's 9:52 PM on Sunday, September 13.")
        self.assertEqual(clock_answer('What day is it today?', now)['spoken'], 'Today is Sunday, September 13, 2026.')
        self.assertEqual(clock_answer("What's the current time in Tokyo?", now)['spoken'], "It's 10:52 AM on Monday, September 14 in Tokyo.")
        self.assertIsNone(clock_answer('What time is the meeting?', now))
        self.assertIsNone(clock_answer('What is the time signature of a waltz?', now))
        self.assertIsNone(clock_answer("What's the current time in Narnia?", now))
        plan = self.router.decide('What time is it?')
        self.assertEqual((plan['route'], plan['band'], plan['answer']['kind']), ('calc', 'tool', 'clock'))
        self.assertIn('expression', plan['answer'])

    def test_queries_never_end_in_punctuation(self):
        for text in ('Search the web for Pocket TTS.', 'What is the capital of Mongolia?', 'Who won the Bills game?'):
            self.assertNotRegex(self.router.decide(text)['query'] or '', r'[?.!]$', text)


if __name__ == '__main__':
    unittest.main()


class RecapTests(unittest.TestCase):
    def setUp(self):
        import json, tempfile, datetime
        self.tmp = tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8')
        now = datetime.datetime(2026, 9, 12, 18, 0, tzinfo=datetime.timezone.utc)
        rows = [
            {'at': (now - datetime.timedelta(hours=3)).isoformat(), 'question': 'What is the capital of Mongolia?', 'answer': 'Ulaanbaatar.'},
            {'at': (now - datetime.timedelta(hours=2)).isoformat(), 'question': 'what is the capital of mongolia', 'answer': 'Still Ulaanbaatar.'},
            {'at': (now - datetime.timedelta(minutes=50)).isoformat(), 'question': 'Who would win, Jordan or LeBron?', 'answer': 'Jordan.'},
            {'at': (now - datetime.timedelta(minutes=20)).isoformat(), 'question': 'cancelled one', 'cancelled': True},
            {'at': (now - datetime.timedelta(minutes=5)).isoformat(), 'question': 'What have we talked about before?', 'answer': 'recap'},
            {'at': (now - datetime.timedelta(minutes=1)).isoformat(), 'app': 'eval', 'question': 'Who painted the Mona Lisa?', 'answer': 'Leonardo.'},
        ]
        for row in rows:
            self.tmp.write(json.dumps(row) + '\n')
        self.tmp.close()
        self.now = now

    def tearDown(self):
        import os
        os.unlink(self.tmp.name)

    def test_recap_lists_distinct_recent_questions_newest_first(self):
        from router import recap_answer
        answer = recap_answer(path=self.tmp.name, now=self.now)
        self.assertEqual(answer['kind'], 'recap')
        self.assertEqual(answer['count'], 2)
        self.assertEqual(answer['spoken'],
                         'Most recently you asked: Who would win, Jordan or LeBron. Before that: what is the capital of mongolia. That goes back about 2 hours.')

    def test_recap_with_no_ledger_is_honest(self):
        from router import recap_answer
        answer = recap_answer(path='/nonexistent/turns.jsonl')
        self.assertEqual(answer['count'], 0)
        self.assertIn('earlier conversations', answer['spoken'])

    def test_router_routes_recap_questions_as_a_tool(self):
        plan = Router(probe=False).decide('what have we talked about before?')
        self.assertEqual((plan['route'], plan['band']), ('recap', 'tool'))
        self.assertIn('spoken', plan['answer'])
        self.assertEqual(Router(probe=False).decide('what is the capital of Mongolia')['route'], 'library')


class EvalRoundTwoTests(unittest.TestCase):
    """Second grader pass on the 214-question bank (2026-09-13): opinion and self questions
    were spending a lookup, recap and 'lately' cues missed, and the workspace query collapsed."""

    def setUp(self):
        self.router = Router(probe=False)

    def test_comparative_questions_are_opinions_not_lookups(self):
        for text in ("Who's better, Michael Jordan or LeBron James?", "Is it better to rent or buy a house right now?",
                     "What's the most overrated programming language?", "Is Rust better than C++?"):
            plan = self.router.decide(text)
            self.assertEqual((plan['route'], plan['reason']), ('chat', 'asks for an opinion or advice'), text)

    def test_questions_about_milo_itself_stay_local(self):
        for text in ("What model are you running on?", "Can you run commands on my computer?", "Do you remember our conversations?",
                     "Is my data private?", "Are you always listening?"):
            plan = self.router.decide(text)
            self.assertEqual((plan['route'], plan['reason']), ('chat', 'asks about Milo itself'), text)

    def test_recap_and_recency_cues(self):
        self.assertEqual(self.router.decide('What did I ask you about recently?')['route'], 'recap')
        plan = self.router.decide('Anything new on the Linux desktop lately?')
        self.assertEqual(plan['route'], 'freshness')
        self.assertNotIn('lately', plan['query'])

    def test_workspace_query_never_collapses_to_a_stop_word(self):
        self.assertEqual(self.router.decide('What is in my workspace?')['query'], 'projects roadmap status')
        self.assertEqual(self.router.decide('What is the status of milo?')['query'], 'milo')

    def test_spelling_is_a_tool_answer(self):
        plan = self.router.decide('How do you spell accommodate?')
        self.assertEqual(plan['route'], 'calc')
        self.assertEqual(plan['answer']['spoken'], 'Accommodate is spelled A, C, C, O, M, M, O, D, A, T, E.')
