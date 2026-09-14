import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from demo_intents import decide,api_proposal

class IntentTests(unittest.TestCase):
    def test_action_and_information_are_distinct(self):
        for text in ['Open Firefox','Please mute','Could you open Firefox?']:
            self.assertEqual(decide(text)['delivery'],'silent')
        for text in ['How do I open Firefox?','Why would I close this?','Can you explain mute?']:
            self.assertEqual(decide(text)['delivery'],'spoken')
    def test_only_explicit_note_writes(self):
        self.assertEqual(decide('Create a note weekend: Buy milk!')['content'],'Buy milk!')
        self.assertEqual(decide('Append to weekend: Bring tea.')['kind'],'document_append')
        for text in ['How do I create a note?', 'Create a note', 'Create a note weekend']:
            self.assertNotIn(decide(text)['kind'],['document_create','document_append'])
        with self.assertRaises(ValueError):decide('Create a note ../secret: text')
    def test_api_never_sends_or_includes_documents(self):
        result=api_proposal('Use a stronger model to compare architecture tradeoffs',True)
        self.assertTrue(result['suggested'])
        for key in ['request_sent','api_calls_enabled','spend_incurred','document_included']:
            self.assertFalse(result[key])

if __name__=='__main__':unittest.main()
