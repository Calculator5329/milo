import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from careful import cleaned,wants_careful


class CarefulTests(unittest.TestCase):
    def test_explicit_requests_qualify(self):
        for phrase in [
            'think carefully','think hard','ask the big model','use the cloud model',
            'use openrouter','ask openrouter','give me a thorough answer','be thorough',
            'careful answer',
        ]:
            with self.subTest(phrase=phrase):
                self.assertEqual(wants_careful(f'{phrase}: explain this'),'explicit request')

    def test_hard_shapes_qualify(self):
        cases={
            'Can you prove that there are infinitely many primes?':'proof or structured reasoning',
            'Give me a derivation of the quadratic formula.':'proof or structured reasoning',
            'Reason through this step by step.':'proof or structured reasoning',
            'Compare SQLite and Postgres, including tradeoffs.':'comparison with tradeoffs',
            'Compare tabs and spaces with pros and cons.':'comparison with tradeoffs',
            'Write a Python function that merges two sorted iterators.':'programming task',
            'Create a class that tracks a bounded queue.':'programming task',
            'Explain the event loop in depth.':'depth requested',
            'Describe this protocol in detail.':'depth requested',
            ' '.join(['word']*61):'long question',
        }
        for question,reason in cases.items():
            with self.subTest(question=question):self.assertEqual(wants_careful(question),reason)

    def test_everyday_questions_do_not_qualify(self):
        for question in [
            'What is SQLite?','What is 12 times 12?','Open the weather link.',
            'Remind me to call Sam tomorrow.','Hello, how are you?',
            'What command lists files?','Write a one-line command to list files.',
            'Compare cats and dogs.','What are the tradeoffs of SQLite?',
            'What does derive mean?','What is a mathematical proof?',
        ]:
            with self.subTest(question=question):self.assertIsNone(wants_careful(question))

    def test_cleaned_removes_explicit_trigger_and_repairs_spacing(self):
        self.assertEqual(cleaned('Think carefully: explain the result.'),'explain the result.')
        self.assertEqual(cleaned('Use OpenRouter, and compare these options.'),'and compare these options.')
        self.assertEqual(cleaned('What is SQLite?'),'What is SQLite?')
        self.assertEqual(cleaned('  Be thorough.  Explain why.  '),'Explain why.')
        self.assertEqual(cleaned('Explain this, think hard.'),'Explain this.')
        self.assertEqual(cleaned('Explain this, think hard, please.'),'Explain this, please.')

    def test_cli_prints_json(self):
        script=Path(__file__).resolve().parents[1]/'careful.py'
        result=subprocess.run([sys.executable,str(script),'Think hard: solve this.'],check=True,capture_output=True,text=True)
        self.assertEqual(json.loads(result.stdout),{'reason':'explicit request','cleaned':'solve this.'})


if __name__=='__main__':unittest.main()
