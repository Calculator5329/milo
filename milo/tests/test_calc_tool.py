import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calc_tool


class CalculatorTests(unittest.TestCase):
    def test_arithmetic_spoken_forms_and_number_words(self):
        cases = {
            "what is 17 times 23": 391,
            "what's 144 divided by 12": 12,
            "square root of 144": 12,
            "2 to the power of 10": 1024,
            "45 plus 17 minus 3": 59,
            "seven times eight": 56,
            "3 * (4 + 5)": 27,
            "-3 * 4": -12,
            "2 ** 6": 64,
        }
        for utterance, expected in cases.items():
            with self.subTest(utterance=utterance):
                result = calc_tool.answer(utterance)
                self.assertIsNotNone(result)
                self.assertEqual(result["kind"], "arithmetic")
                self.assertEqual(result["result"], expected)
                self.assertTrue(result["spoken"])
                self.assertNotRegex(result["spoken"], r"[*%/()+\-=]")

    def test_percent_is_its_own_kind(self):
        for utterance, expected in (("12 percent of 80", 9.6), ("what is 15% of 200", 30)):
            with self.subTest(utterance=utterance):
                result = calc_tool.answer(utterance)
                self.assertEqual(result["kind"], "percent")
                self.assertEqual(result["result"], expected)
                self.assertNotIn("%", result["spoken"])

    def test_units_cover_dimensions_and_compound_length(self):
        cases = (
            ("convert 3 miles to km", "unit", 4.828, "kilometers"),
            ("how many cups in a liter", "unit", 4.227, "cups"),
            ("what is 72 fahrenheit in celsius", "unit", 22.22, "degrees Celsius"),
            ("5 feet 10 inches in cm", "unit", 177.8, "centimeters"),
            ("1 pound in kg", "unit", 0.4536, "kilograms"),
            ("100 kilometers per hour in mph", "unit", 62.14, "miles per hour"),
            ("2 GB in MiB", "unit", 1907, "mebibytes"),
            ("2 hours in seconds", "unit", 7200, "seconds"),
            ("2 acres in square meters", "unit", 8094, "square meters"),
        )
        for utterance, kind, expected, unit_name in cases:
            with self.subTest(utterance=utterance):
                result = calc_tool.answer(utterance)
                self.assertEqual(result["kind"], kind)
                self.assertTrue(math.isclose(result["result"], expected, rel_tol=0, abs_tol=0.01))
                self.assertIn(unit_name, result["spoken"])

    def test_currency_uses_static_rates_and_local_override_without_network(self):
        with tempfile.TemporaryDirectory() as home:
            rates_dir = Path(home) / ".local" / "state" / "milo"
            rates_dir.mkdir(parents=True)
            (rates_dir / "rates.json").write_text(json.dumps({
                "as_of": "2026-09-01",
                "base": "USD",
                "rates": {"USD": 1, "EUR": 2},
            }))
            with patch.dict("os.environ", {"HOME": home}, clear=False):
                result = calc_tool.answer("convert 10 dollars to euros")
        self.assertEqual(result["kind"], "currency")
        self.assertEqual(result["result"], 20)
        self.assertIn("as of 2026-09-01", result["spoken"])

    def test_rejects_non_calculation_questions(self):
        for utterance in ("what is a mile", "how many people live in Texas", "what time is it"):
            with self.subTest(utterance=utterance):
                self.assertIsNone(calc_tool.answer(utterance))

    def test_ast_safety_rejects_names_attributes_and_huge_exponents(self):
        for utterance in (
            "__import__('os').system('echo unsafe')",
            "thing.value + 2",
            "2 to the power of 65",
            "2 ** 65",
        ):
            with self.subTest(utterance=utterance):
                self.assertIsNone(calc_tool.answer(utterance))



class SpokenQuestionTests(unittest.TestCase):
    """Spoken scaffolding ('What's the ...?') and worded conversions from the question bank."""

    def test_question_prefixes_and_percent_off(self):
        self.assertEqual(calc_tool.answer("What's the square root of 144?")["result"], 12)
        off = calc_tool.answer("What's 20 percent off of 150 dollars?")
        self.assertEqual((off["kind"], off["result"]), ("percent", 120))
        self.assertEqual(off["spoken"], "20 percent off 150 dollars is 120 dollars")
        self.assertEqual(calc_tool.answer("How much is 45 plus 87?")["result"], 132)

    def test_worded_conversions_and_british_spellings(self):
        self.assertAlmostEqual(calc_tool.answer("How many kilometres is 30 miles?")["result"], 48.28, places=2)
        self.assertEqual(calc_tool.answer("How many ounces are in a pound?")["result"], 16)
        self.assertEqual(calc_tool.answer("How many seconds are in a day?")["result"], 86400)
        self.assertAlmostEqual(calc_tool.answer("How many cups are in a litre?")["result"], 4.227, places=3)
        for text in ("What is the capital of France?", "How many people live in Tokyo?", "how many days until christmas"):
            self.assertIsNone(calc_tool.answer(text), text)


if __name__ == "__main__":
    unittest.main()


from calc_tool import answer


class SpellingAndExactnessTests(unittest.TestCase):
    def test_spelling_letter_by_letter(self):
        self.assertEqual(answer('Spell necessary')['spoken'], 'Necessary is spelled N, E, C, E, S, S, A, R, Y.')
        self.assertEqual(answer("How many m's are in recommend?")['spoken'], 'There are two Ms in recommend.')
        self.assertEqual(answer("how many s's are there in mississippi")['result'], 4)
        self.assertIsNone(answer('How do you spell it?'))

    def test_exact_conversions_drop_about(self):
        self.assertEqual(answer('How many ounces in a pound?')['spoken'], '1 pound is 16 ounces')
        self.assertEqual(answer('how many seconds in a day')['spoken'], '1 day is 86400 seconds')
        self.assertIn('is about 8.047', answer('What is 5 miles in kilometers?')['spoken'])
