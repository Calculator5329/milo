import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model_comparison import run
from server import Turn

class Engine:
    def __init__(self):self.lock=threading.Lock();self.forgotten=[]
    def forget(self,turn):self.forgotten.append(turn.id)

class ModelComparisonTests(unittest.TestCase):
    def test_actual_stream_answer_and_minimal_context(self):
        engine=Engine();turn=Turn('compare',{})
        reply=io.BytesIO(b'{"message":{"content":"Three trays."}}\n{"done":true,"eval_count":4}\n')
        with patch('model_comparison.urllib.request.urlopen',return_value=reply) as transport:
            result=run(engine,turn,'18 seedlings, six per tray. How many trays?', 'qwen2.5:3b','precise',{'qwen2.5:3b':'Quick'},'http://127.0.0.1:11434')
        payload=json.loads(transport.call_args.args[0].data)
        self.assertEqual([m['role'] for m in payload['messages']],['system','user'])
        self.assertEqual(result['answer'],'Three trays.')
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['generated_tokens'],4)
        self.assertEqual(engine.forgotten,['compare'])
        self.assertFalse(engine.lock.locked())
    def test_partial_stream_is_not_reported_as_completed(self):
        engine=Engine();turn=Turn('partial',{})
        with patch('model_comparison.urllib.request.urlopen',return_value=io.BytesIO(b'{"message":{"content":"Partial"}}\n')):
            result=run(engine,turn,'A question','qwen2.5:3b','precise',{'qwen2.5:3b':'Quick'},'http://127.0.0.1:11434')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['answer'],'Partial')
    def test_cancelled_run_does_not_contact_a_model(self):
        engine=Engine();turn=Turn('cancelled',{});turn.cancelled.set()
        with patch('model_comparison.urllib.request.urlopen') as transport:
            result=run(engine,turn,'A question','qwen2.5:3b','precise',{'qwen2.5:3b':'Quick'},'http://127.0.0.1:11434')
        transport.assert_not_called();self.assertEqual(result['status'],'cancelled')
        self.assertFalse(engine.lock.locked())
