import io,json,sys,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from drafting import draft,DraftError

def response(*events):return io.BytesIO(b''.join(json.dumps(event).encode()+b'\n' for event in events))
class DraftTests(unittest.TestCase):
    def call(self,body,cancelled=None):
        with patch('urllib.request.urlopen',return_value=body) as request:
            result=draft('Write a short guide',model='gpt-oss:20b',ollama_url='http://localhost:11434',cancelled=cancelled or threading.Event(),context={'name':'example.md','content':'Quoted source text'})
        return result,request
    def test_draft_preserves_written_format(self):
        text,request=self.call(response({'message':{'content':'# Guide\n\n- One thing.'}},{'done':True,'done_reason':'stop'}))
        self.assertEqual(text,'# Guide\n\n- One thing.\n')
        payload=json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['messages'][-1]['content'],'Write a short guide')
        self.assertIn('Quoted reference document',payload['messages'][1]['content'])
    def test_cancelled_draft_makes_no_request(self):
        cancelled=threading.Event();cancelled.set()
        result,request=self.call(response(),cancelled)
        self.assertIsNone(result);request.assert_not_called()
    def test_unfinished_and_truncated_drafts_are_not_returned(self):
        for events in [({'message':{'content':'Partial'}},),({'message':{'content':'Partial'},'done':True,'done_reason':'length'},),({'done':True},)]:
            with self.subTest(events=events),self.assertRaises(DraftError):self.call(response(*events))
    def test_oversized_reply_is_rejected(self):
        with self.assertRaises(DraftError):self.call(response({'message':{'content':'x'*16001},'done':True}))
if __name__=='__main__':unittest.main()
