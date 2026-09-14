"""Unit checks for the latency pass: first-chunk clause cuts, the dated prompt, and stage marks."""
import datetime
import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from server import FIRST_CLAUSE_ARM, SILENCE_MS, SYSTEM, Turn, split_sentence, system_prompt

class SplitSentenceTests(unittest.TestCase):
    def test_default_mode_waits_for_terminal_punctuation(self):
        text='I think that is a good idea, and we should try it soon'
        self.assertEqual(split_sentence(text),(None,text))
        self.assertEqual(split_sentence(text+'. Then'),('I think that is a good idea, and we should try it soon.','Then'))
        self.assertEqual(split_sentence('Really?! Yes',final=False)[0],'Really?!')

    def test_default_mode_bounded_clause_and_final_flush(self):
        long=('word '*40).strip()
        sentence,rest=split_sentence(long)
        self.assertLess(len(sentence),170);self.assertTrue(rest.startswith('word'))
        self.assertEqual(split_sentence('tail without punctuation',final=True),('tail without punctuation',''))

    def test_first_chunk_cuts_at_comma_once_armed(self):
        text='I think that is a genuinely good idea, and we should try it'
        self.assertGreaterEqual(len(text),FIRST_CLAUSE_ARM)
        self.assertEqual(split_sentence(text,first=True),('I think that is a genuinely good idea,','and we should try it'))
        # Below the arming length the comma is ignored even in first mode.
        short='Well, I think that is fine'
        self.assertLess(len(short),FIRST_CLAUSE_ARM)
        self.assertEqual(split_sentence(short,first=True),(None,short))

    def test_first_chunk_skips_tiny_leading_clause_and_handles_and_colon_semicolon(self):
        text='Well, that depends on how much time you have and whether'
        self.assertEqual(split_sentence(text,first=True),('Well, that depends on how much time you have','and whether'))
        self.assertEqual(split_sentence('Here is the short version of the whole plan: build first',first=True),
                         ('Here is the short version of the whole plan:','build first'))
        self.assertEqual(split_sentence('The report takes fifteen minutes on its own; the email',first=True),
                         ('The report takes fifteen minutes on its own;','the email'))
        # Numbers are not clause boundaries.
        text='The budget was roughly 1,000 dollars for the year and'
        self.assertEqual(split_sentence(text,first=True),(None,text))

    def test_first_chunk_prefers_earlier_sentence_end(self):
        self.assertEqual(split_sentence('Yes, that works. Book the slot first, then send',first=True)[0],'Yes, that works.')

    def test_subsequent_sentences_keep_punctuation_rule(self):
        text='I think that is a genuinely good idea, and we should try it'
        self.assertEqual(split_sentence(text,first=False),(None,text))

class PromptAndMetricsTests(unittest.TestCase):
    def test_prompt_carries_local_date_and_freshness_line(self):
        when=datetime.datetime(2026,9,3,10,0).astimezone()
        prompt=system_prompt(when)
        self.assertTrue(prompt.startswith(SYSTEM))
        self.assertIn('Today is Thursday, 3 September 2026',prompt)
        self.assertIn('prefer the provided sources and say when you are unsure',prompt)
        self.assertIn('Today is ',system_prompt())

    def test_stage_marks_first_wins_unless_repeat(self):
        turn=Turn('t')
        turn.mark('first_token_ms');first=turn.metrics['first_token_ms']
        turn.metrics['first_token_ms']=-1;turn.mark('first_token_ms')
        self.assertEqual(turn.metrics['first_token_ms'],-1)
        turn.mark('last_audio_sent',repeat=True);turn.metrics['last_audio_sent']=-1;turn.mark('last_audio_sent',repeat=True)
        self.assertGreaterEqual(turn.metrics['last_audio_sent'],first)

    def test_silence_gate_default(self):
        self.assertEqual(SILENCE_MS,350)

if __name__=='__main__':unittest.main()
