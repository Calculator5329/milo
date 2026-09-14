import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emotion import PROMPT_HINT, TAGS, adjust, plan_samples, strip


class EmotionTests(unittest.TestCase):
    def test_strip_accepts_every_tag_in_every_marker_form(self):
        forms = ("[{tag}]", "({tag})", "*{tag}*")
        for tag in TAGS:
            for form in forms:
                with self.subTest(tag=tag, form=form):
                    marker = form.format(tag=tag)
                    self.assertEqual(strip(f"  {marker}  A short sentence."),
                                     (tag, "A short sentence."))

    def test_strip_removes_unknown_marker_words(self):
        for marker in ("[urgent]", "(urgent)", "*urgent*"):
            with self.subTest(marker=marker):
                self.assertEqual(strip(f"{marker} Move now."), (None, "Move now."))
        self.assertEqual(strip("No marker here."), (None, "No marker here."))

    def test_adjust_applies_small_deltas_without_mutating_baseline(self):
        baseline = {"pitch": 3, "rate": 1.65, "sound": "natural"}
        expected = {
            "calm": (2.0, 1.5),
            "warm": (2.5, 1.65),
            "excited": (4.5, 1.8),
            "serious": (3.0, 1.55),
            "playful": (4.25, 1.7),
            "sorry": (1.5, 1.5),
        }
        for tag, (pitch, rate) in expected.items():
            with self.subTest(tag=tag):
                changed = adjust(baseline, tag)
                self.assertEqual(changed["pitch"], pitch)
                self.assertEqual(changed["rate"], rate)
                self.assertEqual(changed["sound"], "natural")
                self.assertIsNot(changed, baseline)
        self.assertEqual(baseline, {"pitch": 3, "rate": 1.65, "sound": "natural"})

    def test_adjust_clamps_to_speech_audio_ranges(self):
        self.assertEqual(adjust({"pitch": 7.5, "rate": 1.95}, "excited"),
                         {"pitch": 8, "rate": 2})
        self.assertEqual(adjust({"pitch": -7.2, "rate": .7}, "sorry"),
                         {"pitch": -8, "rate": .65})
        untouched = {"pitch": 3, "rate": 1.65}
        self.assertEqual(adjust(untouched, None), untouched)
        self.assertIsNot(adjust(untouched, None), untouched)

    def test_prompt_hint_is_one_short_sentence(self):
        self.assertLess(len(PROMPT_HINT.encode("utf-8")), 140)
        self.assertNotIn("\n", PROMPT_HINT)
        self.assertEqual(PROMPT_HINT.count("."), 1)
        for tag in TAGS:
            self.assertIn(f"[{tag}]", PROMPT_HINT)

    def test_sample_plan_has_manifest_shape_without_tts(self):
        manifest = plan_samples()
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["voice"], "peter_yearsley")
        self.assertEqual(manifest["baseline_options"],
                         {"pitch": 3, "rate": 1.65, "sound": "natural"})
        self.assertEqual(len(manifest["samples"]), 5)
        for sample in manifest["samples"]:
            self.assertEqual(set(sample), {
                "id", "tagged_text", "untagged_text", "tags", "sentences",
                "audio", "durations",
            })
            self.assertIn(len(sample["sentences"]), (2, 3))
            self.assertEqual(len(sample["tags"]), len(sample["sentences"]))
            self.assertEqual(sample["tags"], [row["tag"] for row in sample["sentences"]])
            self.assertEqual(set(sample["audio"]), {"untagged", "tagged"})
            self.assertEqual(set(sample["durations"]), {"untagged", "tagged"})
            for variant in ("untagged", "tagged"):
                self.assertEqual(set(sample["audio"][variant]), {"wav", "ogg"})
                self.assertIsNone(sample["durations"][variant])
            for sentence in sample["sentences"]:
                self.assertEqual(set(sentence), {"text", "tag", "options", "durations"})
                self.assertIn(sentence["tag"], TAGS)
                self.assertEqual(set(sentence["options"]), {"untagged", "tagged"})
                self.assertEqual(sentence["options"]["untagged"], manifest["baseline_options"])
                self.assertEqual(set(sentence["durations"]), {"untagged", "tagged"})
                self.assertIsNone(sentence["durations"]["untagged"])
                self.assertIsNone(sentence["durations"]["tagged"])


if __name__ == "__main__":
    unittest.main()
