import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conversation import _context_units  # noqa: E402
from memory import OllamaEmbedder, TokenOverlapScorer, load, recall  # noqa: E402
import memory  # noqa: E402


class FakeEmbedder(OllamaEmbedder):
    def __init__(self, cache_path, requests):
        super().__init__(cache_path=cache_path)
        self.requests = requests

    def _request_embeddings(self, texts, timeout):
        self.requests.append(list(texts))
        return [
            [float(len(text)), float(sum(map(ord, text)) % 101) + 1.0]
            for text in texts
        ]


class RecordingScorer:
    def __init__(self, scores):
        self.scores = scores
        self.query = None

    def score(self, query, lines):
        self.query = query
        return self.scores[:len(lines)]


class MemoryTests(unittest.TestCase):
    def setUp(self):
        cache_root = Path.home() / ".cache" / "tmp"
        cache_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="milo-memory-test-", dir=cache_root
        )
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_parses_markdown_sections_and_stable_keys(self):
        path = self.root / "memory.md"
        path.write_text(
            "# Curated notes\n\n"
            "## Identity\n"
            "- Preferred name is Sam.\n"
            "- Uses they pronouns.\n\n"
            "Ignored prose.\n"
            "## Projects\n"
            "- Building a voice companion.\n",
            encoding="utf-8",
        )

        first = load(path)
        second = load(path)

        self.assertEqual(
            [(line["section"], line["text"]) for line in first],
            [
                ("Identity", "Preferred name is Sam."),
                ("Identity", "Uses they pronouns."),
                ("Projects", "Building a voice companion."),
            ],
        )
        self.assertEqual(
            [line["key"] for line in first], [line["key"] for line in second]
        )
        self.assertTrue(all(len(line["key"]) == 64 for line in first))

    def test_embedding_cache_round_trip_only_embeds_new_lines(self):
        path = self.root / "memory.md"
        index = self.root / "memory-index.json"
        path.write_text(
            "## Identity\n- Preferred name is Sam.\n"
            "## Projects\n- Building a voice companion.\n",
            encoding="utf-8",
        )
        requests = []
        first_lines = load(path)
        FakeEmbedder(index, requests).score("voice project", first_lines)

        path.write_text(
            path.read_text(encoding="utf-8")
            + "- Testing bounded memory.\n",
            encoding="utf-8",
        )
        second_lines = load(path)
        FakeEmbedder(index, requests).score("memory project", second_lines)

        self.assertEqual(
            requests[0],
            [
                "voice project",
                "Preferred name is Sam.",
                "Building a voice companion.",
            ],
        )
        self.assertEqual(requests[1], ["memory project", "Testing bounded memory."])
        stored = json.loads(index.read_text(encoding="utf-8"))
        self.assertEqual(set(stored["vectors"]), {line["key"] for line in second_lines})

    def test_fallback_scores_stemmed_token_overlap(self):
        lines = [
            {"section": "Projects", "text": "Plan the project milestones.", "key": "a"},
            {"section": "Preferences", "text": "Prefers quiet audio.", "key": "b"},
        ]

        scores = TokenOverlapScorer().score("the planning projects", lines)

        self.assertGreater(scores[0], scores[1])
        self.assertEqual(scores[1], 0.0)

    def test_embedding_timeout_returns_fallback_scores_within_deadline(self):
        class SlowEmbedder(OllamaEmbedder):
            def _request_embeddings(self, texts, timeout):
                time.sleep(0.25)
                return [[1.0] for _text in texts]

        lines = [{
            "section": "Projects",
            "text": "Plan the project milestones.",
            "key": "a",
        }]
        embedder = SlowEmbedder(
            cache_path=self.root / "index.json",
            timeout=0.05,
        )

        started = time.perf_counter()
        scores = embedder.score("planning projects", lines)
        elapsed = time.perf_counter() - started

        self.assertTrue(embedder.used_fallback)
        self.assertGreater(scores[0], 0.0)
        self.assertLess(elapsed, 0.15)

    def test_template_placeholders_are_not_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.md"
            path.write_text("## Preferences\n- [Stable preference]\n- Likes short answers\n", encoding="utf-8")
            self.assertEqual([line["text"] for line in memory.load(path)], ["Likes short answers"])

    def test_recall_uses_last_three_turns_and_stays_inside_budget(self):
        path = self.root / "memory.md"
        path.write_text(
            "## Identity\n"
            "- Preferred name is Sam.\n"
            "- Time zone is Central.\n"
            "- This third identity line is optional and deliberately quite long.\n"
            "## Projects\n"
            "- This highly relevant project line is deliberately too long for the slice.\n",
            encoding="utf-8",
        )
        scorer = RecordingScorer([0.0, 0.0, 1.0, 2.0])

        result = recall(
            ["turn zero", "turn one", "turn two", "turn three"],
            budget_units=80,  # units, not bytes: room for the header and two short lines
            scorer=scorer,
            path=path,
        )

        self.assertEqual(scorer.query, "turn one\nturn two\nturn three")
        self.assertIn("- Preferred name is Sam.", result)
        self.assertIn("- Time zone is Central.", result)
        self.assertNotIn("highly relevant", result)
        self.assertLessEqual(
            _context_units([{"role": "user", "content": result}]), 80
        )

    def test_missing_or_empty_file_returns_empty_recall(self):
        missing = self.root / "missing.md"
        empty = self.root / "empty.md"
        empty.write_text("# No notes yet\n", encoding="utf-8")

        self.assertEqual(recall(["hello"], path=missing), "")
        self.assertEqual(recall(["hello"], path=empty), "")

    def test_cli_init_add_list_and_remove_with_environment_path(self):
        script = Path(__file__).resolve().parents[1] / "memory.py"
        path = self.root / "state" / "memory.md"
        env = {
            **os.environ,
            "MILO_MEMORY": str(path),
            "MILO_MEMORY_INDEX": str(self.root / "state" / "index.json"),
        }

        initialized = subprocess.run(
            [sys.executable, str(script), "init"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertIn(str(path), initialized.stdout)
        template = path.read_text(encoding="utf-8")
        self.assertIn("## Identity", template)
        self.assertIn("## Standing reminders", template)
        bullets = [line for line in template.splitlines() if line.startswith("- ")]
        self.assertTrue(bullets)
        self.assertTrue(all(line.startswith("- [") and line.endswith("]") for line in bullets))

        added = subprocess.run(
            [
                sys.executable,
                str(script),
                "add",
                "Projects",
                "Build a small voice demo.",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        key = added.stdout.strip().split()[-1]
        listed = subprocess.run(
            [sys.executable, str(script), "list"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertIn(key, listed.stdout)
        self.assertIn("Projects: Build a small voice demo.", listed.stdout)

        subprocess.run(
            [sys.executable, str(script), "remove", key],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        after = subprocess.run(
            [sys.executable, str(script), "list"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotIn(key, after.stdout)
        self.assertNotIn("Build a small voice demo.", after.stdout)


if __name__ == "__main__":
    unittest.main()
