"""Persistent preference contract probes; retained under ~/.cache/tmp."""
import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo_documents import DocumentError
from preferences import DEFAULTS, MAX_UPDATE_ATTEMPTS, Preferences, PreferencesError


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        cache = Path.home() / ".cache" / "tmp"
        cache.mkdir(parents=True, exist_ok=True)
        # Keep probes for later inspection rather than permanently deleting them.
        self.directory = Path(tempfile.mkdtemp(prefix="milo-preferences-", dir=cache))
        self.root = self.directory / "settings"
        self.preferences = Preferences(self.root)

    def test_defaults_are_created_as_json_and_persist_across_instances(self):
        current = self.preferences.get()
        self.assertEqual({key: current[key] for key in DEFAULTS}, DEFAULTS)
        self.assertRegex(current["revision"], r"^[0-9a-f]{64}$")
        stored = json.loads((self.root / "preferences.md").read_text(encoding="utf-8"))
        self.assertEqual(stored, DEFAULTS)
        self.assertEqual(Preferences(self.root).get(), current)
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir() if not path.name.startswith(".")),
            ["preferences.md"],
        )

    def test_valid_updates_merge_normalize_and_retain_revisions(self):
        original = self.preferences.get()
        changed = self.preferences.update({
            "voice": "alba",
            "mode": "precise",
            "sound": "small-speaker",
            "rate": 1.04,
            "theme": "moonroom",
        })
        self.assertEqual(
            {key: changed[key] for key in DEFAULTS},
            {"voice": "alba", "mode": "precise", "sound": "small", "rate": 1.04,
             "theme": "moonroom", "pitch": 0, "model": "gemma4:12b"},
        )
        self.assertNotEqual(changed["revision"], original["revision"])
        self.assertEqual(Preferences(self.root).get(), changed)
        revisions = self.preferences.workspace.revisions("preferences.md")
        self.assertEqual([item["revision"] for item in revisions], [original["revision"]])
        snapshot = self.root / ".history" / "preferences.md" / original["revision"]
        self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8")), DEFAULTS)

    def test_old_preferences_gain_neutral_pitch_and_continuous_values_survive(self):
        original = self.preferences.get()
        legacy = {key: original[key] for key in DEFAULTS if key != 'pitch'}
        self.preferences.workspace.replace('preferences.md', json.dumps(legacy), original['revision'])
        self.assertEqual(self.preferences.get()['pitch'], 0)
        updated = self.preferences.update({'pitch': -2.5, 'rate': 1.17})
        self.assertEqual(updated['pitch'], -2.5)
        self.assertEqual(Preferences(self.root).get()['rate'], 1.17)

    def test_each_allowed_value_round_trips(self):
        cases = {
            "voice": ("marius", "javert", "bill_boerst", "stuart_bell", "alba"),
            "mode": ("conversational", "precise", "brainstorm"),
            "sound": ("natural", "clear", "small"),
            "rate": (0.96, 1, 1.04),
            "theme": ("workshop", "blueprint", "hifi", "library", "moonroom"),
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.preferences.update({field: value})[field], value)

    def test_unknown_and_invalid_updates_leave_storage_unchanged(self):
        original = self.preferences.get()
        invalid = (
            {"api_backend": "remote"},
            {"apiKey": "secret"},
            {"history": []},
            {"docs": ["notes.md"]},
            {"revision": original["revision"]},
            {"voice": "unknown"},
            {"mode": "creative"},
            {"sound": "loud"},
            {"rate": True},
            {"rate": 2.01},
            {"pitch": 9},
            {"theme": "system"},
            None,
        )
        for patch in invalid:
            with self.subTest(patch=patch), self.assertRaises(PreferencesError):
                self.preferences.update(patch)
            self.assertEqual(self.preferences.get(), original)
        self.assertEqual(self.preferences.workspace.revisions("preferences.md"), [])

    def test_corrupt_existing_content_is_preserved_and_reported(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "preferences.md"
        for raw in (
            b"not json\n",
            b'{"voice":"marius"}\n',
            b'{"voice":"marius","mode":"conversational","sound":"natural",'
            b'"rate":1,"theme":"workshop","api_key":"secret"}\n',
            b"\xff",
        ):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertRaises((PreferencesError, DocumentError)):
                    Preferences(self.root).get()
                self.assertEqual(path.read_bytes(), raw)

    def test_conflict_retry_merges_a_concurrent_change(self):
        self.preferences.get()
        replace = self.preferences.workspace.replace
        conflicted = False

        def replace_after_competing_write(name, content, expected_revision):
            nonlocal conflicted
            if not conflicted:
                conflicted = True
                Preferences(self.root).update({"theme": "blueprint"})
            return replace(name, content, expected_revision)

        self.preferences.workspace.replace = replace_after_competing_write
        result = self.preferences.update({"voice": "alba"})
        self.assertTrue(conflicted)
        self.assertEqual(result["voice"], "alba")
        self.assertEqual(result["theme"], "blueprint")

    def test_concurrent_distinct_updates_are_both_retained(self):
        self.preferences.get()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(Preferences(self.root).update, {"voice": "javert"}),
                pool.submit(Preferences(self.root).update, {"mode": "brainstorm"}),
            )
            for future in futures:
                future.result()
        final = self.preferences.get()
        self.assertEqual(final["voice"], "javert")
        self.assertEqual(final["mode"], "brainstorm")

    def test_conflict_retries_are_bounded(self):
        current = self.preferences.get()
        attempts = 0

        def always_conflict(*_args):
            nonlocal attempts
            attempts += 1
            raise DocumentError("Revision conflict: read the document again before editing.")

        self.preferences.workspace.replace = always_conflict
        with self.assertRaisesRegex(PreferencesError, "too many times"):
            self.preferences.update({"voice": "alba"})
        self.assertEqual(attempts, MAX_UPDATE_ATTEMPTS)
        self.assertEqual(self.preferences.get(), current)

    def test_empty_or_identical_patch_does_not_create_history(self):
        original = self.preferences.get()
        self.assertEqual(self.preferences.update({}), original)
        self.assertEqual(self.preferences.update({"voice": "marius"}), original)
        self.assertEqual(self.preferences.workspace.revisions("preferences.md"), [])


if __name__ == "__main__":
    unittest.main()
