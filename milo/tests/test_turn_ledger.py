import json, tempfile, threading, unittest
from pathlib import Path
from turn_ledger import TurnRecorder, flags


class FakeTurn:
    def __init__(self):
        self.id = 'abc'
        self.options = {'model': 'gemma4:12b', 'voice': 'peter_yearsley', 'web': False}
        self.cancelled = threading.Event()


class TurnLedgerTests(unittest.TestCase):
    def test_records_question_sources_answer_and_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'turns.jsonl'
            turn = FakeTurn()
            recorder = TurnRecorder(turn, 'look up mutex', 'corner', path=path)
            seen = []
            send = recorder.wrap(seen.append)
            send({'type': 'searching', 'query': 'mutex', 'origin': 'library'})
            send({'type': 'sources', 'sources': [{'title': 'Mutex', 'book': 'unix', 'origin': 'library'}]})
            send({'type': 'sentence', 'text': 'A mutex is a lock. '})
            send({'type': 'sentence', 'text': 'Only one holder.'})
            send({'type': 'done', 'metrics': {'first_token_ms': 80, 'first_audio_chunk_sent': 400}})
            recorder.close()
            self.assertEqual(len(seen), 5)
            row = json.loads(path.read_text().splitlines()[0])
            self.assertEqual(row['question'], 'look up mutex')
            self.assertEqual(row['answer'], 'A mutex is a lock. Only one holder.')
            self.assertEqual(row['sources'][0]['title'], 'Mutex')
            self.assertEqual(row['options'], {'model': 'gemma4:12b', 'voice': 'peter_yearsley'})
            self.assertFalse(row['cancelled'])
            self.assertEqual(flags(row), [])

    def test_flags_denied_access_and_empty_lookup(self):
        row = {'answer': "I cannot access external databases.", 'lookup': {'query': 'x', 'origin': 'library'}, 'sources': [],
               'metrics': {'generation_finish': 'length', 'first_audio_ms': 2000}, 'events': []}
        marks = flags(row)
        self.assertIn('denied access', marks)
        self.assertIn('lookup found nothing', marks)
        self.assertIn('cut off by token budget', marks)
        self.assertIn('slow first audio 2000 ms', marks)

    def test_disabled_path_writes_nothing(self):
        recorder = TurnRecorder(FakeTurn(), 'hi', 'milo', path=None)
        recorder.wrap(lambda e: None)({'type': 'sentence', 'text': 'x'})
        recorder.close()
        self.assertEqual(recorder.row['answer'], 'x')


if __name__ == '__main__':
    unittest.main()
